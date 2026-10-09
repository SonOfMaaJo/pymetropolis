from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from pymetropolis.metro_demand.routing.files import (
    ZonesLevel1MedoidsFile,
    ZonesLevel1RoadNodeFile,
    ZonesLevel2MedoidsFile,
    ZonesLevel2RoadNodeFile,
    ZonesLevel3MedoidsFile,
    ZonesLevel3RoadNodeFile,
    ZonesLevel4MedoidsFile,
    ZonesLevel4RoadNodeFile,
    ZonesLevel5MedoidsFile,
    ZonesLevel5RoadNodeFile,
)
from pymetropolis.metro_demand.routing.opentripplanner import OpenTripPlannerStep
from pymetropolis.metro_demand.routing.routing_cli import RoutingCLIStep, run_routing, trip_routing
from pymetropolis.metro_network.road_network.files import (
    RoadEdgesCleanFile,
    RoadEdgesFreeFlowTravelTimeFile,
)
from pymetropolis.metro_pipeline.parameters import (
    BoolParameter,
    EnumParameter,
    ListParameter,
    TimeParameter,
)
from pymetropolis.metro_pipeline.steps import InputFile
from pymetropolis.metro_pipeline.types import Int, Time
from pymetropolis.metro_simulation.run.files import MetroNextExpectedTravelTimeFunctionsFile

from .files import (
    ZoneODLevel1CongestedTravelTimesFile,
    ZoneODLevel1FreeFlowTravelTimesFile,
    ZoneODLevel1PublicTransitTravelTimesFile,
    ZoneODLevel2CongestedTravelTimesFile,
    ZoneODLevel2FreeFlowTravelTimesFile,
    ZoneODLevel2PublicTransitTravelTimesFile,
    ZoneODLevel3CongestedTravelTimesFile,
    ZoneODLevel3FreeFlowTravelTimesFile,
    ZoneODLevel3PublicTransitTravelTimesFile,
    ZoneODLevel4CongestedTravelTimesFile,
    ZoneODLevel4FreeFlowTravelTimesFile,
    ZoneODLevel4PublicTransitTravelTimesFile,
    ZoneODLevel5CongestedTravelTimesFile,
    ZoneODLevel5FreeFlowTravelTimesFile,
    ZoneODLevel5PublicTransitTravelTimesFile,
)

if TYPE_CHECKING:
    import polars as pl


class ZonesODFreeFlowTravelTimesStep(RoutingCLIStep):
    """Computes the free-flow travel time by car between each ordered pair of zones, using each
    zone's representative road node (ZonesRoadNodeFile) as a virtual origin/destination.
    """

    zones = ListParameter(
        "od_matrix_travel_times.free_flow_zones_levels",
        inner=Int(lb=1, ub=5),
        max_length=5,
        description="Zones levels for which OD free-flow travel times are computed.",
        default=None,
        example="`[3, 4]`",
    )
    input_files = {
        "zone1_road_node": InputFile(
            ZonesLevel1RoadNodeFile, when=lambda step: step.zones is not None and 1 in step.zones
        ),
        "zone2_road_node": InputFile(
            ZonesLevel2RoadNodeFile, when=lambda step: step.zones is not None and 2 in step.zones
        ),
        "zone3_road_node": InputFile(
            ZonesLevel3RoadNodeFile, when=lambda step: step.zones is not None and 3 in step.zones
        ),
        "zone4_road_node": InputFile(
            ZonesLevel4RoadNodeFile, when=lambda step: step.zones is not None and 4 in step.zones
        ),
        "zone5_road_node": InputFile(
            ZonesLevel5RoadNodeFile, when=lambda step: step.zones is not None and 5 in step.zones
        ),
        "edges": RoadEdgesCleanFile,
        "edges_fftt": RoadEdgesFreeFlowTravelTimeFile,
    }
    output_files = {
        "zone1_fftt": ZoneODLevel1FreeFlowTravelTimesFile,
        "zone2_fftt": ZoneODLevel2FreeFlowTravelTimesFile,
        "zone3_fftt": ZoneODLevel3FreeFlowTravelTimesFile,
        "zone4_fftt": ZoneODLevel4FreeFlowTravelTimesFile,
        "zone5_fftt": ZoneODLevel5FreeFlowTravelTimesFile,
    }

    def is_defined(self) -> bool:
        return super().is_defined() and self.zones is not None

    def run(self):
        import polars as pl

        assert self.exec_path is not None
        assert self.zones is not None

        edges_gdf = self.input["edges"].read()
        edges_fftt = self.input["edges_fftt"].read()
        edges = (
            pl.from_pandas(edges_gdf.loc[:, ["edge_id", "source", "target", "length"]])
            .join(edges_fftt, on="edge_id", how="left")
            .with_columns(pl.col("free_flow_travel_time").dt.total_nanoseconds() / 1e9)
            .rename({"free_flow_travel_time": "weight"})
        )
        n = edges["weight"].null_count()
        if n:
            logger.warning(f"Discarding {n} edges with NULL free-flow travel time.")
            edges = edges.filter(pl.col("weight").is_not_null())

        for zone in self.zones:
            zones_df = self.input[f"zone{zone}_road_node"].read().select("zone_id", "road_node")
            pairs, trips = read_trips(zones_df)
            results = trip_routing(trips, edges, self.exec_path, with_routes=False)
            results = results.select(
                query_id="trip_id", free_flow_travel_time=pl.duration(seconds="value")
            )
            df = pairs.join(results, on="query_id", how="left", coalesce=False).select(
                "origin_zone_id", "destination_zone_id", "free_flow_travel_time"
            )
            self.output[f"zone{zone}_fftt"].write(df)


class ZonesODCongestedTravelTimesStep(RoutingCLIStep):
    """Computes the congested travel time by car between each ordered pair of zones, using each
    zone's representative road node as a virtual origin/destination.

    Unlike the free-flow variant, results depend on departure time: routing is
    run once per zone pair againt the congested edge travel-time functions
    produced by the simulation (MetroNextExpectedTravlTimeFunctionsFile),
    giving one breakpoint every recording_interval. The stored value is the median
    travel time over breakpoints falling within `time_window` (along with the min,
    max and standard deviation over the same breakpoints).
    If `time_window` is not specified, the full time window of the simulation is used.
    """

    time_window = ListParameter(
        "od_matrix_travel_times.time_window",
        inner=Time(),
        length=2,
        description="Time window over which the congested travel time is aggregated.",
        example="`[06:00:00, 09:00:00]`",
    )
    zones = ListParameter(
        "od_matrix_travel_times.congested_zones_levels",
        inner=Int(),
        max_length=5,
        description="Zones levels for which OD congested travel times are computed.",
        default=None,
        example="`[3, 4]`",
    )

    input_files = {
        "zone1_road_node": InputFile(
            ZonesLevel1RoadNodeFile, when=lambda step: step.zones is not None and 1 in step.zones
        ),
        "zone2_road_node": InputFile(
            ZonesLevel2RoadNodeFile, when=lambda step: step.zones is not None and 2 in step.zones
        ),
        "zone3_road_node": InputFile(
            ZonesLevel3RoadNodeFile, when=lambda step: step.zones is not None and 3 in step.zones
        ),
        "zone4_road_node": InputFile(
            ZonesLevel4RoadNodeFile, when=lambda step: step.zones is not None and 4 in step.zones
        ),
        "zone5_road_node": InputFile(
            ZonesLevel5RoadNodeFile, when=lambda step: step.zones is not None and 5 in step.zones
        ),
        "edges": RoadEdgesCleanFile,
        "edges_fftt": RoadEdgesFreeFlowTravelTimeFile,
        "edge_ttfs": MetroNextExpectedTravelTimeFunctionsFile,
    }
    output_files = {
        "zone1_congested": ZoneODLevel1CongestedTravelTimesFile,
        "zone2_congested": ZoneODLevel2CongestedTravelTimesFile,
        "zone3_congested": ZoneODLevel3CongestedTravelTimesFile,
        "zone4_congested": ZoneODLevel4CongestedTravelTimesFile,
        "zone5_congested": ZoneODLevel5CongestedTravelTimesFile,
    }

    def is_defined(self) -> bool:
        return super().is_defined() and self.zones is not None

    def run(self):
        import polars as pl

        assert self.exec_path is not None
        assert self.zones is not None

        edges_gdf = self.input["edges"].read()
        edges_fftt = self.input["edges_fftt"].read()
        edges = (
            pl.from_pandas(edges_gdf.loc[:, ["edge_id", "source", "target", "length"]])
            .join(edges_fftt, on="edge_id", how="left")
            .with_columns(pl.col("free_flow_travel_time").dt.total_nanoseconds() / 1e9)
            .rename({"free_flow_travel_time": "weight"})
        )
        n = edges["weight"].null_count()
        if n:
            logger.warning(f"Discarding {n} edges with NULL free-flow travel time.")
            edges = edges.filter(pl.col("weight").is_not_null())

        edge_ttfs = self.input["edge_ttfs"].read()
        if self.time_window is not None:
            # Remove breakpoints before the start of the time window (we don't need them and they
            # slow down the queries).
            edge_ttfs = edge_ttfs.filter(pl.col("departure_time") >= self.time_window[0].seconds())
        edge_ttfs = (
            edge_ttfs.filter(pl.col("vehicle_id") == "car_driver_alone")
            .select("edge_id", "departure_time", "travel_time")
            .join(edges.select("edge_id"), on="edge_id", how="semi")
        )

        for zone in self.zones:
            zones_df = self.input[f"zone{zone}_road_node"].read().select("zone_id", "road_node")
            pairs, trips = read_trips(zones_df)
            with tempfile.TemporaryDirectory() as tmp:
                tmp_directory = Path(tmp)
                processing_routing(trips, edges, self.exec_path, edge_ttfs, tmp_directory)
                df = pl.read_parquet(tmp_directory / "output" / "profile_results.parquet")
            if self.time_window is not None:
                # A null departure_time means the travel time is constant over the whole
                # simulated period (the route was never affected by congestion).
                df = df.filter(
                    pl.col("departure_time").is_null()
                    | (self.time_window[0].seconds() <= pl.col("departure_time"))
                    & (self.time_window[1].seconds() >= pl.col("departure_time"))
                )
            results = df.group_by("query_id").agg(
                congested_travel_time=pl.duration(seconds=pl.col("travel_time").median()),
                congested_travel_time_min=pl.duration(seconds=pl.col("travel_time").min()),
                congested_travel_time_max=pl.duration(seconds=pl.col("travel_time").max()),
                congested_travel_time_std=pl.duration(
                    seconds=pl.col("travel_time").std().fill_null(0.0)
                ),
            )
            df = pairs.join(results, on="query_id", how="left", coalesce=False).select(
                "origin_zone_id",
                "destination_zone_id",
                "congested_travel_time",
                "congested_travel_time_min",
                "congested_travel_time_max",
                "congested_travel_time_std",
            )
            self.output[f"zone{zone}_congested"].write(df)


class ZonesODPublicTransitTravelTimesStep(OpenTripPlannerStep):
    """Computes the travel time by public transit between each ordered pair of zones, using
    OpenTripPlanner.

    Each zone is represented by one or several medoids (`ZonesLevelNMedoidsFile`, computed by
    `ZonesLevelNMedoidsStep`), i.e. weighted representative points guaranteed to lie on the
    pedestrian network. For each ordered pair of distinct zones, a query is run between every
    combination of the origin zone's medoids and the destination zone's medoids, and the results
    are aggregated into a single value per zone pair using the weighted median (the weight of a
    combination being the product of its two medoids' weights).

    This is the zone-to-zone equivalent of `TripsOpenTripPlannerStep` /
    `SurveyedTripsOpenTripPlannerStep`, applied to the simulation's own zones instead of individual
    trips.
    """

    zones = ListParameter(
        "od_matrix_travel_times.public_transit_zones_levels",
        inner=Int(lb=1, ub=5),
        max_length=5,
        description="Zones levels for which OD public-transit travel times are computed.",
        default=None,
        example="`[3, 4]`",
    )
    time = TimeParameter(
        "od_matrix_travel_times.public_transit_time",
        description="Departure / arrival time used for all the public-transit OD queries.",
        note=(
            "Whether this is a departure or an arrival time depends on "
            "`od_matrix_travel_times.public_transit_arrive_by`."
        ),
    )
    arrive_by = BoolParameter(
        "od_matrix_travel_times.public_transit_arrive_by",
        default=False,
        description=(
            "If `True`, `od_matrix_travel_times.public_transit_time` is used as the arrival time "
            "at the destination medoid. Otherwise, it is used as the departure time from the "
            "origin medoid."
        ),
    )
    include_initial_wait = BoolParameter(
        "od_matrix_travel_times.public_transit_include_initial_wait",
        default=False,
        description=(
            "Whether the initial waiting time (between the requested time and the itinerary's "
            "departure / arrival) is included in the travel time and generalized time."
        ),
        note=(
            "OpenTripPlanner's travel time starts at the itinerary's actual departure, so a "
            "poorly-served OD pair with a departure 1 hour after the requested time is not "
            "penalized. "
            "When `true`, the initial waiting time is added to the travel time, and added to the "
            "generalized time with the waiting multiplier (`opentripplanner.multipliers.wait`); "
            "the itinerary minimizing this generalized time is selected. "
            "In all cases, the initial waiting time is reported in column `initial_waiting_time`."
        ),
    )

    access_mode = EnumParameter(
        "od_matrix_travel_times.public_transit_access_mode",
        values=["walk", "car", "walk_then_car"],
        default="walk",
        description=(
            "How the travelers can go from the origin to the first public-transit stop (access) "
            "and from the last stop to the destination (egress)."
        ),
        note=(
            'If `"walk"`, access and egress are walked. '
            'If `"car"`, the traveler can also be driven to any stop (car drop-off) and picked up '
            "at any stop (car pick-up); OpenTripPlanner selects the least-cost option between "
            "walking and car for each itinerary. "
            'If `"walk_then_car"`, the queries are first run with walking access / egress; the '
            "queries without any itinerary because no stop is reachable on foot from the origin "
            "(resp. to the destination) are run again with car access (resp. egress). "
            "The queries without itinerary for other reasons (e.g., no public-transit connection "
            "in the search window) are not run again. "
            "In all cases, the access / egress mode and duration are reported in columns "
            "`access_mode`, `access_time`, `egress_mode`, `egress_time`."
        ),
    )
    input_files = {
        "zone1_medoids": InputFile(
            ZonesLevel1MedoidsFile, when=lambda step: step.zones is not None and 1 in step.zones
        ),
        "zone2_medoids": InputFile(
            ZonesLevel2MedoidsFile, when=lambda step: step.zones is not None and 2 in step.zones
        ),
        "zone3_medoids": InputFile(
            ZonesLevel3MedoidsFile, when=lambda step: step.zones is not None and 3 in step.zones
        ),
        "zone4_medoids": InputFile(
            ZonesLevel4MedoidsFile, when=lambda step: step.zones is not None and 4 in step.zones
        ),
        "zone5_medoids": InputFile(
            ZonesLevel5MedoidsFile, when=lambda step: step.zones is not None and 5 in step.zones
        ),
    }
    output_files = {
        "zone1_transit": ZoneODLevel1PublicTransitTravelTimesFile,
        "zone2_transit": ZoneODLevel2PublicTransitTravelTimesFile,
        "zone3_transit": ZoneODLevel3PublicTransitTravelTimesFile,
        "zone4_transit": ZoneODLevel4PublicTransitTravelTimesFile,
        "zone5_transit": ZoneODLevel5PublicTransitTravelTimesFile,
    }

    def is_defined(self) -> bool:
        return super().is_defined() and self.zones is not None and self.time is not None

    def run(self):
        import polars as pl

        assert self.zones is not None
        assert self.time is not None

        total_seconds = round(self.time.seconds())
        hour = total_seconds // 3600 % 24
        minute = total_seconds // 60 % 60
        second = total_seconds % 60
        time_str = f"{hour:02d}:{minute:02d}:{second:02d}"

        for zone in self.zones:
            medoids = self.input[f"zone{zone}_medoids"].read().to_crs("EPSG:4326")
            medoids["lng"] = medoids["geometry"].x
            medoids["lat"] = medoids["geometry"].y
            medoids_df = pl.from_pandas(medoids[["zone_id", "weight", "lng", "lat"]])

            zone_ids = medoids_df.select("zone_id").unique(maintain_order=True)
            pairs = zone_ids.select(origin_zone_id="zone_id").join(
                zone_ids.select(destination_zone_id="zone_id"), how="cross"
            )
            pairs = pairs.filter(pl.col("origin_zone_id") != pl.col("destination_zone_id"))

            combos = build_zone_od_queries(pairs, medoids_df, time_str, self.arrive_by)
            df = self.run_queries(
                combos, self.access_mode or "walk", bool(self.include_initial_wait)
            )
            results = aggregate_zone_od_results(pairs, combos, df)
            self.output[f"zone{zone}_transit"].write(results)


def build_zone_od_queries(
    pairs: pl.DataFrame, medoids: pl.DataFrame, time_str: str, arrive_by: bool
) -> pl.DataFrame:
    """Returns one OpenTripPlanner query for each combination (origin medoid, destination medoid)
    of each zone pair.

    `pairs` must have columns `origin_zone_id` and `destination_zone_id`; `medoids` must have
    columns `zone_id`, `weight`, `lng` and `lat` (WGS 84). The returned DataFrame has one row per
    query, identified by `trip_id`, with the id of the zone pair in column `pair_id` and the weight
    of the combination (product of the two medoids' weights) in column `weight`.
    """
    import polars as pl

    pairs = pairs.with_columns(pair_id=pl.int_range(0, pl.len(), dtype=pl.UInt64))
    return (
        pairs.join(medoids, left_on="origin_zone_id", right_on="zone_id", how="inner")
        .join(medoids, left_on="destination_zone_id", right_on="zone_id", how="inner")
        .select(
            "pair_id",
            origin_lng="lng",
            origin_lat="lat",
            destination_lng="lng_right",
            destination_lat="lat_right",
            weight=pl.col("weight") * pl.col("weight_right"),
            time=pl.lit(time_str),
            arrive_by=pl.lit(arrive_by),
        )
        .with_columns(trip_id=pl.int_range(0, pl.len(), dtype=pl.UInt64))
    )


def aggregate_zone_od_results(
    pairs: pl.DataFrame, combos: pl.DataFrame, results: pl.DataFrame
) -> pl.DataFrame:
    """Aggregates the OpenTripPlanner results of the medoid combinations into a single value per
    zone pair, using the weighted median over the combinations for which an itinerary was found.

    The travel time of a zone pair is NULL only if no itinerary was found for any of its medoid
    combinations. The number of combinations with an itinerary is logged, not returned.

    The departure and arrival times, the (initial) waiting times, the access / egress modes and
    times and the legs returned are those of the itinerary whose travel time is the weighted
    median.
    """
    import polars as pl

    pairs = pairs.with_columns(pair_id=pl.int_range(0, pl.len(), dtype=pl.UInt64))
    df = results.join(combos.select("trip_id", "pair_id", "weight"), on="trip_id")
    out = pairs
    for col in ("travel_time", "generalized_time"):
        # Weighted median ("inverted CDF" definition): smallest value such that the cumulative
        # weight of the values lower or equal is at least half of the total weight. NULL values
        # (no itinerary found) are ignored.
        medians = (
            df.filter(pl.col(col).is_not_null())
            .sort("pair_id", col)
            .with_columns(
                cum_weight=pl.col("weight").cum_sum().over("pair_id"),
                tot_weight=pl.col("weight").sum().over("pair_id"),
            )
            .filter(pl.col("cum_weight") >= 0.5 * pl.col("tot_weight"))
            .group_by("pair_id")
            .agg(pl.col(col).first(), pl.col("trip_id").first().alias(f"{col}_trip_id"))
        )
        out = out.join(medians, on="pair_id", how="left")
    out = out.join(
        df.select(
            "departure_time",
            "arrival_time",
            "waiting_time",
            "initial_waiting_time",
            "in_vehicle_time",
            "access_mode",
            "access_time",
            "egress_mode",
            "egress_time",
            "legs",
            travel_time_trip_id="trip_id",
        ),
        on="travel_time_trip_id",
        how="left",
    )
    nb_partial = (
        df.group_by("pair_id")
        .agg(pl.col("travel_time").null_count().alias("n"), pl.len())
        .filter((pl.col("n") > 0) & (pl.col("n") < pl.col("len")))
        .height
    )
    nb_null = out["travel_time"].null_count()
    logger.info(
        f"Public-transit OD travel times: {nb_null:,} / {len(out):,} zone pairs without any "
        f"itinerary ({nb_null / max(len(out), 1):.1%}), {nb_partial:,} zone pairs with an "
        "itinerary for only some of their medoid combinations"
    )
    return out.select(
        "origin_zone_id",
        "destination_zone_id",
        "travel_time",
        "generalized_time",
        "departure_time",
        "arrival_time",
        "waiting_time",
        "initial_waiting_time",
        "in_vehicle_time",
        "access_mode",
        "access_time",
        "egress_mode",
        "egress_time",
        "legs",
    )


def read_trips(zones: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Builds the zone x zone virtual "trips" used as OD queries for routing:
    every ordered pair of distinct zones, using each zone's representative road
    node as origin/destination.

    Returns two views of the same pairs: 'pairs' keeps the zone ids (used to join
    the routing results back to zone ids once routing is done),'trips' keeps only
    the node ids, renamed to the format expected by 'trip_routing()/processing_routing()'
    ('trip_id', 'origin_node', 'destination_node')
    """
    import polars as pl

    pairs = zones.select(origin_zone_id="zone_id", origin_node="road_node").join(
        zones.select(destination_zone_id="zone_id", destination_node="road_node"), how="cross"
    )
    pairs = pairs.filter(pl.col("origin_zone_id") != pl.col("destination_zone_id")).with_columns(
        query_id=pl.int_range(0, pl.len(), dtype=pl.UInt64)
    )
    trips = pairs.select("query_id", "origin_node", "destination_node").rename(
        {"query_id": "trip_id"}
    )
    return pairs, trips


def processing_routing(
    trips: pl.DataFrame,
    edges: pl.DataFrame,
    routing_exec_path: Path,
    edge_ttfs: pl.DataFrame,
    tmp_directory: Path,
):
    """Runs the routing executable in "Intersect" mode (temporal profile) for a
    set of OD queries.

    This is the profile-mode equivalent of 'prepare_routing()'/'run_routing' in
    'routing_cli.py', which only supports a single fixed departure time per query
    (the executable returns the full travel time profile instead of a single value),
    and the edge-level congested travel-time functions ('edge_ttfs') are written
    alongside the statuc edge weights so the executable can reconstruct each edge's
    travel time at any departure_time. Results are read separately by the caller
    from 'tmp_directory/output/profile_results.parquet'.
    """
    import polars as pl

    queries = trips.select(
        query_id="trip_id",
        origin="origin_node",
        destination="destination_node",
        departure_time=pl.lit(None, dtype=pl.Float64),
    )
    queries.write_parquet(tmp_directory / "queries.parquet")

    edges = edges.select("edge_id", "source", "target", "weight")
    edges = edges.sort("weight").unique(subset=["source", "target"], keep="first").sort("edge_id")
    edges.rename({"weight": "travel_time"}).write_parquet(tmp_directory / "edges.parquet")

    edge_ttfs.write_parquet(tmp_directory / "edge_ttfs.parquet")

    parameters = {
        "algorithm": "Intersect",
        "output_route": False,
        "input_files": {
            "queries": "queries.parquet",
            "edges": "edges.parquet",
            "edge_ttfs": "edge_ttfs.parquet",
        },
        "output_directory": "output",
        "saving_format": "Parquet",
    }
    with open(tmp_directory / "parameters.json", "w") as f:
        json.dump(parameters, f)
    run_routing(routing_exec_path, tmp_directory)
