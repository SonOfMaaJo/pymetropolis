from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from pymetropolis.metro_common import MetropyError
from pymetropolis.metro_network.road_network.files import RoadEdgesCapacitiesFile, RoadEdgesCleanFile
from pymetropolis.metro_pipeline import Step
from pymetropolis.metro_pipeline.parameters import FloatParameter, IntParameter
from pymetropolis.metro_simulation.run.files import MetroExAnteSimulatedEdgeQueueLengthsFile

from .files import TomTomRouteResultsFile, TomTomRoutesFile, TomTomTargetCapacitiesFile

if TYPE_CHECKING:
    import polars as pl


def build_signal_matrix(
    route_results: pl.DataFrame, queue_lengths: pl.DataFrame, edges: pl.DataFrame
) -> pl.DataFrame:
    """Returns one row per `tomtom_id`, with one column per edge type holding the sum, over the
    route's edges of that type, of the entry-queue-length at the time the route entered each edge.

    The queue-length file is a step function of time (one row every `recording_interval`
    seconds): the queue length "at" `entry_time` is the value of the latest recorded breakpoint
    at or before `entry_time`, found with a backward as-of join.
    """
    import polars as pl

    rr = route_results.join(
        edges.select("edge_id", "edge_type"), on="edge_id", how="inner"
    ).sort(["edge_id", "entry_time"])
    q = queue_lengths.select("edge_id", "time", "entry_queue_length").sort(["edge_id", "time"])
    matched = rr.join_asof(
        q, left_on="entry_time", right_on="time", by="edge_id", strategy="backward"
    ).with_columns(pl.col("entry_queue_length").fill_null(0.0))
    return (
        matched.group_by("tomtom_id", "edge_type")
        .agg(signal=pl.col("entry_queue_length").sum())
        .pivot(on="edge_type", index="tomtom_id", values="signal", aggregate_function="sum")
        .fill_null(0.0)
    )


def fit_capacities(
    signal_df: pl.DataFrame,
    category_columns: list[str],
    observed: pl.DataFrame,
    h_prev: dict[str, float],
    ridge_alpha: float,
) -> tuple[dict[str, float], dict[str, int], dict[str, float], float]:
    """Fits `h_j` (inverse capacity of edge type `j`) by non-negative least squares on
    `tomtom_congested_time ~= sum_j h_j * signal_j`, ridge-regularized towards `h_prev` (rather
    than towards 0, which has no physical meaning here) to tame the near-zero eigenvalues caused
    by poorly-sampled or collinear categories (see `regress_and_update_capacities.py` for the
    same issue in the previous delta-based parametrization).

    Returns `(h_hat, nb_observations, mean_nonzero_signal, rmse)`.
    """
    import numpy as np
    import polars as pl
    from scipy.optimize import nnls

    df = signal_df.with_columns(pl.col("tomtom_id").cast(pl.Int64)).join(
        observed.with_columns(pl.col("tomtom_id").cast(pl.Int64)), on="tomtom_id", how="inner"
    )
    X = df.select(category_columns).to_numpy()
    y = df["tomtom_congested_time"].to_numpy()

    nb_observations = {c: int((X[:, j] > 0).sum()) for j, c in enumerate(category_columns)}
    mean_nonzero_signal = {
        c: float(X[X[:, j] > 0, j].mean()) if nb_observations[c] > 0 else 0.0
        for j, c in enumerate(category_columns)
    }

    h_prev_arr = np.array([h_prev[c] for c in category_columns])
    n = len(category_columns)
    X_aug = np.vstack([X, np.sqrt(ridge_alpha) * np.eye(n)])
    y_aug = np.concatenate([y, np.sqrt(ridge_alpha) * h_prev_arr])
    h_hat, _ = nnls(X_aug, y_aug)

    residuals = y - X @ h_hat
    rmse = float(np.sqrt(np.mean(residuals**2)))
    return dict(zip(category_columns, h_hat.tolist())), nb_observations, mean_nonzero_signal, rmse


def apply_relaxation(
    h_hat: dict[str, float],
    h_prev: dict[str, float],
    nb_observations: dict[str, int],
    mean_nonzero_signal: dict[str, float],
    min_observations: int,
    min_signal: float,
    delta_min: float,
    delta_max: float,
    relaxation_factor: float,
) -> pl.DataFrame:
    """Applies the same two safeguards as `regress_and_update_capacities.py`, in the same order,
    but to `h_j` directly instead of to a multiplicative correction `delta_j`:

    1. Clamp the raw ratio `h_hat_j / h_prev_j` to `[delta_min, delta_max]`.
    2. Relax the clamped `h_j` towards the previous *applied* `h_j` with an EMA of weight
       `relaxation_factor`.

    A category is skipped (left at `h_prev_j`) if it has too few observations or too little mean
    nonzero signal to identify its capacity (the capacity lever has nothing to explain, not just
    noise -- see the module docstring of `regress_and_update_capacities.py`).
    """
    import polars as pl

    rows = []
    for edge_type, prev in h_prev.items():
        low_obs = nb_observations.get(edge_type, 0) < min_observations
        low_signal = mean_nonzero_signal.get(edge_type, 0.0) < min_signal
        raw_h = h_hat.get(edge_type)
        if raw_h is None or raw_h <= 0.0 or low_obs or low_signal:
            rows.append(
                {
                    "edge_type": edge_type,
                    "capacity": 3600.0 / prev,
                    "raw_ratio": float("nan"),
                    "nb_observations": nb_observations.get(edge_type, 0),
                    "mean_nonzero_signal": mean_nonzero_signal.get(edge_type, 0.0),
                    "skipped": True,
                }
            )
            continue
        raw_ratio = raw_h / prev
        clamped_h = prev * min(max(raw_ratio, delta_min), delta_max)
        applied_h = relaxation_factor * clamped_h + (1.0 - relaxation_factor) * prev
        rows.append(
            {
                "edge_type": edge_type,
                "capacity": 3600.0 / applied_h,
                "raw_ratio": raw_ratio,
                "nb_observations": nb_observations.get(edge_type, 0),
                "mean_nonzero_signal": mean_nonzero_signal.get(edge_type, 0.0),
                "skipped": False,
            }
        )
    return pl.DataFrame(rows)


class UpdateRoadCapacitiesStep(Step):
    """Fits bottleneck capacities per edge type from TomTom-observed congested times and the
    ex-ante simulation's per-edge queue lengths, under a point-queue model.

    Under Little's law, the delay entering the bottleneck of edge `i` at time `t` is
    `q_i(t) / s_i = q_i(t) * h_i`, with `q_i(t)` the simulated entry-queue length and
    `h_i = 1 / s_i` the (unknown) inverse capacity. For a TomTom route replayed on the frozen
    ex-ante network state, its congested time is approximately `sum_i q_i(t_i) * h_i` over the
    route's edges, `t_i` being the time the route enters edge `i`. `h_i` is aggregated by edge
    type (there are far more edges than map-matched TomTom routes), fitted by non-negative least
    squares (`h_j >= 0`, i.e. a finite positive capacity), then clamped and relaxed with an
    exponential moving average exactly as in the previous, travel-time-function-based calibration
    script (see `regress_and_update_capacities.py`), just reparametrized from a multiplicative
    correction to a direct fit of the physical inverse capacity.

    This Step does not write `road_network.capacities` itself: doing so would make it depend,
    transitively through the ex-ante simulation and the congestion replay, on its own output --
    a cycle that pymetropolis's dependency graph cannot resolve. Its output is meant to be merged
    into the config TOML by an external orchestrator between calibration iterations, which then
    re-invokes pymetropolis so that the change is picked up through its normal cache invalidation.
    """

    delta_min = FloatParameter(
        "capacity_calibration.delta_min",
        default=0.5,
        description="Minimum allowed ratio of the raw fitted h_j to the previous applied h_j.",
        lower_bound=1e-8,
    )
    delta_max = FloatParameter(
        "capacity_calibration.delta_max",
        default=2.0,
        description="Maximum allowed ratio of the raw fitted h_j to the previous applied h_j.",
        lower_bound=1e-8,
    )
    relaxation_factor = FloatParameter(
        "capacity_calibration.relaxation_factor",
        default=0.3,
        description=(
            "EMA weight given to the new clamped h_j: "
            "applied_h_j = factor * clamped_h_j + (1 - factor) * previous_applied_h_j."
        ),
        lower_bound=1e-8,
        upper_bound=1.0,
    )
    min_observations = IntParameter(
        "capacity_calibration.min_observations",
        default=20,
        description=(
            "Minimum number of TomTom routes with nonzero signal on an edge type for its "
            "capacity to be updated this iteration; otherwise it is left unchanged."
        ),
        lower_bound=0,
    )
    min_signal = FloatParameter(
        "capacity_calibration.min_signal",
        default=0.2,
        description=(
            "Minimum mean nonzero per-route signal (sum of entry-queue-lengths over the route's "
            "edges of that type) for an edge type to be updated this iteration; otherwise it is "
            "left unchanged."
        ),
        lower_bound=0.0,
    )
    ridge_alpha = FloatParameter(
        "capacity_calibration.ridge_alpha",
        default=100.0,
        description="Ridge penalty pulling h_j towards its previous applied value in the NNLS fit.",
        lower_bound=0.0,
    )

    input_files = {
        "routes": TomTomRoutesFile,
        "route_results": TomTomRouteResultsFile,
        "queue_lengths": MetroExAnteSimulatedEdgeQueueLengthsFile,
        "clean_edges": RoadEdgesCleanFile,
        "capacities": RoadEdgesCapacitiesFile,
    }
    output_files = {"target_capacities": TomTomTargetCapacitiesFile}

    def run(self):
        import polars as pl

        routes = self.input["routes"].read()
        observed = pl.from_pandas(
            routes.loc[:, ["tomtom_id", "tt_no_traffic", "tt_traffic"]]
        ).with_columns(
            tomtom_congested_time=(pl.col("tt_traffic") - pl.col("tt_no_traffic")).dt.total_seconds(
                fractional=True
            )
        )

        route_results = self.input["route_results"].read()
        queue_lengths = self.input["queue_lengths"].read()
        edges = self.input["clean_edges"].read_as_df().select("edge_id", "edge_type")  # ty: ignore[unresolved-attribute]

        category_columns = sorted(edges["edge_type"].unique().to_list())

        signal_df = build_signal_matrix(route_results, queue_lengths, edges)
        for c in category_columns:
            if c not in signal_df.columns:
                # No replayed route ever entered an edge of this type: no signal at all.
                signal_df = signal_df.with_columns(pl.lit(0.0).alias(c))

        h_prev = self.load_previous_h(category_columns, edges)

        h_hat, nb_observations, mean_nonzero_signal, rmse = fit_capacities(
            signal_df, category_columns, observed, h_prev, self.ridge_alpha
        )
        logger.info(f"Capacity-update fit RMSE: {rmse:.2f}s over {len(signal_df)} routes")

        df = apply_relaxation(
            h_hat,
            h_prev,
            nb_observations,
            mean_nonzero_signal,
            self.min_observations,
            self.min_signal,
            self.delta_min,
            self.delta_max,
            self.relaxation_factor,
        )
        for row in df.sort("edge_type").iter_rows(named=True):
            flag = " (skipped)" if row["skipped"] else ""
            logger.info(
                f"  {row['edge_type']:16s} n={row['nb_observations']:5d}  "
                f"mean_signal={row['mean_nonzero_signal']:.4f}  "
                f"capacity={row['capacity']:.1f}{flag}"
            )

        self.output["target_capacities"].write(df)

    def load_previous_h(self, category_columns: list[str], edges: pl.DataFrame) -> dict[str, float]:
        """Returns `{edge_type: h_prev}`, the EMA anchor for this iteration.

        Uses this Step's own previous output if it exists (the applied `h` from the last
        calibration iteration); otherwise falls back to the current `road_network.capacities`,
        resolved per edge and averaged by edge type (the production baseline).
        """
        import polars as pl

        target_file = self.output["target_capacities"]
        h_prev = (
            {row["edge_type"]: 3600.0 / row["capacity"] for row in target_file.read().iter_rows(named=True)}
            if target_file.exists()
            else {}
        )
        missing = [c for c in category_columns if c not in h_prev]
        if missing:
            capacities = self.input["capacities"].read().join(edges, on="edge_id", how="inner")
            baseline = (
                capacities.filter(pl.col("capacity").is_not_null())
                .group_by("edge_type")
                .agg(capacity=pl.col("capacity").mean())
            )
            baseline_dict = dict(zip(baseline["edge_type"], baseline["capacity"]))
            for c in missing:
                if c not in baseline_dict:
                    raise MetropyError(
                        f"No previous target capacity and no scalar baseline capacity for edge "
                        f"type `{c}`: cannot seed the EMA anchor (time-dependent capacities are "
                        "not supported by this Step)."
                    )
                h_prev[c] = 3600.0 / baseline_dict[c]
        return h_prev
