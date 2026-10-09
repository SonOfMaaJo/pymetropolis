from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from pymetropolis.metro_demand.routing.od_pairs import (
    StepWithPedestrianForbiddenTypes,
    StepWithRoadForbiddenTypes,
)
from pymetropolis.metro_demand.zones.file import (
    ZonesLevel1File,
    ZonesLevel2File,
    ZonesLevel3File,
    ZonesLevel4File,
    ZonesLevel5File,
)
from pymetropolis.metro_network.pedestrian_network.files import PedestrianEdgesCleanFile
from pymetropolis.metro_network.road_network.files import RoadEdgesCleanFile
from pymetropolis.metro_pipeline.parameters import EnumParameter, FloatParameter, IntParameter
from pymetropolis.metro_spatial import GeoStep
from pymetropolis.random import RandomStep

from .files import (
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

if TYPE_CHECKING:
    import geopandas as gpd
    import numpy as np
    import polars as pl


class ZonesBaseModel(GeoStep, StepWithRoadForbiddenTypes):
    threshold = FloatParameter(
        "zones.weiszfeld.threshold",
        default=1e-6,
        description=(
            "Convergence threshold for the Weiszfeld algorithm used to approximate each zone's "
            "geometric median road node."
        ),
        note=(
            "The algorithm stops once the candidate point moves by less than this distance "
            "(in the network's CRS units) between two iterations."
        ),
    )
    max_iter = IntParameter(
        "zones.weiszfeld.max_iter",
        default=100,
        description=(
            "Maximum number of iterations for the Weiszfeld algorithm used to approximate each "
            "zone's geometric median road node."
        ),
    )

    priority = 0

    def weiszfeldOptimumNode(self, nodes: np.ndarray) -> np.ndarray:
        """Find the approximate of the geometric median of nodes."""
        import numpy as np

        assert self.max_iter is not None
        assert self.threshold is not None

        node_ = nodes.mean(axis=0)
        for _ in range(self.max_iter):
            distances = np.sqrt(np.sum((nodes - node_) ** 2, axis=1))
            if np.all(distances != 0):
                weights = 1 / distances
                new_node_ = np.sum(weights[:, None] * nodes, axis=0) / np.sum(weights)
            else:
                new_node_ = nodes[distances == 0][0, :]

            if np.sqrt(np.sum((node_ - new_node_) ** 2)) < self.threshold:
                return new_node_

            node_ = new_node_
        return node_

    def find_origin_destination_node(
        self, zones: gpd.GeoDataFrame, edges: gpd.GeoDataFrame
    ) -> pl.DataFrame:
        import geopandas as gpd
        import pandas as pd
        import polars as pl
        from scipy.spatial import KDTree
        from shapely.geometry import Point

        logger.debug("Listing candidate road-network nodes.")
        source_nodes = edges[["source", "geometry"]].rename(columns={"source": "node"})
        source_nodes["geometry"] = source_nodes["geometry"].apply(lambda g: Point(g.coords[0]))
        target_nodes = edges[["target", "geometry"]].rename(columns={"target": "node"})
        target_nodes["geometry"] = target_nodes["geometry"].apply(lambda g: Point(g.coords[-1]))
        nodes = gpd.GeoDataFrame(
            pd.concat([source_nodes, target_nodes], ignore_index=True), crs=edges.crs
        ).drop_duplicates(subset="node")

        logger.debug("Assigning each road-network node to its zone.")
        nodes = nodes.sjoin(zones, how="inner", predicate="within").drop(columns=["index_right"])

        logger.debug("Finding the optimum_node node in each zone.")
        nodes["x"] = nodes.geometry.x
        nodes["y"] = nodes.geometry.y
        df = pl.from_pandas(nodes.loc[:, ["node", "zone_id", "x", "y"]])
        optimum_nodes: dict = {}
        for (zone_id,), zone_df in df.partition_by(
            "zone_id", as_dict=True, include_key=False
        ).items():
            nodes_ = zone_df.select("x", "y").to_numpy()
            tree = KDTree(nodes_)
            o_node = self.weiszfeldOptimumNode(nodes_)
            _, optimum_node_idx = tree.query(o_node)
            optimum_nodes[zone_id] = zone_df["node"][int(optimum_node_idx)]
        return pl.DataFrame(
            {"zone_id": list(optimum_nodes.keys()), "road_node": list(optimum_nodes.values())}
        ).with_columns(pl.col("road_node").cast(pl.UInt64))

    def run(self):
        zones = self.input["zone"].read()
        zones = zones.to_crs(self.crs)
        edges = self.input["edges"].read()
        edges = edges.loc[
            ~edges["edge_type"].isin(self.forbidden_types),
            ["edge_id", "geometry", "source", "target"],
        ]
        nodes = self.find_origin_destination_node(zones, edges)
        self.output["zone_road_node"].write(nodes)


class ZonesLevel1RoadNodesStep(ZonesBaseModel):
    input_files = {"zone": ZonesLevel1File, "edges": RoadEdgesCleanFile}
    output_files = {"zone_road_node": ZonesLevel1RoadNodeFile}


class ZonesLevel2RoadNodesStep(ZonesBaseModel):
    input_files = {"zone": ZonesLevel2File, "edges": RoadEdgesCleanFile}
    output_files = {"zone_road_node": ZonesLevel2RoadNodeFile}


class ZonesLevel3RoadNodesStep(ZonesBaseModel):
    input_files = {"zone": ZonesLevel3File, "edges": RoadEdgesCleanFile}
    output_files = {"zone_road_node": ZonesLevel3RoadNodeFile}


class ZonesLevel4RoadNodesStep(ZonesBaseModel):
    input_files = {"zone": ZonesLevel4File, "edges": RoadEdgesCleanFile}
    output_files = {"zone_road_node": ZonesLevel4RoadNodeFile}


class ZonesLevel5RoadNodesStep(ZonesBaseModel):
    input_files = {"zone": ZonesLevel5File, "edges": RoadEdgesCleanFile}
    output_files = {"zone_road_node": ZonesLevel5RoadNodeFile}


class ZonesMedoidsBaseStep(StepWithPedestrianForbiddenTypes, RandomStep):
    """Runs a KMedoids clustering on the pedestrian-network nodes within each zone, to get one or
    several representative points (medoids) per zone, weighted by the summed length of pedestrian
    edges assigned to each cluster.

    This is the same clustering as `SurveyedZoneMedoidsStep`, generalized to the simulation's own
    zones (all zones of the level, instead of only those referenced by observed survey trips).
    """

    nb_clusters = IntParameter(
        "zones.nb_medoids",
        default=1,
        description="Number of clusters when running the KMedoids algorithm on zones.",
        note=(
            "Larger values increase the running time quadratically, but lead to more accurate "
            "travel times."
        ),
    )
    algorithm = EnumParameter(
        "zones.medoids_algorithm",
        values=["kmedoids", "weiszfeld"],
        default="kmedoids",
        description="Algorithm used to compute the representative point(s) of each zone.",
        note=(
            '"kmedoids" runs an exact KMedoids / PAM clustering (pairwise distance matrix, '
            "quadratic in the number of nodes per zone: accurate but can be very slow/memory-heavy "
            'for zones with many nodes). "weiszfeld" clusters nodes with a lightweight KMeans '
            "then refines each cluster's center with Weiszfeld's algorithm (geometric median, "
            "linear in the number of nodes per zone) before snapping it to the nearest actual "
            "node: much cheaper, but only an approximation."
        ),
    )

    priority = 0

    def run(self):
        from itertools import cycle, islice

        import geopandas as gpd
        import numpy as np
        import polars as pl
        from shapely.geometry import Point
        from sklearn.cluster import KMeans
        from sklearn_extra.cluster import KMedoids
        from tqdm import tqdm

        assert self.nb_clusters is not None
        assert self.forbidden_types is not None
        assert self.algorithm is not None

        def weiszfeld_median(points: np.ndarray, tol: float = 1e-4, max_iter: int = 200) -> np.ndarray:
            # Iterative approximation of the geometric median (minimizes the sum of Euclidean
            # distances to all points), much cheaper than an exact medoid search.
            y = points.mean(axis=0)
            for _ in range(max_iter):
                distances = np.maximum(np.linalg.norm(points - y, axis=1), 1e-12)
                weights = 1.0 / distances
                y_new = (weights[:, None] * points).sum(axis=0) / weights.sum()
                if np.linalg.norm(y_new - y) < tol:
                    return y_new
                y = y_new
            return y

        zones = self.input["zone"].read()
        zones = zones[["zone_id", "geometry"]]

        edges: gpd.GeoDataFrame = self.input["edges"].read()
        edges = edges.loc[
            ~edges["edge_type"].isin(self.forbidden_types),
            ["edge_id", "geometry", "source", "target", "length"],
        ]
        nodes = gpd.GeoDataFrame(
            edges.groupby("source").agg({"length": "sum", "geometry": "first"}), crs=edges.crs
        )
        nodes = nodes.reset_index(names="node_id")
        nodes["geometry"] = nodes["geometry"].apply(lambda geom: Point(geom.coords[0]))
        nodes = nodes.sjoin(zones.to_crs(nodes.crs), predicate="intersects", how="inner")
        nodes["x"] = nodes.geometry.x
        nodes["y"] = nodes.geometry.y
        nodes_df = pl.from_pandas(nodes.drop(columns=["geometry", "index_right"]))

        medoids = list()
        zones_nodes_by_id = nodes_df.partition_by("zone_id", include_key=False, as_dict=True)
        for (zone_id,), zone_nodes in tqdm(
            zones_nodes_by_id.items(),
            total=len(zones_nodes_by_id),
            desc="Computing zone medoids",
            smoothing=0.05,
        ):
            X = zone_nodes.select("x", "y").to_numpy()
            if len(X) < self.nb_clusters:
                # There are fewer nodes than clusters: return each node as a center.
                # Cycle the nodes so that the number of centers stay fixed.
                centers = np.fromiter(
                    islice(cycle(X), self.nb_clusters), dtype=np.dtype((float, 2))
                )
                weights = np.repeat(1 / len(centers), len(centers))
            elif self.algorithm == "weiszfeld":
                # Fast approximation: cluster with KMeans (linear in the number of nodes), then
                # refine each cluster's center with Weiszfeld's algorithm and snap it to the
                # nearest actual node in the cluster, instead of an exact (quadratic) KMedoids.
                kmeans = KMeans(
                    n_clusters=self.nb_clusters, random_state=self.random_seed, n_init=1
                ).fit(X)
                labels = kmeans.labels_
                centers = np.empty((self.nb_clusters, 2))
                for cluster in range(self.nb_clusters):
                    cluster_points = X[labels == cluster]
                    median = weiszfeld_median(cluster_points)
                    nearest = np.argmin(np.linalg.norm(cluster_points - median, axis=1))
                    centers[cluster] = cluster_points[nearest]
                values_by_cluster = (
                    zone_nodes.with_columns(cluster=pl.Series(labels))
                    .group_by("cluster")
                    .agg(pl.col("length").sum())
                    .with_columns(weight=pl.col("length") / pl.col("length").sum())
                    .sort("cluster")
                )
                weights = values_by_cluster["weight"].to_numpy()
            else:
                kmedoids = KMedoids(n_clusters=self.nb_clusters, random_state=self.random_seed).fit(
                    X
                )
                centers = kmedoids.cluster_centers_
                values_by_cluster = (
                    zone_nodes.with_columns(cluster=pl.Series(kmedoids.labels_))
                    .group_by("cluster")
                    .agg(pl.col("length").sum())
                    .with_columns(weight=pl.col("length") / pl.col("length").sum())
                    .sort("cluster")
                )
                weights = values_by_cluster["weight"].to_numpy()
            medoids.extend(
                [
                    [zone_id, i + 1, weights[i], centers[i][0], centers[i][1]]
                    for i in range(len(centers))
                ]
            )
        medoids_df = pl.DataFrame(
            medoids, schema=["zone_id", "index", "weight", "x", "y"], orient="row"
        )
        n = medoids_df["zone_id"].n_unique()
        if n < len(zones):
            logger.warning(f"No pedestrian node in zone for {len(zones) - n} zones.")
        medoids_gdf = gpd.GeoDataFrame(
            data=medoids_df.drop("x", "y").to_pandas(),
            geometry=gpd.GeoSeries.from_xy(medoids_df["x"], medoids_df["y"], crs=edges.crs),
        )
        self.output["medoids"].write(medoids_gdf)


class ZonesLevel1MedoidsStep(ZonesMedoidsBaseStep):
    input_files = {"zone": ZonesLevel1File, "edges": PedestrianEdgesCleanFile}
    output_files = {"medoids": ZonesLevel1MedoidsFile}


class ZonesLevel2MedoidsStep(ZonesMedoidsBaseStep):
    input_files = {"zone": ZonesLevel2File, "edges": PedestrianEdgesCleanFile}
    output_files = {"medoids": ZonesLevel2MedoidsFile}


class ZonesLevel3MedoidsStep(ZonesMedoidsBaseStep):
    input_files = {"zone": ZonesLevel3File, "edges": PedestrianEdgesCleanFile}
    output_files = {"medoids": ZonesLevel3MedoidsFile}


class ZonesLevel4MedoidsStep(ZonesMedoidsBaseStep):
    input_files = {"zone": ZonesLevel4File, "edges": PedestrianEdgesCleanFile}
    output_files = {"medoids": ZonesLevel4MedoidsFile}


class ZonesLevel5MedoidsStep(ZonesMedoidsBaseStep):
    input_files = {"zone": ZonesLevel5File, "edges": PedestrianEdgesCleanFile}
    output_files = {"medoids": ZonesLevel5MedoidsFile}
