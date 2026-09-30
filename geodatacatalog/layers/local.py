"""Local file provider (GeoJSON, GeoPackage, GeoTIFF...)."""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import rioxarray

from ..base import BaseLayer, LayerKind
from ..request import GeoRequest

log = logging.getLogger(__name__)


class LocalLayer(BaseLayer):
    """Reads a local vector or raster file, clipped to the request bbox.

    Local reads are fast and the file may change on disk, so results are
    never disk-cached (``cacheable = False``).
    """

    cacheable = False

    def __init__(self, layer_id, path, kind=LayerKind.VECTOR, **kwargs):
        super().__init__(layer_id, str(path), **kwargs)
        self._kind = kind
        self.path = Path(path)

    @property
    def kind(self):
        return self._kind

    def _fetch_sync(self, request: GeoRequest):
        if self._kind == LayerKind.VECTOR:
            gdf = gpd.read_file(self.path, bbox=request.bbox)
            if request.crs:
                gdf = gdf.to_crs(request.crs)
            return gdf
        da = rioxarray.open_rasterio(self.path)
        xmin, ymin, xmax, ymax = request.bbox
        return da.rio.clip_box(minx=xmin, miny=ymin, maxx=xmax, maxy=ymax)
