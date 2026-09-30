"""WCS provider → xarray.DataArray (owslib)."""
from __future__ import annotations

import logging
from io import BytesIO

import rioxarray
import xarray as xr

from ..base import BaseLayer, LayerKind, retry_on_network_error
from ..request import GeoRequest

log = logging.getLogger(__name__)


class WCSLayer(BaseLayer):
    """
    Downloads a WCS 2.0.1 coverage through owslib.
    The coverage is auto-detected from GetCapabilities when ``coverage_id``
    is not provided.

    Example::
        WCSLayer(
            layer_id="sand_0_5cm",
            url="https://maps.isric.org/mapserv?map=/map/sand.map",
            coverage_id="sand_0-5cm_mean",
            scale_size=128,
        )
    """

    kind = LayerKind.RASTER

    def __init__(self, layer_id, url, coverage_id: str | None = None,
                 scale_size: int = 128,
                 lon_axis: str = "Long", lat_axis: str = "Lat",
                 subsetting_crs: str = "http://www.opengis.net/def/crs/EPSG/0/4326",
                 output_crs: str = "http://www.opengis.net/def/crs/EPSG/0/4326",
                 fmt: str = "image/tiff",
                 **kwargs):
        from types import SimpleNamespace
        super().__init__(layer_id, url, **kwargs)
        self.product = SimpleNamespace(
            coverage_id=coverage_id,
            scale_size=scale_size,
            lon_axis=lon_axis,
            lat_axis=lat_axis,
            subsetting_crs=subsetting_crs,
            output_crs=output_crs,
            fmt=fmt,
        )

    @retry_on_network_error()
    def _fetch_sync(self, request: GeoRequest) -> xr.DataArray:
        import requests as req_lib
        from owslib.wcs import WebCoverageService

        wcs = WebCoverageService(url=self.url, version="2.0.1")
        cov_id = self.product.coverage_id
        if cov_id is None:
            cov_id = list(wcs.contents.keys())[0]
        elif cov_id not in wcs.contents:
            raise ValueError(
                f"Coverage '{cov_id}' not found. "
                f"Available: {list(wcs.contents.keys())}"
            )

        assert request.bbox is not None
        xmin, ymin, xmax, ymax = request.bbox
        base = self.url.split("?")[0]
        extra = "?" + "&".join(
            p for p in (self.url.split("?")[1:] or [""])
            if p and not any(k in p.upper() for k in ("SERVICE=", "REQUEST=", "VERSION="))
        ) if "?" in self.url else ""

        qs = (
            f"{base}{extra}{'&' if extra else '?'}"
            f"SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage"
            f"&COVERAGEID={cov_id}"
            f"&FORMAT={self.product.fmt}"
            f"&SUBSET={self.product.lon_axis}({xmin},{xmax})"
            f"&SUBSET={self.product.lat_axis}({ymin},{ymax})"
            f"&SUBSETTINGCRS={self.product.subsetting_crs}"
            f"&OUTPUTCRS={self.product.output_crs}"
            f"&SCALESIZE={self.product.lon_axis}({self.product.scale_size}),{self.product.lat_axis}({self.product.scale_size})"
        )

        r = req_lib.get(qs, timeout=120)
        r.raise_for_status()
        da = rioxarray.open_rasterio(BytesIO(r.content))
        log.info(f"[{self.layer_id}] WCS shape={da.shape}")
        return da
