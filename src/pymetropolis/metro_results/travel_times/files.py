from pymetropolis.metro_pipeline.file import Column, MetroDataFrameFile, MetroDataType

SCHEMA_ZONE_OD_FREE_FLOW_TT = [
    Column(
        "origin_zone_id",
        MetroDataType.ID,
        description="Identifier of the origin zone.",
        nullable=False,
    ),
    Column(
        "destination_zone_id",
        MetroDataType.ID,
        description="Identifier of the destination zone.",
        nullable=False,
    ),
    Column(
        "free_flow_travel_time",
        MetroDataType.DURATION,
        description=(
            "Travel time by car between the two zones' road nodes, under free-flow conditions."
        ),
        nullable=False,
    ),
]

SCHEMA_ZONE_OD_CONGESTED_TT = [
    Column(
        "origin_zone_id",
        MetroDataType.ID,
        description="Identifier of the origin zone.",
        nullable=False,
    ),
    Column(
        "destination_zone_id",
        MetroDataType.ID,
        description="Identifier of the destination zone.",
        nullable=False,
    ),
    Column(
        "congested_travel_time",
        MetroDataType.DURATION,
        description=(
            "Travel time by car between the two zones' road nodes, under "
            "congested conditions, aggregated (median) over the configured time window."
        ),
        nullable=False,
    ),
    Column(
        "congested_travel_time_min",
        MetroDataType.DURATION,
        description=(
            "Minimum travel time over the congested breakpoints falling within the time window."
        ),
        nullable=False,
    ),
    Column(
        "congested_travel_time_max",
        MetroDataType.DURATION,
        description=(
            "Maximum travel time over the congested breakpoints falling within the time window."
        ),
        nullable=False,
    ),
    Column(
        "congested_travel_time_std",
        MetroDataType.DURATION,
        description=(
            "Standard deviation of the travel time over the congested breakpoints falling "
            "within the time window. Zero when only one breakpoint falls in the window."
        ),
        nullable=False,
    ),
]


class ZoneODLevel1FreeFlowTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone1_od_free_flow_travel_times.parquet"
    description = (
        "Travel time by car under free-flow conditions between each pair of Level-1 zones."
    )
    schema = SCHEMA_ZONE_OD_FREE_FLOW_TT


class ZoneODLevel1CongestedTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone1_od_congested_travel_times.parquet"
    description = (
        "Congested travel time by car between each pair of Level-1 zones, aggregated"
        "over a given time window."
    )
    schema = SCHEMA_ZONE_OD_CONGESTED_TT


class ZoneODLevel2FreeFlowTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone2_od_free_flow_travel_times.parquet"
    description = (
        "Travel time by car under free-flow conditions between each pair of Level-2 zones."
    )
    schema = SCHEMA_ZONE_OD_FREE_FLOW_TT


class ZoneODLevel2CongestedTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone2_od_congested_travel_times.parquet"
    description = (
        "Congested travel time by car between each pair of Level-2 zones, aggregated"
        "over a given time window."
    )
    schema = SCHEMA_ZONE_OD_CONGESTED_TT


class ZoneODLevel3FreeFlowTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone3_od_free_flow_travel_times.parquet"
    description = (
        "Travel time by car under free-flow conditions between each pair of Level-3 zones."
    )
    schema = SCHEMA_ZONE_OD_FREE_FLOW_TT


class ZoneODLevel3CongestedTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone3_od_congested_travel_times.parquet"
    description = (
        "Congested travel time by car between each pair of Level-3 zones, aggregated"
        "over a given time window."
    )
    schema = SCHEMA_ZONE_OD_CONGESTED_TT


class ZoneODLevel4FreeFlowTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone4_od_free_flow_travel_times.parquet"
    description = (
        "Travel time by car under free-flow conditions between each pair of Level-4 zones."
    )
    schema = SCHEMA_ZONE_OD_FREE_FLOW_TT


class ZoneODLevel4CongestedTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone4_od_congested_travel_times.parquet"
    description = (
        "Congested travel time by car between each pair of Level-4 zones, aggregated"
        "over a given time window."
    )
    schema = SCHEMA_ZONE_OD_CONGESTED_TT


class ZoneODLevel5FreeFlowTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone5_od_free_flow_travel_times.parquet"
    description = (
        "Travel time by car under free-flow conditions between each pair of Level-5 zones."
    )
    schema = SCHEMA_ZONE_OD_FREE_FLOW_TT


class ZoneODLevel5CongestedTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone5_od_congested_travel_times.parquet"
    description = (
        "Congested travel time by car between each pair of Level-5 zones, aggregated"
        "over a given time window."
    )
    schema = SCHEMA_ZONE_OD_CONGESTED_TT


SCHEMA_ZONE_OD_PUBLIC_TRANSIT_TT = [
    Column(
        "origin_zone_id",
        MetroDataType.ID,
        description="Identifier of the origin zone.",
        nullable=False,
    ),
    Column(
        "destination_zone_id",
        MetroDataType.ID,
        description="Identifier of the destination zone.",
        nullable=False,
    ),
    Column(
        "travel_time",
        MetroDataType.DURATION,
        description=(
            "Travel time by public transit between the two zones, aggregated (weighted median) "
            "over the zones' medoids."
        ),
        nullable=True,
    ),
    Column(
        "generalized_time",
        MetroDataType.DURATION,
        description=(
            "Generalized time by public transit between the two zones (travel time with "
            "mode-specific weights), aggregated (weighted median) over the zones' medoids."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "departure_time",
        MetroDataType.DURATION,
        description=(
            "Effective departure time from the origin medoid (time since local midnight of "
            "`gtfs.date`) of the itinerary whose travel time is the weighted median over the "
            "zones' medoids."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "arrival_time",
        MetroDataType.DURATION,
        description=(
            "Arrival time at the destination medoid (time since local midnight of `gtfs.date`) "
            "of the itinerary whose travel time is the weighted median over the zones' medoids."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "waiting_time",
        MetroDataType.DURATION,
        description=(
            "Waiting time of the itinerary whose travel time is the weighted median over the "
            "zones' medoids."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "initial_waiting_time",
        MetroDataType.DURATION,
        description=(
            "Waiting time between the requested departure time and the departure (or between "
            "the arrival and the requested arrival time) of the itinerary whose travel time is "
            "the weighted median over the zones' medoids. Included in `travel_time` only if "
            "`od_matrix_travel_times.public_transit_include_initial_wait` is true."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "in_vehicle_time",
        MetroDataType.DURATION,
        description=(
            "Time spent in public-transit vehicles (sum of the transit legs' durations), i.e., "
            "excluding initial waiting, access, egress, transfer walking and waiting times. Same "
            "itinerary as `travel_time`."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "access_mode",
        MetroDataType.STRING,
        description=(
            "Mode used to go from the origin medoid to the first public-transit stop: `WALK` or "
            "`CAR` (car drop-off, only with `od_matrix_travel_times.public_transit_access_mode` "
            "set to `car` or "
            "`walk_then_car`). Same itinerary as `travel_time`."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "access_time",
        MetroDataType.DURATION,
        description=(
            "Duration of the access (from the origin medoid to the first public-transit stop). "
            "Same itinerary as `travel_time`."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "egress_mode",
        MetroDataType.STRING,
        description=(
            "Mode used to go from the last public-transit stop to the destination medoid: `WALK` "
            "or `CAR` (car pick-up). Same itinerary as `travel_time`."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "egress_time",
        MetroDataType.DURATION,
        description=(
            "Duration of the egress (from the last public-transit stop to the destination "
            "medoid). Same itinerary as `travel_time`."
        ),
        nullable=True,
        optional=True,
    ),
    Column(
        "legs",
        MetroDataType.ANY,
        description=(
            "Sequence of legs (mode, travel time, route id, boarding and alighting stop ids) of "
            "the itinerary whose travel time is the weighted median over the zones' medoids."
        ),
        nullable=True,
        optional=True,
    ),
]


class ZoneODLevel1PublicTransitTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone1_od_public_transit_travel_times.parquet"
    description = "Travel time by public transit between each pair of Level-1 zones."
    schema = SCHEMA_ZONE_OD_PUBLIC_TRANSIT_TT


class ZoneODLevel2PublicTransitTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone2_od_public_transit_travel_times.parquet"
    description = "Travel time by public transit between each pair of Level-2 zones."
    schema = SCHEMA_ZONE_OD_PUBLIC_TRANSIT_TT


class ZoneODLevel3PublicTransitTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone3_od_public_transit_travel_times.parquet"
    description = "Travel time by public transit between each pair of Level-3 zones."
    schema = SCHEMA_ZONE_OD_PUBLIC_TRANSIT_TT


class ZoneODLevel4PublicTransitTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone4_od_public_transit_travel_times.parquet"
    description = "Travel time by public transit between each pair of Level-4 zones."
    schema = SCHEMA_ZONE_OD_PUBLIC_TRANSIT_TT


class ZoneODLevel5PublicTransitTravelTimesFile(MetroDataFrameFile):
    path = "demand/routing/zone5_od_public_transit_travel_times.parquet"
    description = "Travel time by public transit between each pair of Level-5 zones."
    schema = SCHEMA_ZONE_OD_PUBLIC_TRANSIT_TT
