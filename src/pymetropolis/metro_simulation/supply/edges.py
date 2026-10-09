from typing import TYPE_CHECKING, Any

from pymetropolis.metro_common.errors import MetropyError
from pymetropolis.metro_common.utils import pl_duration_to_seconds
from pymetropolis.metro_network.road_network import RoadEdgesCapacitiesFile, RoadEdgesCleanFile
from pymetropolis.metro_network.road_network.files import (
    RoadEdgesPenaltiesFile,
    RoadEdgesPrimaryFlagFile,
)
from pymetropolis.metro_pipeline.parameters import CustomParameter
from pymetropolis.metro_pipeline.steps import InputFile, Step

from .files import MetroEdgesFile

if TYPE_CHECKING:
    import geopandas as gpd
    import polars as pl

# Parameters allowed for each type of speed-density function of Metropolis-Core.
SPEED_DENSITY_KEYS = {
    "FreeFlow": set(),
    "Bottleneck": {"capacity"},
    "ThreeRegimes": {"min_density", "jam_density", "jam_speed", "jam_speed_ratio", "beta"},
}
SPEED_DENSITY_COLUMNS = ("capacity", "min_density", "jam_density", "jam_speed", "beta")


def validate_speed_density_function(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("type") not in SPEED_DENSITY_KEYS:
        raise MetropyError(
            "Invalid speed-density function (table with `type` FreeFlow, Bottleneck or "
            f"ThreeRegimes expected): `{value}`"
        )
    allowed = SPEED_DENSITY_KEYS[value["type"]]
    unknown = set(value.keys()) - allowed - {"type"}
    if unknown:
        raise MetropyError(f"Unknown keys for a {value['type']} speed-density function: {unknown}")
    for k in allowed - {"jam_speed", "jam_speed_ratio"}:
        if not isinstance(value.get(k), int | float) or value[k] < 0:
            raise MetropyError(f"Value `{k}` must be a non-negative number: `{value}`")
    if value["type"] == "ThreeRegimes":
        if ("jam_speed" in value) == ("jam_speed_ratio" in value):
            raise MetropyError(
                f"Exactly one of `jam_speed` and `jam_speed_ratio` expected: `{value}`"
            )
        if value["jam_density"] < value["min_density"]:
            raise MetropyError(f"Value `jam_density` is smaller than `min_density`: `{value}`")
        if not 0.0 <= value.get("jam_speed_ratio", 0.0) <= 1.0:
            raise MetropyError(f"Value `jam_speed_ratio` must be between 0 and 1: `{value}`")
    return {k: float(v) if k != "type" else v for k, v in value.items()}


def speed_density_validator(value: Any) -> dict:
    """Returns a map edge_type -> speed-density function ("default": other edge types)."""
    if isinstance(value, dict) and "type" in value:
        return {"default": validate_speed_density_function(value)}
    if isinstance(value, dict):
        return {k: validate_speed_density_function(v) for k, v in value.items()}
    raise MetropyError(f"Invalid speed-density functions (table expected): `{value}`")


def add_speed_density_columns(df: "pl.DataFrame", functions: dict) -> "pl.DataFrame":
    """Adds the `speed_density.*` columns from the edge-type-specific functions.

    `df` must have the columns `edge_type`, `length` and `speed` (the free-flow speed, in m/s,
    used for `jam_speed_ratio`). Edges with a null length (dummy edges) are always in free flow.
    """
    import polars as pl

    default = functions.get("default", {"type": "FreeFlow"})
    by_type = {k: v for k, v in functions.items() if k != "default"}

    def mapped(key: str, dtype) -> "pl.Expr":
        return pl.col("edge_type").replace_strict(
            {t: f.get(key) for t, f in by_type.items()},
            default=pl.lit(default.get(key), dtype=dtype),
            return_dtype=dtype,
        )

    df = df.with_columns(
        pl.when(pl.col("length") > 0)
        .then(mapped("type", pl.String))
        .otherwise(pl.lit("FreeFlow"))
        .alias("speed_density.type"),
        *(mapped(k, pl.Float64).alias(f"speed_density.{k}") for k in SPEED_DENSITY_COLUMNS),
    )
    # The jam speed can be defined relative to the edge's free-flow speed.
    return df.with_columns(
        pl.coalesce(
            mapped("jam_speed_ratio", pl.Float64) * pl.col("speed"), "speed_density.jam_speed"
        ).alias("speed_density.jam_speed")
    )


class WriteMetroEdgesStep(Step):
    """Generates the input edges file for the Metropolis-Core simulation."""

    speed_density = CustomParameter(
        "road_network.speed_density",
        validator=speed_density_validator,
        validator_description=(
            "table defining one speed-density function, or table with edge types (or `default`) "
            "as keys and speed-density functions as values"
        ),
        description=(
            "Speed-density function of the running part of the edges (default: free flow, "
            "congestion only arises from the bottlenecks). For `ThreeRegimes`, the speed is "
            "`v0 (1 - c) + jam_speed c`, with `c = ((d - min_density) / (jam_density - "
            "min_density))^beta` and `d` the density: total headway of the vehicles on the edge "
            "divided by length times lanes (the headway of a simulated vehicle is divided by "
            "`simulation_ratio`). `jam_speed` (m/s) can be replaced by `jam_speed_ratio`, a share "
            "of the edge's free-flow speed. Dummy edges (null length) always stay in free flow."
        ),
        example="""
```toml
[road_network.speed_density.default]
type = "ThreeRegimes"
min_density = 0.0
jam_density = 0.2
jam_speed_ratio = 0.3
beta = 1.0
[road_network.speed_density.motorway]
type = "FreeFlow"
```
        """,
    )
    input_files = {
        "clean_edges": RoadEdgesCleanFile,
        "capacities": InputFile(RoadEdgesCapacitiesFile, optional=True),
        "penalties": InputFile(RoadEdgesPenaltiesFile, optional=True),
        "primary_flags": InputFile(RoadEdgesPrimaryFlagFile, optional=True),
    }
    output_files = {"metro_edges": MetroEdgesFile}

    def run(self):
        import polars as pl

        edges: gpd.GeoDataFrame = self.input["clean_edges"].read()
        columns = ["edge_id", "source", "target", "length", "speed_limit", "lanes", "hov_lanes"]
        if self.speed_density is not None:
            columns.append("edge_type")
        df = pl.from_pandas(edges.loc[:, columns])
        # Cast all ids to String. This prevents issues when the ids are integer but are cast to
        # String to handle parallel edges or HOV edges (since origin / destination ids also need to
        # be cast to String in this case).
        df = df.with_columns(
            pl.col("edge_id").cast(pl.String),
            pl.col("source").cast(pl.String),
            pl.col("target").cast(pl.String),
        )
        if self.input["primary_flags"].exists():
            primary_flags = self.input["primary_flags"].read()
            df = df.join(
                primary_flags.filter("primary"), on=pl.col("edge_id").cast(pl.String), how="semi"
            )
        df = df.with_columns(original_id=pl.col("edge_id"))
        st_counts = df.group_by("source", "target").len()
        if st_counts["len"].max() > 1:
            # Add dummy nodes and edges to prevent parallel edges.
            # If there are two edges, 1 and 2, from node 10 to node 11:
            # - Set their target node to "10-11-dummy-0" and "10-11-dummy-1", respectively.
            # - Add two edges with id "1-dummy" and "2-dummy", with source "10-11-dummy-0" and
            #   "10-11-dumy-1", with target "11" and "11", and with length 0.
            parallel_edges = (
                df.join(st_counts.filter(pl.col("len") > 1), on=["source", "target"], how="semi")
                .with_columns(st_idx=pl.int_range(pl.len()).over("source", "target"))
                .with_columns(
                    dummy_node=pl.concat_str(
                        "source", pl.lit("-"), "target", pl.lit("-dummy-"), "st_idx"
                    )
                )
                .drop("st_idx")
            )
            dummy_edges = parallel_edges.with_columns(
                edge_id=pl.concat_str(pl.col("edge_id"), pl.lit("-dummy")),
                # Set original_id to None so that no bottleneck capacity will be attached to this
                # edge.
                original_id=None,
                source="dummy_node",
                length=0.0,
            ).drop("dummy_node")
            df = pl.concat(
                (
                    # Edges which are NOT parallel.
                    df.join(parallel_edges, on="edge_id", how="anti"),
                    # Parallel edges (with target node modified).
                    parallel_edges.with_columns(target="dummy_node").drop("dummy_node"),
                    # Dummy edges to connect modified target node to actual target node.
                    dummy_edges,
                )
            )
        if df["hov_lanes"].max() > 0.0:
            # Add "-hov" to the id of each hov edge.
            hov_edges = df.filter(pl.col("hov_lanes") > 0).with_columns(
                edge_id=pl.concat_str("edge_id", pl.lit("-hov")),
                target=pl.concat_str("source", pl.lit("-"), "target", pl.lit("-dummy-hov")),
                lanes="hov_lanes",
            )
            # Create dummy edges to prevent the parallel edges problem.
            dummy_edges = df.filter(pl.col("hov_lanes") > 0).with_columns(
                edge_id=pl.concat_str(pl.col("edge_id"), pl.lit("-dummy-hov")),
                # Set original_id to None so that no bottleneck capacity will be attached to this
                # edge.
                original_id=None,
                source=pl.concat_str("source", pl.lit("-"), "target", pl.lit("-dummy-hov")),
                length=0.0,
            )
            # Remove HOV lanes from the actual number of lanes.
            # And filter out hov-only edges.
            df = df.filter(pl.col("lanes") > 0).with_columns(
                lanes=pl.col("lanes") - pl.col("hov_lanes")
            )
            df = pl.concat((df, hov_edges, dummy_edges))
        df = df.select(
            "edge_id",
            "original_id",
            "source",
            "target",
            "length",
            "lanes",
            speed=pl.col("speed_limit") / 3.6,
            overtaking=pl.lit(True),
            *(["edge_type"] if self.speed_density is not None else []),
        )
        if self.input["capacities"].exists():
            capacities: pl.DataFrame = (
                self.input["capacities"]
                .read()
                .with_columns(original_id=pl.col("edge_id").cast(pl.String))
                .drop("edge_id")
            )
            # The join is done on the `original_id` column so that both normal edges and HOV edges
            # are attached the correct capacity.
            df = (
                df.join(capacities, on="original_id", how="left")
                .with_columns(bottleneck_flow=pl.col("capacity") / 3600.0)
                .drop("capacity")
            )
            if "capacities" in capacities.columns and "times" in capacities.columns:
                df = df.with_columns(
                    bottleneck_flows=pl.col("capacities").list.eval(pl.element() / 3600.0),
                    bottleneck_times=pl.col("times").list.eval(
                        pl_duration_to_seconds(pl.element())
                    ),
                ).drop("capacities", "times")
        if self.input["penalties"].exists():
            penalties: pl.DataFrame = self.input["penalties"].read()
            df = (
                df.join(
                    penalties.select(
                        "constant",
                        "speed_multiplier",
                        original_id=pl.col("edge_id").cast(pl.String),
                    ),
                    on="original_id",
                    how="left",
                )
                .rename({"constant": "constant_travel_time"})
                .with_columns(speed=pl.col("speed") * pl.col("speed_multiplier").fill_null(1.0))
                .drop("speed_multiplier")
            )
        if self.speed_density is not None:
            # Done after the penalties so that `jam_speed_ratio` applies to the actual free-flow
            # speed.
            df = add_speed_density_columns(df, self.speed_density).drop("edge_type")
        df = df.drop("original_id").sort("source", "target")
        self.output["metro_edges"].write(df)

    def config_hash(self) -> str:
        # `road_network.speed_density` was added after the other parameters: it is left out of
        # the hash when not set, so that existing runs are not considered outdated.
        if self.speed_density is None:
            saved = self._config_dict.pop("speed_density", None)
            try:
                return super().config_hash()
            finally:
                self._config_dict["speed_density"] = saved
        return super().config_hash()
