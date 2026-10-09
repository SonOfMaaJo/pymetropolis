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
from .od_zones import (
    ZonesODCongestedTravelTimesStep,
    ZonesODFreeFlowTravelTimesStep,
    ZonesODPublicTransitTravelTimesStep,
)

TRAVEL_TIMES_FILES = [
    ZoneODLevel1CongestedTravelTimesFile,
    ZoneODLevel2CongestedTravelTimesFile,
    ZoneODLevel3CongestedTravelTimesFile,
    ZoneODLevel4CongestedTravelTimesFile,
    ZoneODLevel5CongestedTravelTimesFile,
    ZoneODLevel1FreeFlowTravelTimesFile,
    ZoneODLevel2FreeFlowTravelTimesFile,
    ZoneODLevel3FreeFlowTravelTimesFile,
    ZoneODLevel4FreeFlowTravelTimesFile,
    ZoneODLevel5FreeFlowTravelTimesFile,
    ZoneODLevel1PublicTransitTravelTimesFile,
    ZoneODLevel2PublicTransitTravelTimesFile,
    ZoneODLevel3PublicTransitTravelTimesFile,
    ZoneODLevel4PublicTransitTravelTimesFile,
    ZoneODLevel5PublicTransitTravelTimesFile,
]

TRAVEL_TIMES_STEPS = [
    ZonesODFreeFlowTravelTimesStep,
    ZonesODCongestedTravelTimesStep,
    ZonesODPublicTransitTravelTimesStep,
]
