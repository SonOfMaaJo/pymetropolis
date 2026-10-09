from pymetropolis.metro_pipeline.file import MetroPlotFile, PopulationFile


class TourDepartureTimeConvergencePlotFile(MetroPlotFile):
    path = "results/graphs/convergence/tour_departure_time.png"
    description = "RMSE of departure-time shift from one iteration to another."


class SimulatedRoadTravelTimesConvergencePlotFile(MetroPlotFile):
    path = "results/graphs/convergence/simulated_road_travel_times.png"
    description = "RMSE of simulated edge-level travel times from one iteration to another."


class ExpectedRoadTravelTimesConvergencePlotFile(MetroPlotFile):
    path = "results/graphs/convergence/expected_road_travel_times.png"
    description = "RMSE of expected edge-level travel times from one iteration to another."


class RouteLengthDiffConvergencePlotFile(MetroPlotFile):
    path = "results/graphs/convergence/route_length_diff.png"
    description = (
        "Mean length of the selected route not selected during the previous iteration, over "
        "iterations."
    )


class MeanSurplusConvergencePlotFile(MetroPlotFile):
    path = "results/graphs/convergence/mean_surplus.png"
    description = "Mean surplus over iterations."


class TripDepartureTimeDistributionPlotFile(MetroPlotFile, PopulationFile):
    path = "results/graphs/{population}/trips/departure_time_distribution.png"
    description = "Histogram of departure time distribution, over trips."


class ExpectedRoadNetworkCongestionFunctionPlotFile(MetroPlotFile):
    path = "results/graphs/road_network/congestion_function_expected.png"
    description = (
        "Expected congestion function over all edges of the road network. "
        "Values are computed as `Σ exp travel time / Σ free-flow travel time - 1`, "
        "with the sumations over all edges."
    )


class SimulationRoadNetworkCongestionFunctionPlotFile(MetroPlotFile):
    path = "results/graphs/road_network/congestion_function_simulated.png"
    description = (
        "Simulated congestion function over all edges of the road network. "
        "Values are computed as `Σ sim travel time / Σ free-flow travel time - 1`, "
        "with the sumations over all edges."
    )


class TripModeSharesPlotFile(MetroPlotFile, PopulationFile):
    path = "results/graphs/{population}/trips/mode_shares.png"
    description = "Mode shares at the trip-level (in number of trips)."


class StudyAreaPlotFile(MetroPlotFile):
    path = "results/graphs/study_area.png"
    description = (
        "Map of the study area: level-3 zones (communes), highlighting the simulation area, "
        "with level-2 zone (department) boundaries and labels overlaid when available."
    )


class PublicTransitNetworkPlotFile(MetroPlotFile):
    path = "results/graphs/public_transit_network.png"
    description = (
        "Map of the public-transit network active on `gtfs.date`, built from the configured "
        "GTFS feeds, colored by transit mode."
    )


class RoadNetworkCapacityPlotFile(MetroPlotFile):
    path = "results/graphs/road_network/capacities.png"
    description = "Map of the road network, colored by edge type, to visualize capacity assignment."


class RoadNetworkFlowPlotFile(MetroPlotFile):
    path = "results/graphs/road_network/flow.png"
    description = (
        "Map of the road network, with edges colored and sized by simulated traffic flow "
        "(number of vehicles from `route_results`)."
    )
