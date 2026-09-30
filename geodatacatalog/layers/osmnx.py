"""OpenStreetMap provider → GeoDataFrame (osmnx)."""
from __future__ import annotations

import logging

import geopandas as gpd
from shapely.geometry import box

from ..base import BaseLayer, LayerKind
from ..request import GeoRequest

log = logging.getLogger(__name__)


class OSMNXLayer(BaseLayer):
    kind = LayerKind.VECTOR

    def __init__(self, layer_id, url="https://nominatim.openstreetmap.org/search",
                 network_type="drive", tags: dict | None = None, **kwargs):
        super().__init__(layer_id, url, **kwargs)
        self.network_type = network_type
        self.tags = tags  # when set: feature mode (osmnx.features_from_polygon)

    def _fetch_sync(self, request: GeoRequest) -> gpd.GeoDataFrame:
        import osmnx as ox
        aoi = (request.aoi_gdf.to_crs("EPSG:4326").union_all()
               if request.aoi_gdf is not None else box(*request.bbox))
        if self.tags:
            gdf = ox.features_from_polygon(aoi, tags=self.tags)
            gdf = gdf[gdf.geometry.notna()].copy()
            # Keep centroids only for polygon features (buildings, etc.)
            gdf["geometry"] = gdf.geometry.centroid
        else:
            G = ox.graph_from_polygon(aoi, network_type=self.network_type)
            gdf = ox.graph_to_gdfs(G, nodes=False)
        gdf = gdf.reset_index(drop=True)
        if request.crs:
            gdf = gdf.to_crs(request.crs)
        return gdf
