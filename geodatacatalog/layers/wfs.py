"""WFS provider → GeoDataFrame (owslib)."""
from __future__ import annotations

import logging
import threading

import geopandas as gpd
import pandas as pd

from ..base import BaseLayer, LayerKind, retry_on_network_error
from ..request import GeoRequest

log = logging.getLogger(__name__)


class WFSLayer(BaseLayer):
    """
    OGC WFS service accessed through owslib.
    Native CRS auto-detected from GetCapabilities, automatic pagination.

    Example::
        WFSLayer(
            layer_id="communes",
            url="https://data.geopf.fr/wfs/ows",
            typename="ADMINEXPRESS-COG.LATEST:commune",
        )
        WFSLayer(
            layer_id="failles_brgm",
            url="http://mapsref.brgm.fr/wxs/1GG/BRGM_1M_INSPIRE_geolUnits_geolFaults",
            typename="gmlsp:GE_GeologicFault_BRGM_EN_1M_Surface",
        )
    """

    kind = LayerKind.VECTOR

    def __init__(self, layer_id, url, typename, version="2.0.0",
                 page_size=5000, **kwargs):
        from types import SimpleNamespace
        super().__init__(layer_id, url, **kwargs)
        self.product = SimpleNamespace(
            typename=typename,
            version=version,
            page_size=page_size,
        )
        self._wfs_instance = None
        self._wfs_lock = threading.Lock()

    def _get_wfs(self):
        if self._wfs_instance is None:
            with self._wfs_lock:
                if self._wfs_instance is None:
                    from owslib.wfs import WebFeatureService
                    self._wfs_instance = WebFeatureService(
                        url=self.url, version=self.product.version, timeout=self.timeout
                    )
        return self._wfs_instance

    @retry_on_network_error()
    def _fetch_sync(self, request: GeoRequest) -> gpd.GeoDataFrame:
        from pyproj import Transformer

        wfs = self._get_wfs()
        if self.product.typename not in wfs.contents:
            available = list(wfs.contents)
            raise ValueError(
                f"[{self.layer_id}] typename '{self.product.typename}' not found "
                f"in the WFS capabilities ({self.url}). "
                f"Available layers: {available}"
            )
        layer_crs = wfs.contents[self.product.typename].crsOptions[0].getcode()

        assert request.bbox is not None
        if layer_crs.upper() not in ("EPSG:4326", "CRS:84"):
            t = Transformer.from_crs("EPSG:4326", layer_crs, always_xy=True)
            x0, y0 = t.transform(request.bbox[0], request.bbox[1])
            x1, y1 = t.transform(request.bbox[2], request.bbox[3])
            bbox_proj = [x0, y0, x1, y1]
        else:
            bbox_proj = list(request.bbox)

        all_gdfs, startindex = [], 0
        while True:
            response = wfs.getfeature(
                typename=self.product.typename,
                bbox=bbox_proj,
                srsname=layer_crs,
                startindex=startindex,
                maxfeatures=self.product.page_size,
            )
            gdf_page = gpd.read_file(response)
            if gdf_page.empty:
                break
            if gdf_page.crs is None:
                gdf_page = gdf_page.set_crs(layer_crs)
            all_gdfs.append(gdf_page)
            startindex += len(gdf_page)
            log.info(f"[{self.layer_id}] WFS pagination: {startindex} features")
            if len(gdf_page) < self.product.page_size:
                break
            if request.max_features and startindex >= request.max_features:
                break

        if not all_gdfs:
            return gpd.GeoDataFrame(geometry=[], crs=layer_crs)

        gdf = pd.concat(all_gdfs, ignore_index=True)
        if gdf.crs is None:
            gdf = gdf.set_crs(layer_crs)
        # When bbox was skipped (typename not in caps), clip spatially now.
        if bbox_proj is None and request.bbox is not None:
            from shapely.geometry import box
            bbox_wgs84 = box(*request.bbox)
            gdf_wgs84 = gdf.to_crs("EPSG:4326")
            gdf = gdf[gdf_wgs84.intersects(bbox_wgs84)].reset_index(drop=True)
            log.info(f"[{self.layer_id}] Post-fetch spatial filter: {len(gdf)} features in bbox")
        if request.crs and gdf.crs.to_epsg() != int(request.crs.split(":")[-1]):
            gdf = gdf.to_crs(request.crs)
        log.info(f"[{self.layer_id}] WFS: {len(gdf)} features")
        return gdf
