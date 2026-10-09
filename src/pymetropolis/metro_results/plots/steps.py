from loguru import logger

from pymetropolis.metro_common.errors import MetropyError
from pymetropolis.metro_common.utils import (
    pl_duration_to_seconds,
    seconds_since_midnight_to_time_string,
    seconds_to_duration_string,
)
from pymetropolis.metro_demand.zones.file import ZonesLevel2File, ZonesLevel3File
from pymetropolis.metro_network.public_transit import GTFSStep
from pymetropolis.metro_network.road_network.files import (
    RoadEdgesCapacitiesFile,
    RoadEdgesCleanFile,
    RoadEdgesFreeFlowTravelTimeFile,
)
from pymetropolis.metro_pipeline import PopulationStep, Step
from pymetropolis.metro_pipeline.parameters import ListParameter
from pymetropolis.metro_pipeline.steps import InputFile
from pymetropolis.metro_pipeline.types import Time
from pymetropolis.metro_results.aggregate import IterationResultsFile
from pymetropolis.metro_results.demand import TripResultsFile
from pymetropolis.metro_simulation.run import (
    MetroExpectedTravelTimeFunctionsFile,
    MetroRouteResultsFile,
    MetroSimulatedTravelTimeFunctionsFile,
)
from pymetropolis.metro_spatial import GeoStep
from pymetropolis.metro_spatial.simulation_area.file import SimulationAreaFile

from .files import (
    ExpectedRoadNetworkCongestionFunctionPlotFile,
    ExpectedRoadTravelTimesConvergencePlotFile,
    MeanSurplusConvergencePlotFile,
    PublicTransitNetworkPlotFile,
    RoadNetworkCapacityPlotFile,
    RoadNetworkFlowPlotFile,
    RouteLengthDiffConvergencePlotFile,
    SimulatedRoadTravelTimesConvergencePlotFile,
    SimulationRoadNetworkCongestionFunctionPlotFile,
    StudyAreaPlotFile,
    TourDepartureTimeConvergencePlotFile,
    TripDepartureTimeDistributionPlotFile,
    TripModeSharesPlotFile,
)

# Define a color palette from Okabe and Ito.
ORANGE = "#E69F00"
LIGHTBLUE = "#56B4E9"
GREEN = "#009E73"
YELLOW = "#F0E442"
BLUE = "#0072B2"
RED = "#D55E00"
PINK = "#CC79A7"
BLACK = "#000000"
COLORS = [ORANGE, LIGHTBLUE, GREEN, BLUE, RED, PINK, YELLOW, BLACK]

PURPLE = "#9932CC"
TEAL = "#008080"


class ConvergencePlotStep(Step):
    """Generates various graphs to analyze the convergence of the simulation."""

    input_files = {"iteration_results": IterationResultsFile}
    output_files = {
        "tour_departure_time": TourDepartureTimeConvergencePlotFile,
        "simulated_travel_time": SimulatedRoadTravelTimesConvergencePlotFile,
        "expected_travel_time": ExpectedRoadTravelTimesConvergencePlotFile,
        "route_length_diff": RouteLengthDiffConvergencePlotFile,
        "surplus": MeanSurplusConvergencePlotFile,
    }

    def run(self):
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter, PercentFormatter

        # TODO. What if the values are all null? (e.g., no trip simulation).
        # TODO. Add configuration for log vs linear scale (and labels?).
        df = self.input["iteration_results"].read()
        # Remove first iteration (no value).
        df = df[1:]
        xs = df["iteration"]
        # Plot graphs for the duration variables.
        for col, ofile, label in (
            ("rmse_tour_departure_time", "tour_departure_time", "Departure time RMSE"),
            (
                "rmse_simulated_road_travel_times",
                "simulated_travel_time",
                "Simulated edge-level travel times RMSE",
            ),
            (
                "rmse_expected_road_travel_times",
                "expected_travel_time",
                "Expected edge-level travel times RMSE",
            ),
        ):
            fig, ax = plt.subplots()
            ys = df[col].dt.total_nanoseconds() / 1e9
            ax.semilogy(xs, ys, alpha=0.9)
            ax.set_xlabel("Iteration")
            ax.set_ylabel(f"{label} (log scale)")
            ax.set_xlim(xs.min(), xs.max())
            # ax.set_ylim(bottom=0)
            ax.yaxis.set_major_formatter(
                FuncFormatter(lambda x, pos: seconds_to_duration_string(x))
            )
            ax.grid()
            fig.tight_layout()
            self.output[ofile].write(fig)
        # Plot graphs for the float variables.
        for col, ofile, label, bottom_to_zero, percent_format in (
            (
                "mean_road_trip_length_diff",
                "route_length_diff",
                "Route length difference (m)",
                True,
                False,
            ),
            ("mean_surplus", "surplus", "Mean surplus (€)", False, False),
        ):
            fig, ax = plt.subplots()
            ys = df[col]
            ax.plot(xs, ys, alpha=0.9)
            ax.set_xlabel("Iteration")
            ax.set_ylabel(label)
            ax.set_xlim(xs.min(), xs.max())
            if bottom_to_zero:
                ax.set_ylim(bottom=0)
            if percent_format:
                ax.yaxis.set_major_formatter(PercentFormatter(xmax=1))
            ax.grid()
            fig.tight_layout()
            self.output[ofile].write(fig)


class TripDepartureTimeDistributionStep(PopulationStep):
    """Generates a histogram of departure-time distribution at the trip level."""

    input_files = {"trip_results": TripResultsFile}
    output_files = {"trip_departure_time_distribution_plot": TripDepartureTimeDistributionPlotFile}

    def run(self):
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter

        # TODO. Add configuration for number of bins (and maybe axis labels?)
        df = self.input["trip_results"].read()
        fig, ax = plt.subplots()
        values = df.select(pl_duration_to_seconds("departure_time")).to_series()
        ax.hist(values, bins=60, density=True, alpha=0.9, histtype="step")
        ax.set_xlabel("Departure time")
        ax.set_ylabel("Density")
        ax.set_xlim(values.min(), values.max())
        ax.set_ylim(bottom=0)
        ax.xaxis.set_major_formatter(
            FuncFormatter(lambda x, pos: seconds_since_midnight_to_time_string(x))
        )
        ax.grid()
        fig.tight_layout()
        self.output["trip_departure_time_distribution_plot"].write(fig)


class RoadNetworkCongestionFunctionPlotsStep(Step):
    """Generates plots of expected and simulated congestion function global over the entire road
    network.
    """

    input_files = {
        "edges_fftt": RoadEdgesFreeFlowTravelTimeFile,
        "sim_ttfs": MetroSimulatedTravelTimeFunctionsFile,
        "exp_ttfs": MetroExpectedTravelTimeFunctionsFile,
    }
    output_files = {
        "sim_plot": SimulationRoadNetworkCongestionFunctionPlotFile,
        "exp_plot": ExpectedRoadNetworkCongestionFunctionPlotFile,
    }

    def run(self):
        import matplotlib.pyplot as plt
        import polars as pl
        from matplotlib.ticker import FuncFormatter, PercentFormatter

        edges_fftt = self.input["edges_fftt"].read()
        for x, label in (("sim", "Simulated"), ("exp", "Expected")):
            df = self.input[f"{x}_ttfs"].read()
            fig, ax = plt.subplots()
            # TODO. Which vehicle type to select?
            # For now, we take car driver alone.
            df = df.filter(vehicle_id="car_driver_alone")
            # Compute total free-flow travel time ON THE PRIMARY EDGES ONLY.
            tot_fftt = (
                edges_fftt.join(df, on=pl.col("edge_id").cast(pl.String), how="semi")[
                    "free_flow_travel_time"
                ]
                .sum()
                .total_seconds()
            )
            df = (
                df.group_by("departure_time")
                .agg(cong=pl.col("travel_time").sum() / tot_fftt - 1)
                .sort("departure_time")
            )
            ax.plot(df["departure_time"], df["cong"], alpha=0.9)
            ax.set_xlabel("Departure time")
            ax.set_ylabel(f"{label} road-network congestion")
            ax.set_xlim(df["departure_time"].min(), df["departure_time"].max())
            ax.set_ylim(bottom=0)
            ax.xaxis.set_major_formatter(
                FuncFormatter(lambda x, pos: seconds_since_midnight_to_time_string(x))
            )
            ax.yaxis.set_major_formatter(PercentFormatter(xmax=1))
            ax.grid()
            fig.tight_layout()
            self.output[f"{x}_plot"].write(fig)


class TripModeSharesStep(PopulationStep):
    """Generates a plot of mode shares at the trip level."""

    input_files = {"trip_results": TripResultsFile}
    output_files = {"plot": TripModeSharesPlotFile}

    def run(self):
        import matplotlib.pyplot as plt
        from matplotlib.ticker import PercentFormatter

        df = self.input["trip_results"].read()
        shares = df["mode"].value_counts(normalize=True, sort=True)
        fig, ax = plt.subplots()
        bars = ax.barh(
            y=shares["mode"],
            width=shares["proportion"],
            height=0.9,
            align="center",
            color=COLORS,
            zorder=1,
        )
        ax.bar_label(bars, fmt="{:.0%}", padding=5, zorder=3)
        ax.xaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
        ax.set_xlim(left=0)
        ax.tick_params(axis="y", which="both", length=0)
        ax.set_xlabel("Share")
        ax.grid(which="major", axis="x", zorder=2)
        fig.tight_layout(pad=0.5)
        self.output["plot"].write(fig)


class StudyAreaPlotStep(GeoStep):
    """Plots a map of the study area: level-3 zones (communes), highlighting those within the
    simulation area. Level-2 zone (department) boundaries and name labels are overlaid when
    `zones.custom_files.level2` (or another level-2 zones source) is configured.
    """

    input_files = {
        "zones3": ZonesLevel3File,
        "zones2": InputFile(ZonesLevel2File, optional=True),
        "area": SimulationAreaFile,
    }
    output_files = {"plot": StudyAreaPlotFile}

    def run(self):
        import geopandas as gpd
        import matplotlib.lines as mlines
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt

        zones3 = self.input["zones3"].read().to_crs(self.crs)
        area = self.input["area"].get_area(crs=self.crs)

        fig, ax = plt.subplots(figsize=(10, 10))
        if "within_area" in zones3.columns:
            within = zones3[zones3["within_area"].fillna(False)]
            outside = zones3[~zones3["within_area"].fillna(False)]
        else:
            within = zones3
            outside = zones3.iloc[0:0]
        legend_handles = list()
        if len(outside):
            outside.plot(ax=ax, facecolor="#f0f0f0", edgecolor="#bbbbbb", linewidth=0.3, zorder=1)
            legend_handles.append(
                mpatches.Patch(
                    facecolor="#f0f0f0", edgecolor="#bbbbbb", label="Neighboring communes"
                )
            )
        within.plot(
            ax=ax, facecolor=LIGHTBLUE, edgecolor="white", linewidth=0.3, alpha=0.7, zorder=2
        )
        legend_handles.append(
            mpatches.Patch(
                facecolor=LIGHTBLUE,
                edgecolor="white",
                label=f"Communes in study area ({len(within)})",
            )
        )
        gpd.GeoSeries([area], crs=self.crs).boundary.plot(ax=ax, color=RED, linewidth=1.5, zorder=4)
        legend_handles.append(
            mlines.Line2D([], [], color=RED, linewidth=1.5, label="Simulation area boundary")
        )

        zones2 = self.input["zones2"].read_if_exists()
        if zones2 is not None:
            zones2 = zones2.to_crs(self.crs)
            zones2.boundary.plot(ax=ax, color=BLACK, linewidth=1.0, zorder=3)
            for _, row in zones2.iterrows():
                name = row.get("name")
                if row.geometry is not None and name:
                    centroid = row.geometry.representative_point()
                    ax.annotate(
                        name,
                        (centroid.x, centroid.y),
                        ha="center",
                        fontsize=8,
                        weight="bold",
                        zorder=5,
                    )
            legend_handles.append(
                mlines.Line2D([], [], color=BLACK, linewidth=1.0, label="Department boundary")
            )

        ax.set_axis_off()
        ax.set_aspect("equal")
        ax.legend(handles=legend_handles, loc="lower left", fontsize=8, framealpha=0.9)
        fig.tight_layout()
        self.output["plot"].write(fig)


class RoadNetworkCapacityPlotStep(GeoStep):
    """Plots a map of the road network, colored by edge type, to visualize how bottleneck
    capacities (configured per edge type, see `road_network.capacities`) are assigned across the
    network.
    """

    input_files = {"edges": RoadEdgesCleanFile, "capacities": RoadEdgesCapacitiesFile}
    output_files = {"plot": RoadNetworkCapacityPlotFile}

    def run(self):
        import matplotlib.pyplot as plt

        edges = self.input["edges"].read().to_crs(self.crs)
        capacities = self.input["capacities"].read().to_pandas()
        edges = edges.merge(capacities[["edge_id", "capacity"]], on="edge_id", how="inner")
        edges = edges[edges["capacity"].notna()]

        edge_types = sorted(edges["edge_type"].dropna().unique())
        # `edge_type` can have more categories than the Okabe-Ito palette has colors for, so a
        # larger qualitative colormap is used instead.
        cmap = plt.get_cmap("tab20", len(edge_types))
        fig, ax = plt.subplots(figsize=(10, 10))
        for i, edge_type in enumerate(edge_types):
            subset = edges[edges["edge_type"] == edge_type]
            subset.plot(ax=ax, color=cmap(i), linewidth=0.6, label=edge_type, zorder=2)
        ax.set_axis_off()
        ax.set_aspect("equal")
        ax.legend(loc="lower left", fontsize=8, title="Edge type", framealpha=0.9)
        fig.tight_layout()
        self.output["plot"].write(fig)


class RoadNetworkFlowPlotStep(GeoStep):
    """Plots a map of the road network, with edges colored and sized by the simulated number of
    vehicles that traversed them (from `route_results`, i.e., the last run's actual vehicle
    trajectories).
    """

    time_window = ListParameter(
        "plots.road_network_flow.time_window",
        inner=Time(),
        length=2,
        description=(
            "Time window used to restrict the vehicle counts to entries within that window. "
            "If not specified, all vehicle entries over the whole simulated period are counted."
        ),
        example="`[06:00:00, 09:00:00]`",
    )
    input_files = {"edges": RoadEdgesCleanFile, "route_results": MetroRouteResultsFile}
    output_files = {"plot": RoadNetworkFlowPlotFile}

    def run(self):
        import matplotlib.pyplot as plt
        import polars as pl

        edges = self.input["edges"].read().to_crs(self.crs)
        route_results = self.input["route_results"].read()
        if self.time_window is not None:
            route_results = route_results.filter(
                (pl.col("entry_time") >= self.time_window[0].seconds())
                & (pl.col("entry_time") <= self.time_window[1].seconds())
            )
        flow = route_results.group_by("edge_id").agg(flow=pl.len()).to_pandas()
        edges = edges.merge(flow, on="edge_id", how="left")
        edges["flow"] = edges["flow"].fillna(0)
        # Draw high-flow edges last so that they are not hidden below low-flow edges.
        edges = edges.sort_values("flow")

        vmax = max(edges["flow"].quantile(0.98), 1)
        widths = 0.3 + 2.0 * edges["flow"].clip(upper=vmax) / vmax
        fig, ax = plt.subplots(figsize=(10, 10))
        edges.plot(
            ax=ax,
            column="flow",
            cmap="viridis",
            linewidth=widths,
            vmin=0,
            vmax=vmax,
            legend=True,
            legend_kwds={"label": "Number of vehicles", "shrink": 0.6},
            zorder=2,
        )
        ax.set_axis_off()
        ax.set_aspect("equal")
        fig.tight_layout()
        self.output["plot"].write(fig)


# GTFS `route_type` codes come in two schemes: the basic codes (0-12) and the "extended" codes
# (three-digit, e.g. 101, 700, 1501) used by many European feeds. Both are bucketed into the same
# small set of transit-mode categories, each mapped to a label and a color from the Okabe-Ito
# palette, used by `PublicTransitNetworkPlotStep`.
GTFS_CATEGORY_LABELS = {
    "tram": "Tram",
    "subway": "Subway/Metro",
    "rail": "Rail",
    "bus": "Bus/Coach",
    "ferry": "Ferry",
    "cable_tram": "Cable tram",
    "aerial_lift": "Aerial lift",
    "funicular": "Funicular",
    "trolleybus": "Trolleybus",
    "monorail": "Monorail",
    "taxi": "Taxi",
    "other": "Other",
}
GTFS_CATEGORY_COLORS = {
    "tram": ORANGE,
    "subway": PURPLE,
    "rail": RED,
    "bus": LIGHTBLUE,
    "ferry": TEAL,
    "cable_tram": GREEN,
    "aerial_lift": PINK,
    "funicular": YELLOW,
    "trolleybus": GREEN,
    "monorail": PINK,
    "taxi": "#888888",
    "other": BLACK,
}


def _gtfs_route_type_category(route_type):
    """Buckets a GTFS `route_type` code, basic (0-12) or extended (100+), into a transit-mode
    category key of `GTFS_CATEGORY_LABELS` / `GTFS_CATEGORY_COLORS`.
    """
    if route_type == 0 or 900 <= route_type < 1000:
        return "tram"
    if route_type == 1 or 400 <= route_type < 500:
        return "subway"
    if route_type == 2 or 100 <= route_type < 200:
        return "rail"
    if route_type == 3 or 200 <= route_type < 300 or 700 <= route_type < 800:
        return "bus"
    if route_type == 4 or 1000 <= route_type < 1200:
        return "ferry"
    if route_type == 5:
        return "cable_tram"
    if route_type == 6 or 1300 <= route_type < 1400:
        return "aerial_lift"
    if route_type == 7 or 1400 <= route_type < 1500:
        return "funicular"
    if route_type == 11 or 800 <= route_type < 900:
        return "trolleybus"
    if route_type == 12:
        return "monorail"
    if 1500 <= route_type < 1600:
        return "taxi"
    return "other"


def _gtfs_active_service_ids(calendar, calendar_dates, target_date):
    """Returns the set of `service_id`s active on `target_date`, from a feed's (optional)
    `calendar.txt` and `calendar_dates.txt` tables.
    """
    import polars as pl

    weekday_col = target_date.strftime("%A").lower()
    active = set()
    if calendar is not None and weekday_col in calendar.columns:
        cal = calendar.with_columns(
            pl.col("start_date").cast(pl.String).str.strptime(pl.Date, "%Y%m%d"),
            pl.col("end_date").cast(pl.String).str.strptime(pl.Date, "%Y%m%d"),
        ).filter(
            (pl.col("start_date") <= target_date)
            & (pl.col("end_date") >= target_date)
            & (pl.col(weekday_col) == 1)
        )
        active |= set(cal["service_id"].to_list())
    if calendar_dates is not None:
        cd = calendar_dates.with_columns(
            pl.col("date").cast(pl.String).str.strptime(pl.Date, "%Y%m%d")
        )
        added = cd.filter((pl.col("date") == target_date) & (pl.col("exception_type") == 1))
        removed = cd.filter((pl.col("date") == target_date) & (pl.col("exception_type") == 2))
        active |= set(added["service_id"].to_list())
        active -= set(removed["service_id"].to_list())
    return active


def _gtfs_feed_lines(gtfs_path, target_date):
    """Reads a GTFS zip file and returns a list of `(LineString, route_type)` for the routes
    active on `target_date`.

    Route paths are taken from `shapes.txt` when the feed provides it. Otherwise, since plotting
    every trip from the (potentially huge) `stop_times.txt` is far too expensive for a network
    overview, each route is approximated by a single representative trip's stop-to-stop path.
    """
    import zipfile

    import polars as pl
    from shapely.geometry import LineString

    with zipfile.ZipFile(gtfs_path) as z:
        names = set(z.namelist())

        def read(name, **kwargs):
            if name not in names:
                return None
            with z.open(name) as f:
                return pl.read_csv(f.read(), infer_schema_length=10000, **kwargs)

        routes = read("routes.txt")
        trips = read("trips.txt")
        calendar = read("calendar.txt")
        calendar_dates = read("calendar_dates.txt")
        if routes is None or trips is None:
            return []

        active_services = _gtfs_active_service_ids(calendar, calendar_dates, target_date)
        trips = trips.filter(pl.col("service_id").is_in(active_services))
        trip_routes = trips.join(
            routes.select("route_id", "route_type"), on="route_id", how="inner"
        )

        lines = list()
        shapes = read("shapes.txt")
        if shapes is not None and "shape_id" in trip_routes.columns:
            shape_routes = (
                trip_routes.filter(pl.col("shape_id").is_not_null())
                .unique(subset=["shape_id"], keep="first")
                .select("shape_id", "route_type")
            )
            shapes = shapes.join(shape_routes, on="shape_id", how="inner").sort(
                "shape_id", "shape_pt_sequence"
            )
            for (_shape_id, route_type), group in shapes.group_by(["shape_id", "route_type"]):
                coords = list(zip(group["shape_pt_lon"], group["shape_pt_lat"], strict=True))
                if len(coords) >= 2:
                    lines.append((LineString(coords), route_type))
            return lines

        stop_times = read("stop_times.txt")
        stops = read("stops.txt")
        if stop_times is None or stops is None:
            return []
        representative_trips = trip_routes.unique(subset=["route_id"], keep="first").select(
            "trip_id", "route_type"
        )
        stop_times = stop_times.join(representative_trips, on="trip_id", how="inner").join(
            stops.select("stop_id", "stop_lat", "stop_lon"), on="stop_id", how="inner"
        )
        stop_times = stop_times.sort("trip_id", "stop_sequence")
        for (_trip_id, route_type), group in stop_times.group_by(["trip_id", "route_type"]):
            coords = list(zip(group["stop_lon"], group["stop_lat"], strict=True))
            if len(coords) >= 2:
                lines.append((LineString(coords), route_type))
        return lines


class PublicTransitNetworkPlotStep(GeoStep, GTFSStep):
    """Plots a map of the public-transit network active on `gtfs.date`, built directly from the
    configured GTFS feeds (`gtfs.files`), colored by transit mode.
    """

    input_files = {"area": SimulationAreaFile}
    output_files = {"plot": PublicTransitNetworkPlotFile}

    def run(self):
        import geopandas as gpd
        import matplotlib.lines as mlines
        import matplotlib.pyplot as plt

        area = self.input["area"].get_area(crs="EPSG:4326")
        minx, miny, maxx, maxy = area.bounds

        all_lines = list()
        for gtfs_path in self.gtfs_files:
            try:
                all_lines.extend(_gtfs_feed_lines(gtfs_path, self.gtfs_date))
            except Exception as e:
                logger.warning(f"Could not read GTFS feed `{gtfs_path}`: {e}")
        if not all_lines:
            raise MetropyError("No GTFS shape could be read from the configured `gtfs.files`.")

        gdf = gpd.GeoDataFrame(
            {"category": [_gtfs_route_type_category(route_type) for _, route_type in all_lines]},
            geometry=[geom for geom, _ in all_lines],
            crs="EPSG:4326",
        )
        gdf = gdf.cx[minx:maxx, miny:maxy].to_crs(self.crs)

        fig, ax = plt.subplots(figsize=(10, 10))
        legend_handles = list()
        # Iterate in a fixed order so that the legend is stable across runs.
        for category in GTFS_CATEGORY_LABELS:
            group = gdf[gdf["category"] == category]
            if group.empty:
                continue
            color = GTFS_CATEGORY_COLORS[category]
            group.plot(ax=ax, color=color, linewidth=0.8, alpha=0.7, zorder=2)
            label = GTFS_CATEGORY_LABELS[category]
            legend_handles.append(mlines.Line2D([], [], color=color, linewidth=1.5, label=label))
        ax.set_axis_off()
        ax.set_aspect("equal")
        ax.legend(
            handles=legend_handles,
            loc="lower left",
            fontsize=8,
            title="Transit mode",
            framealpha=0.9,
        )
        fig.tight_layout()
        self.output["plot"].write(fig)
