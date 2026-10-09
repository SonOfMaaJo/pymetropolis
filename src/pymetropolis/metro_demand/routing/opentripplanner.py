from __future__ import annotations

import math
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import humanize
from loguru import logger

from pymetropolis.common import ThreadedStep
from pymetropolis.metro_common import MetropyError
from pymetropolis.metro_demand.departure_time.files import TstarsFile
from pymetropolis.metro_demand.population.files import (
    TripsDestinationsFile,
    TripsFile,
    TripsOriginsFile,
)
from pymetropolis.metro_demand.routing.files import TripsPublicTransitItinerariesFile
from pymetropolis.metro_network.public_transit import GTFSStep
from pymetropolis.metro_pipeline import PopulationStep
from pymetropolis.metro_pipeline.parameters import (
    EnumParameter,
    FloatParameter,
    IntParameter,
    StringParameter,
    TimeParameter,
)
from pymetropolis.metro_pipeline.steps import InputFile

if TYPE_CHECKING:
    import polars as pl
    import requests

    from pymetropolis.metro_common.time import MetroTime

MAX_TRIES = 3

HEADERS = {"Content-Type": "application/json", "OTPTimeout": "10000"}

QUERY = """
query plan(
    $originLat: CoordinateValue!,
    $originLng: CoordinateValue!,
    $destinationLat: CoordinateValue!,
    $destinationLng: CoordinateValue!,
    $datetime: OffsetDateTime!,
    $walkSpeed: Speed!,
    $walkReluctance: Reluctance!,
    $waitReluctance: Reluctance!,
    $busReluctance: Reluctance!,
    $tramReluctance: Reluctance!,
    $subwayReluctance: Reluctance!,
    $railReluctance: Reluctance!,
    $transferCost: Cost!,
    $access: [PlanAccessMode!],
    $egress: [PlanEgressMode!],
) {
    planConnection(
        origin: {
          location:  { coordinate: { latitude: $originLat, longitude: $originLng } }
        }
        destination: {
          location:  { coordinate: { latitude: $destinationLat, longitude: $destinationLng } }
        }
        dateTime: { __DATETIME_FIELD__: $datetime }
        modes: {
          transitOnly: false
          direct: [WALK]
          transit: {
            access: $access
            egress: $egress
            transit: [
            { mode: BUS,        cost: { reluctance: $busReluctance } },
            { mode: COACH,      cost: { reluctance: $busReluctance } }
            { mode: TROLLEYBUS, cost: { reluctance: $busReluctance } }
            { mode: TRAM,       cost: { reluctance: $tramReluctance } }
            { mode: CABLE_CAR,  cost: { reluctance: $tramReluctance }  }
            { mode: FUNICULAR,  cost: { reluctance: $tramReluctance } }
            { mode: FERRY,      cost: { reluctance: $tramReluctance } }
            { mode: SUBWAY,     cost: { reluctance: $subwayReluctance } }
            { mode: MONORAIL,   cost: { reluctance: $subwayReluctance } }
            { mode: RAIL,       cost: { reluctance: $railReluctance } }
            ]
          }
        }
        preferences: {
          street: {
            walk: { speed: $walkSpeed, reluctance: $walkReluctance, boardCost: 0, safetyFactor: 0 }
          }
          transit: {
            board: { waitReluctance: $waitReluctance }
            transfer: { slack: "PT1M", cost: $transferCost }
          }
        }
    ) {
      routingErrors {
        code
        inputField
      }
      edges {
        node {
          start
          end
          duration
          generalizedCost
          waitingTime
          legs {
            mode
            duration
            route { gtfsId }
            from { stop { gtfsId } }
            to { stop { gtfsId } }
          }
        }
      }
    }
}
"""

# Access (origin -> first stop) and egress (last stop -> destination) modes allowed by OTP. Walking
# is always allowed, OTP selects the least-cost option. With `CAR_DROP_OFF` / `CAR_PICKUP`, the
# traveler is driven to / picked up at any stop reachable by car (OTP requires `CAR_PICKUP` to be
# combined with `WALK`).
WALK_ACCESS = ["WALK"]
CAR_ACCESS = ["WALK", "CAR_DROP_OFF"]
WALK_EGRESS = ["WALK"]
CAR_EGRESS = ["WALK", "CAR_PICKUP"]

QUERY_ARRIVAL = QUERY.replace("__DATETIME_FIELD__", "latestArrival")
QUERY_DEPARTURE = QUERY.replace("__DATETIME_FIELD__", "earliestDeparture")

_thread_local = threading.local()


def get_session() -> requests.Session:
    import requests

    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return _thread_local.session


def run_queries_batch(
    trips: pl.DataFrame,
    api_url: str,
    parameters: dict,
    timezone: str,
    nb_threads: int | None = None,
    include_initial_wait: bool = False,
) -> pl.DataFrame:
    import polars as pl
    from tqdm import tqdm

    logger.debug("Running new batch")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=nb_threads) as executor:
        futures = [
            executor.submit(
                get_least_cost_itinerary, row, api_url, parameters, timezone, include_initial_wait
            )
            for row in trips.iter_rows(named=True)
        ]
        results = []
        for future in tqdm(
            as_completed(futures), total=len(futures), desc="Processing batch", smoothing=0.01
        ):
            results.append(future.result())
    df = pl.from_records(
        results,
        orient="row",
        schema=[
            ("trip_id", trips.schema["trip_id"]),
            ("departure_time", pl.Float64),
            ("arrival_time", pl.Float64),
            ("travel_time", pl.Float64),
            ("generalized_time", pl.Float64),
            ("waiting_time", pl.Float64),
            ("initial_waiting_time", pl.Float64),
            ("in_vehicle_time", pl.Float64),
            ("access_mode", pl.String),
            ("access_time", pl.Float64),
            ("egress_mode", pl.String),
            ("egress_time", pl.Float64),
            (
                "legs",
                pl.List(
                    pl.Struct(
                        [
                            pl.Field("mode", pl.String),
                            pl.Field("travel_time", pl.Float64),
                            pl.Field("route_id", pl.String),
                            pl.Field("from_stop_id", pl.String),
                            pl.Field("to_stop_id", pl.String),
                        ]
                    )
                ),
            ),
            ("query_time", pl.Float64),
            ("routing_errors", pl.List(pl.String)),
        ],
    )
    tot_query_time = timedelta(seconds=float(df["query_time"].sum()))
    tot_time = timedelta(seconds=time.time() - t0)
    logger.debug(f"Total query time: {humanize.precisedelta(tot_query_time)}")
    logger.debug(f"Total time: {humanize.precisedelta(tot_time)}")
    log_routing_errors(df)
    df = df.with_columns(
        departure_time=pl.duration(seconds=pl.col("departure_time")),
        arrival_time=pl.duration(seconds=pl.col("arrival_time")),
        travel_time=pl.duration(seconds=pl.col("travel_time")),
        generalized_time=pl.duration(seconds=pl.col("generalized_time")),
        waiting_time=pl.duration(seconds=pl.col("waiting_time")),
        initial_waiting_time=pl.duration(seconds=pl.col("initial_waiting_time")),
        in_vehicle_time=pl.duration(seconds=pl.col("in_vehicle_time")),
        access_time=pl.duration(seconds=pl.col("access_time")),
        egress_time=pl.duration(seconds=pl.col("egress_time")),
        legs=pl.col("legs").list.eval(
            pl.element().struct.with_fields(
                travel_time=pl.duration(seconds=pl.element().struct.field("travel_time"))
            )
        ),
    ).drop("query_time")
    return df


def log_routing_errors(df: pl.DataFrame):
    """Logs how many queries returned no itinerary, with the routing error codes returned by
    OpenTripPlanner for them."""
    import polars as pl

    nb_empty = df["travel_time"].null_count()
    if nb_empty == 0:
        return
    logger.warning(
        f"No itinerary found for {nb_empty:,} / {len(df):,} queries ({nb_empty / len(df):.1%})"
    )
    codes = (
        df.filter(pl.col("travel_time").is_null())
        .select(
            code=pl.when(pl.col("routing_errors").list.len() > 0)
            .then(pl.col("routing_errors").list.sort().list.join(","))
            .otherwise(pl.lit("NO_ERROR_REPORTED"))
        )
        .group_by("code")
        .len()
        .sort("len", descending=True)
    )
    for code, count in codes.iter_rows():
        logger.warning(f"  {code}: {count:,}")


def format_datetime(day: date | str, hms: str, timezone: str) -> str:
    """Returns the ISO-8601 datetime (with UTC offset) corresponding to the given local date and
    time in the given time zone, taking daylight-saving time into account."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    dt = datetime.combine(day, dtime.fromisoformat(hms), tzinfo=ZoneInfo(timezone))
    return dt.isoformat()


def seconds_since_midnight(day: date | str, iso_datetime: str, timezone: str) -> float:
    """Returns the number of seconds between local midnight of the given day (in the given time
    zone) and the given ISO-8601 datetime (can exceed 24 hours if the datetime is on a later
    day)."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    midnight = datetime.combine(day, dtime(0), tzinfo=ZoneInfo(timezone))
    return (datetime.fromisoformat(iso_datetime) - midnight).total_seconds()


def initial_waiting_time(it: dict, requested: float, arrive_by: bool, day, timezone: str) -> float:
    """Returns the time (in seconds) between the requested departure time and the itinerary's
    departure (or, for arrive-by queries, between the itinerary's arrival and the requested
    arrival time).

    This waiting time (at the origin or destination) is not included in OpenTripPlanner's
    `duration`.
    """
    if arrive_by:
        wait = requested - seconds_since_midnight(day, it["end"], timezone)
    else:
        wait = seconds_since_midnight(day, it["start"], timezone) - requested
    return max(wait, 0.0)


def get_least_cost_itinerary(
    row: dict,
    api_url: str,
    parameters: dict,
    timezone: str,
    include_initial_wait: bool = False,
    nb_tries: int = 0,
):
    from requests.exceptions import RequestException

    requested_datetime = format_datetime(row["date"], row["time"], timezone)
    requested = seconds_since_midnight(row["date"], requested_datetime, timezone)
    variables = {
        **parameters,
        "datetime": requested_datetime,
        "originLat": row["origin_lat"],
        "originLng": row["origin_lng"],
        "destinationLat": row["destination_lat"],
        "destinationLng": row["destination_lng"],
    }
    if row["arrive_by"]:
        query = QUERY_ARRIVAL
    else:
        query = QUERY_DEPARTURE
    session = get_session()
    try:
        t0 = time.time()
        req = session.post(api_url, headers=HEADERS, json={"query": query, "variables": variables})
        query_time = time.time() - t0
        req.raise_for_status()
        data = req.json()
    except (RequestException, ValueError) as e:
        if nb_tries > MAX_TRIES:
            raise MetropyError(f"OpenTripPlanner request failed: {e}")
        # Retry.
        return get_least_cost_itinerary(
            row, api_url, parameters, timezone, include_initial_wait, nb_tries + 1
        )
    plan = (data.get("data") or dict()).get("planConnection") or dict()
    itineraries = plan.get("edges", None)
    if itineraries is None:
        raise MetropyError(f"Invalid OpenTripPlanner result data:\n{data}")
    # Error codes, suffixed by the faulty input field if any (e.g. `NO_STOPS_IN_RANGE:FROM`).
    routing_errors = [
        err["code"] + (f":{err['inputField']}" if err.get("inputField") else "")
        for err in plan.get("routingErrors") or []
    ]
    wait_reluctance = parameters["waitReluctance"]

    def initial_wait(it: dict) -> float:
        return initial_waiting_time(it, requested, row["arrive_by"], row["date"], timezone)

    def cost(it: dict) -> float:
        # When the initial wait is included, it is valued like any other waiting time.
        if include_initial_wait:
            return it["generalizedCost"] + wait_reluctance * initial_wait(it)
        return it["generalizedCost"]

    # Find the itinerary with the least cost.
    edge = min(itineraries, key=lambda e: cost(e["node"]), default=None)
    if edge is None:
        # No itinerary found.
        return (
            row["trip_id"],
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            query_time,
            routing_errors,
        )
    it = edge["node"]
    wait = initial_wait(it)
    legs = [clean_leg(leg) for leg in it["legs"]]
    access_mode, access_time, egress_mode, egress_time = access_egress(legs)
    in_vehicle_time = sum(leg["travel_time"] for leg in legs if leg["route_id"] is not None)
    return (
        row["trip_id"],
        seconds_since_midnight(row["date"], it["start"], timezone),
        seconds_since_midnight(row["date"], it["end"], timezone),
        it["duration"] + wait if include_initial_wait else it["duration"],
        round(cost(it)),
        it["waitingTime"],
        wait,
        in_vehicle_time,
        access_mode,
        access_time,
        egress_mode,
        egress_time,
        legs,
        query_time,
        routing_errors,
    )


def clean_leg(leg: dict):
    if leg["route"] is not None:
        route_id = leg["route"]["gtfsId"]
        from_stop = leg["from"]["stop"]["gtfsId"]
        to_stop = leg["to"]["stop"]["gtfsId"]
    else:
        route_id = None
        from_stop = None
        to_stop = None
    result = {
        "mode": leg["mode"],
        "travel_time": leg["duration"],
        "route_id": route_id,
        "from_stop_id": from_stop,
        "to_stop_id": to_stop,
    }
    return result


def access_egress(legs: list[dict]) -> tuple[str, float, str, float]:
    """Returns the mode and duration of the access (legs before the first transit leg) and of the
    egress (legs after the last transit leg) of an itinerary.

    The mode is `"WALK"` if all the legs are walking legs, otherwise the non-walking mode (e.g.
    `"CAR"`, possibly preceded or followed by a short walk). For an itinerary without any transit
    leg (walk only), the whole itinerary is counted as access.
    """

    def mode(part: list[dict]) -> str:
        return next((leg["mode"] for leg in part if leg["mode"] != "WALK"), "WALK")

    def duration(part: list[dict]) -> float:
        return sum(leg["travel_time"] for leg in part)

    transit = [i for i, leg in enumerate(legs) if leg["route_id"] is not None]
    if not transit:
        return mode(legs), duration(legs), "WALK", 0.0
    access = legs[: transit[0]]
    egress = legs[transit[-1] + 1 :]
    return mode(access), duration(access), mode(egress), duration(egress)


def clean_trips_time(
    trips: pl.DataFrame, tstars: pl.DataFrame | None, time_type: str, time: MetroTime | None
):
    """Add `seconds` and `arrive_by` column to trips."""
    import polars as pl

    if time_type in ("departure", "arrival"):
        col_name = f"{time_type}_time"
        if col_name not in trips.columns:
            trips = trips.with_columns(pl.lit(None, dtype=pl.Duration).alias(col_name))
        trips = trips.select(
            "trip_id", seconds=pl.col(col_name).dt.total_seconds(), arrive_by=time_type == "arrival"
        )
    elif time_type == "tstar":
        if tstars is None:
            raise MetropyError('tstars should be given when `time_type` is `"tstar"`')
        trips = (
            trips.select("trip_id")
            .join(
                tstars.select("trip_id", seconds=pl.col("tstar").dt.total_seconds()),
                on="trip_id",
                how="left",
            )
            .with_columns(arrive_by=True)
        )
    else:
        trips = trips.select(
            "trip_id", seconds=pl.lit(None, dtype=pl.Int64), arrive_by=time_type == "custom_arrival"
        )
    if trips["seconds"].is_null().any():
        if time is None:
            c = trips["seconds"].null_count()
            raise MetropyError(
                f"Departure / arrival time is undefined for {c:,} trips but the "
                "`opentripplanner.time` parameter is not set."
            )
        if "custom" not in time_type:
            s = trips["seconds"].null_count() / len(trips)
            logger.warning(
                f"{s:.1%} trips have NULL values for departure / arrival time, using default "
                "value for them"
            )
        # Fill null values for time column with the given time parameter.
        trips = trips.with_columns(pl.col("seconds").fill_null(round(time.seconds())))
    return trips


class OpenTripPlannerStep(ThreadedStep, GTFSStep):
    """Abstract Step for all steps querying the OpenTripPlanner server."""

    otp_url = StringParameter(
        "opentripplanner.url",
        default="http://0.0.0.0:8080",
        description="URL from which the OpenTripPlanner API can be accessed.",
    )
    timezone = StringParameter(
        "opentripplanner.timezone",
        default="Europe/Paris",
        description="IANA time zone in which the departure / arrival times of the requests are "
        "expressed.",
        note=(
            "The times given in the configuration or read from the input files are local times "
            "in this time zone; they are converted to the corresponding UTC offset (taking "
            "daylight-saving time into account) before being sent to OpenTripPlanner."
        ),
    )
    batch_size = IntParameter(
        "opentripplanner.batch_size",
        description="How many trips should be processed in each batch.",
        note=(
            "Default is to process all trips in a single batch. "
            "Use a lower value if you are running out of memory."
        ),
    )
    walking_speed = FloatParameter(
        "opentripplanner.walking_speed",
        default=4.0,
        description="Walking speed for public-transit trips, in km/h.",
    )
    walking_reluctance = FloatParameter(
        "opentripplanner.multipliers.walk",
        default=2.0,
        description="Multiplier for the value of time walking.",
    )
    waiting_reluctance = FloatParameter(
        "opentripplanner.multipliers.wait",
        default=1.1,
        description="Multiplier for the value of time waiting.",
    )
    bus_reluctance = FloatParameter(
        "opentripplanner.multipliers.bus",
        default=1.2,
        description="Multiplier for the value of time in a bus.",
    )
    tram_reluctance = FloatParameter(
        "opentripplanner.multipliers.tram",
        default=1.0,
        description="Multiplier for the value of time in a tramway.",
    )
    subway_reluctance = FloatParameter(
        "opentripplanner.multipliers.subway",
        default=1.0,
        description="Multiplier for the value of time in a subway.",
    )
    rail_reluctance = FloatParameter(
        "opentripplanner.multipliers.rail",
        default=1.0,
        description="Multiplier for the value of time for rail transport.",
    )
    transfer_cost = IntParameter(
        "opentripplanner.transfer_cost",
        default=300,
        description="Penalty for transfers, in seconds equivalent.",
    )

    def is_defined(self):
        return self.gtfs_date is not None

    def get_parameters(self):
        assert self.walking_speed is not None
        return {
            "walkSpeed": self.walking_speed / 3.6,
            "walkReluctance": self.walking_reluctance,
            "waitReluctance": self.waiting_reluctance,
            "busReluctance": self.bus_reluctance,
            "tramReluctance": self.tram_reluctance,
            "subwayReluctance": self.subway_reluctance,
            "railReluctance": self.rail_reluctance,
            "transferCost": self.transfer_cost,
        }

    def run_queries(
        self, trips: pl.DataFrame, access_mode: str = "walk", include_initial_wait: bool = False
    ) -> pl.DataFrame:
        """Runs the OpenTripPlanner queries of the given trips.

        `access_mode` (`"walk"`, `"car"` or `"walk_then_car"`) controls how the stops can be
        reached from the origin / destination and `include_initial_wait` whether the initial
        waiting time is included in the travel time (see
        `ZonesODPublicTransitTravelTimesStep`).
        """
        import polars as pl

        for col in ("origin_lng", "origin_lat", "destination_lng", "destination_lat"):
            assert trips[col].null_count() == 0, f"Found null values for column `{col}"

        # Add date column.
        trips = trips.with_columns(date=self.gtfs_date)

        if access_mode == "car":
            return self.run_queries_with_modes(trips, CAR_ACCESS, CAR_EGRESS, include_initial_wait)
        df = self.run_queries_with_modes(trips, WALK_ACCESS, WALK_EGRESS, include_initial_wait)
        if access_mode != "walk_then_car":
            return df
        # Run again the queries without any stop reachable on foot, allowing car access (resp.
        # egress) only on the side(s) where no stop was found.
        errors = pl.col("routing_errors")
        no_stops = df.filter(pl.col("travel_time").is_null()).select(
            "trip_id",
            car_access=errors.list.contains("NO_STOPS_IN_RANGE:FROM"),
            car_egress=errors.list.contains("NO_STOPS_IN_RANGE:TO"),
        )
        no_stops = no_stops.filter(pl.col("car_access") | pl.col("car_egress"))
        results = [df.join(no_stops, on="trip_id", how="anti")]
        for (car_access, car_egress), ids in no_stops.group_by("car_access", "car_egress"):
            logger.info(
                f"Running again {len(ids):,} queries without any stop reachable on foot, with car "
                f"access: {car_access}, car egress: {car_egress}"
            )
            results.append(
                self.run_queries_with_modes(
                    trips.join(ids.select("trip_id"), on="trip_id", how="semi"),
                    CAR_ACCESS if car_access else WALK_ACCESS,
                    CAR_EGRESS if car_egress else WALK_EGRESS,
                    include_initial_wait,
                )
            )
        return pl.concat(results, how="vertical")

    def run_queries_with_modes(
        self,
        trips: pl.DataFrame,
        access: list[str],
        egress: list[str],
        include_initial_wait: bool = False,
    ) -> pl.DataFrame:
        import polars as pl

        assert self.otp_url is not None
        assert self.timezone is not None

        parameters = {**self.get_parameters(), "access": access, "egress": egress}
        batch_size = self.batch_size or len(trips)
        batch_size = max(batch_size, 1)
        nb_batches = math.ceil(len(trips) / batch_size)
        if nb_batches == 1:
            return run_queries_batch(
                trips,
                self.otp_url,
                parameters,
                self.timezone,
                self.nb_threads,
                include_initial_wait,
            )
        with tempfile.TemporaryDirectory() as tmp_dir:
            for i in range(nb_batches):
                df = run_queries_batch(
                    trips[i * batch_size : (i + 1) * batch_size],
                    self.otp_url,
                    parameters,
                    self.timezone,
                    self.nb_threads,
                    include_initial_wait,
                )
                df.write_parquet(Path(tmp_dir) / Path(f"otp_results_{i}.parquet"))
                del df
            df = pl.concat(
                (
                    pl.scan_parquet(Path(tmp_dir) / Path(f"otp_results_{i}.parquet"))
                    for i in range(nb_batches)
                ),
                how="vertical",
            ).collect()
        return df


class TripsOpenTripPlannerStep(OpenTripPlannerStep, PopulationStep):
    """Computes the trips' travel time and generalized time by public transit with OpenTripPlanner.

    This step requires having access to an OpenTripPlanner API server.
    You can run one on your machine by following the
    [OpenTripPlanner documentation](https://docs.opentripplanner.org/en/latest/Basic-Tutorial).
    This step has been tested with OpenTripPlanner v2.9.0.

    If you want a simpler (but less flexible) way to compute public-transit travel times, check out
    [TripsPublicTransitTravelTimeFromR5Step](steps.md#tripspublictransittraveltimefromr5step).

    The OpenTripPlanner server must be running when you execute Pymetropolis and accessible from the
    URL [`opentripplanner.url`](parameters.md#opentripplannerurl) (`"http://0.0.0.0:8080"` by
    default).

    The [`gtfs.date`](parameters.md#gtfsdate) parameter controls the date used in the public-transit
    timetables, for all queries.
    You need to ensure that the GTFS file(s) have running services at this date.

    The departure / arrival time of the trips is control by the
    [`time_type`](parameters.md#opentripplannertime_type) and
    [`time`](parameters.md#opentripplannertime) parameters.
    Five different cases are possible:

    - `time_type = "departure"`: trips are departing at their ex-ante departure time, read from
      [TripsFile](files.md#tripsfile) (with `time` as a fallback value)
    - `time_type = "arrival"`: trips are arriving at their ex-ante arrival time, read from
      [TripsFile](files.md#tripsfile) (with `time` as a fallback value)
    - `time_type = "tstar"`: trips are arriving at their desired arrival time, read from
      [TstarsFile](files.md#tstarsfile) (with `time` as a fallback value)
    - `time_type = "custom_departure"`: trips are all departing at time `time`
    - `time_type = "custom_arrival"`: trips are all arriving at time `time`

    Note that the departure / arrival time represents a time window, controllable by
    OpenTripPlanner: for example, a trips with departure time `08:00:00` might actually depart at
    `08:04:00` to match the schedule of a bus.

    The itinerary selected by OpenTripPlanner is the one that minimizes the "generalized time",
    which is equal to the travel time with different weights applied to different modes.

    You can control how the generalized time function is defined through
    several parameters:

    - [`walking_speed`](parameters.md#opentripplannerwalking_speed): walking speed on the pedestrian
      network, in km/h (default is 4 km/h).
    - [`transfer_cost`](parameters.md#opentripplannertransfer_cost): penalty for transfers, in
      seconds equivalent (default is 5 minutes).
    - [`multipliers.walk`](parameters.md#opentripplannermultiplierswalk): multiplier for the value
      of time walking (default is 2).
    - [`multipliers.wait`](parameters.md#opentripplannermultiplierswait): multiplier for the value
      of time waiting (default is 1.1).
    - [`multipliers.bus`](parameters.md#opentripplannermultipliersbus): multiplier for the value of
      time in a bus (default is 1.2).
    - [`multipliers.tram`](parameters.md#opentripplannermultiplierstram): multiplier for the value
      of time in a tramway (default is 1).
    - [`multipliers.subway`](parameters.md#opentripplannermultiplierssubway): multiplier for the
      value of time in a subway (default is 1).
    - [`multipliers.rail`](parameters.md#opentripplannermultipliersrail): multiplier for the value
      of time for rail transport (default is 1).

    When running this step, the public-transit itineraries of all trips are hold in memory.
    If you are running out of memory, you can try to use the
    [`batch_size`](parameters.md#opentripplannerbatch_size) parameter to reduce RAM consumption.
    Reducing the batch size should reduce memory consumption, at the cost of an increase in running
    time.
    By default, all trips are run in a single batch.

    Example of configuration for this step:

    ```toml
    [gtfs]
    date = 2026-05-28

    [opentripplanner]
    url = "http://0.0.0.0:8080"
    batch_size = 50000
    time_type = "tstar"
    walking_speed = 4.0
    transfer_cost = 300
    [opentripplanner.multipliers]
    walk = 2
    wait = 1.1
    bus = 1.2
    tram = 1
    subway = 1
    rail = 1
    ```
    """

    time_type = EnumParameter(
        "opentripplanner.time_type",
        values=["departure", "arrival", "tstar", "custom_departure", "custom_arrival"],
        description="How the departure / arrival time of the requests is defined.",
        note=(
            "If `\"departure\"`, trips' departure times are read from the trips' ex-ante departure "
            "times. "
            "If `\"arrival\"`, trips' arrival times are read from the trips' ex-ante arrival "
            "times. "
            "If `\"tstar\"`, trips' arrival times are read from the trips' desired arrival times. "
            'If `"custom_departure"`, the departure times are equal to the value of '
            "`opentripplanner.time` for all trips. "
            'If `"custom_arrival"`, the arrival times are equal to the value of '
            "`opentripplanner.time` for all trips. "
        ),
    )
    time = TimeParameter(
        "opentripplanner.time",
        description="Departure / arrival time of the requests.",
        note=(
            'If `time_type` is `"custom_departure"`, this is the departure time used for all '
            "requests. "
            'If `time_type` is `"custom_arrival"`, this is the arrival time used for all '
            "requests. "
            "Otherwise, the value is only used as a default for missing departure / arrival time."
        ),
    )
    input_files = {
        "trips": TripsFile,
        "origins": TripsOriginsFile,
        "destinations": TripsDestinationsFile,
        "tstars": InputFile(
            TstarsFile,
            when=lambda inst: inst.time_type == "tstar",
            when_doc='if `time_type` is `"tstar"`',
        ),
    }
    output_files = {"costs": TripsPublicTransitItinerariesFile}

    def is_defined(self):
        return super(OpenTripPlannerStep, self).is_defined() and self.time_type is not None

    def run(self):
        import polars as pl

        assert self.time_type is not None

        trips = self.input["trips"].read()
        # Note that tstars are read even when not required. This could be optimized although the
        # impact is probably very small.
        trips = clean_trips_time(
            trips, self.input["tstars"].read_if_exists(), self.time_type, self.time
        )
        # Convert time column to a HH:MM:SS string.
        trips = trips.with_columns(
            time=pl.time(
                hour=pl.col("seconds") // 3600 % 24,
                minute=pl.col("seconds") // 60 % 60,
                second=pl.col("seconds") % 60,
            ).dt.strftime("%H:%M:%S")
        ).drop("seconds")

        # Read origin / destination longitude and latitude.
        origins = self.input["origins"].read()
        origins.to_crs("EPSG:4326", inplace=True)
        origins_df = pl.DataFrame(
            {
                "trip_id": origins["trip_id"],
                "origin_lng": origins.geometry.x,
                "origin_lat": origins.geometry.y,
            }
        )
        trips = trips.join(origins_df, on="trip_id")
        destinations = self.input["destinations"].read()
        destinations.to_crs("EPSG:4326", inplace=True)
        destinations_df = pl.DataFrame(
            {
                "trip_id": destinations["trip_id"],
                "destination_lng": destinations.geometry.x,
                "destination_lat": destinations.geometry.y,
            }
        )
        trips = trips.join(destinations_df, on="trip_id")

        df = self.run_queries(trips)
        self.output["costs"].write(df)
