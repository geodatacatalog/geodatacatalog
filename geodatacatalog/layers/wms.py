"""WMS provider → xarray.DataArray (owslib + legend + auto-zoom)."""
from __future__ import annotations

import logging
import math
import threading
from io import BytesIO

import numpy as np
import rioxarray
import xarray as xr

from ..base import BaseLayer, LayerKind, retry_on_network_error
from ..request import GeoRequest
from ..utils import _apply_legend, _rgba_to_classes

log = logging.getLogger(__name__)


def _snap_time(positions, requested: str) -> str:
    """Snap a requested ISO time to a WMS layer's advertised time positions.

    ``positions`` is owslib's ``layer.timepositions``: discrete instants and/or
    "start/end[/period]" ranges (GIBS advertises several ranges when a series has
    gaps). Returns a time the server actually offers — ``requested`` when it falls
    inside a range or matches an instant, else the latest available ≤ ``requested``
    (else the earliest), so the common case of a default "last 7 days" window landing
    outside a historical/monthly layer no longer raises a ServiceException.
    Falls back to ``requested`` when the layer advertises no usable times.
    """
    values = [p.strip() for p in (positions or []) if p and p.strip()]
    if not values:
        return requested
    spans = []  # instants become zero-length spans; ISO dates compare as strings
    for v in values:
        parts = v.split("/")
        spans.append((parts[0], parts[1] if len(parts) > 1 else parts[0]))
    if any(start <= requested <= end for start, end in spans):
        return requested
    earlier = [end for _, end in spans if end <= requested]
    return max(earlier) if earlier else min(start for start, _ in spans)


class WMSLayer(BaseLayer):
    """
    OGC WMS service accessed through owslib, zoom auto-derived from the bbox width.
    When ``legend`` is given ({hex_color: label}), the RGBA raster is converted
    into a categorical band carrying a ``legend`` attribute.

    Example::
        WMSLayer(
            layer_id="fuel_map",
            url="https://maps.effis.emergency.copernicus.eu/effis",
            layer_name="fuel_map",
            legend={"#FF0000": "Haute inflammabilité", "#00FF00": "Basse inflammabilité"},
        )
    """

    kind = LayerKind.RASTER

    def __init__(self, layer_id, url, layer_name, style="",
                 img_format="image/png", srs="EPSG:4326",
                 zoom: int | None = None,
                 legend: dict[str, str] | None = None,
                 time: str | None = None,
                 version: str | None = None,
                 **kwargs):
        from types import SimpleNamespace
        super().__init__(layer_id, url, **kwargs)
        self.product = SimpleNamespace(
            layer_name=layer_name,
            style=style,
            img_format=img_format,
            srs=srs,
            zoom=zoom,
            legend=legend,
            time=time,
            version=version,
        )
        self._wms_instance = None
        self._wms_lock = threading.Lock()

    @staticmethod
    def _auto_zoom(width_deg: float) -> int:
        return (1 if width_deg > 360 else 2 if width_deg > 180 else
                3 if width_deg > 80 else 4 if width_deg > 40 else
                5 if width_deg > 20 else 6 if width_deg > 10 else
                7 if width_deg > 5 else 8 if width_deg > 2 else
                9 if width_deg > 1 else 10 if width_deg > 0.5 else
                11 if width_deg > 0.2 else 12 if width_deg > 0.1 else
                13 if width_deg > 0.05 else 14 if width_deg > 0.02 else
                15 if width_deg > 0.01 else 16)

    def _get_wms(self):
        if self._wms_instance is None:
            with self._wms_lock:
                if self._wms_instance is None:
                    from owslib.wms import WebMapService
                    # Negotiate the version: many servers (e.g. IGN Géoplateforme
                    # wms-r) reject owslib's 1.1.1 default with "VERSION ... have to
                    # be 1.3.0 or empty". Try 1.3.0 first, fall back to 1.1.1. owslib
                    # swaps the bbox for yx-axis CRSs under 1.3.0, so GetMap stays
                    # correct across both versions.
                    versions = ([self.product.version] if self.product.version
                                else ["1.3.0", "1.1.1"])
                    last_err = None
                    for version in versions:
                        try:
                            self._wms_instance = WebMapService(
                                self.url, version=version, timeout=self.timeout)
                            break
                        except Exception as e:
                            last_err = e
                    if self._wms_instance is None:
                        raise last_err
        return self._wms_instance

    @retry_on_network_error()
    def _fetch_sync(self, request: GeoRequest) -> xr.DataArray:
        wms = self._get_wms()
        # A comma-separated layer_name draws several layers in one GetMap — needed for
        # scale-tiered ArcGIS services (e.g. EEA Corine) where each zoom is a separate
        # numbered layer; requesting them together renders at every scale.
        layer_names = [s.strip() for s in self.product.layer_name.split(",") if s.strip()]
        missing = [ln for ln in layer_names if ln not in wms.contents]
        if missing:
            raise ValueError(f"Layer(s) {missing} not found. Available: {list(wms.contents.keys())}")

        bbox = request.bbox
        from PIL import Image

        minx, miny, maxx, maxy = bbox
        zoom = request.zoom or self.product.zoom or self._auto_zoom(maxx - minx)
        lat_center = (miny + maxy) / 2
        res_deg = 360 / (256 * 2 ** zoom)

        sh = getattr(wms.contents[layer_names[0]], "scaleHint", None) or {}
        native = sh.get("min")
        if native and float(native) > 0 and res_deg < float(native):
            log.info(f"[{self.layer_id}] WMS zoom={zoom} capped by scaleHint min={float(native):.6g} (res_deg={res_deg:.6g} < native)")
            res_deg = float(native)

        height = max(64, round((maxy - miny) / res_deg))
        width  = max(64, round((maxx - minx) * math.cos(math.radians(lat_center)) / res_deg))
        zoom_src = "request" if request.zoom else ("layer" if self.product.zoom else "auto")
        log.info(f"[{self.layer_id}] WMS zoom={zoom} ({zoom_src}) → {width}×{height}px  res={res_deg:.6g}°/px")

        map_kwargs = dict(
            layers=layer_names,
            srs=self.product.srs,
            bbox=(minx, miny, maxx, maxy),
            size=(width, height),
            format=self.product.img_format,
            transparent=True,
        )
        if self.product.style and len(layer_names) == 1:  # a single style can't map to many layers
            map_kwargs["styles"] = [self.product.style]
        time_val = self.product.time
        if not time_val and request.datetime:
            t = request.datetime.split("/")[0]
            time_val = t if "T" in t else t + "T00:00:00Z"
        if time_val:
            try:  # snap to a time the layer actually offers (time-enabled layers)
                positions = getattr(wms.contents[layer_names[0]], "timepositions", None)
                snapped = _snap_time(positions, time_val)
            except Exception as e:
                log.debug(f"[{self.layer_id}] WMS time snap skipped: {e}")
                snapped = time_val
            if snapped != time_val:
                log.info(f"[{self.layer_id}] WMS time {time_val} unavailable → using {snapped}")
            time_val = snapped
            map_kwargs["time"] = time_val
            log.info(f"[{self.layer_id}] WMS time sent={time_val}")
        else:
            log.info(f"[{self.layer_id}] WMS no date sent (request.datetime={request.datetime!r}, self.product.time={self.product.time!r})")

        response = wms.getmap(**map_kwargs)
        img_bytes = response.read()
        if not img_bytes:
            raise ValueError(f"[{self.layer_id}] Empty WMS response (0 bytes)")

        if "tiff" in self.product.img_format.lower():
            da = rioxarray.open_rasterio(BytesIO(img_bytes))
            if self.product.legend:
                da = _apply_legend(da, self.product.legend)
            return da

        canvas = np.array(Image.open(BytesIO(img_bytes)).convert("RGBA")).transpose(2, 0, 1)
        if not canvas.any():
            log.warning(
                f"[{self.layer_id}] Fully transparent canvas — "
                "check the TIME parameter (temporal layers) and the SRS supported by the service."
            )
        x_coords = np.linspace(minx, maxx, width)
        y_coords = np.linspace(maxy, miny, height)

        if self.product.legend:
            da = _rgba_to_classes(canvas, x_coords, y_coords, self.product.legend)
        else:
            da = xr.DataArray(
                canvas, dims=["band", "y", "x"],
                coords={"band": ["red", "green", "blue", "alpha"],
                        "y": y_coords, "x": x_coords},
            )
        da = da.rio.write_crs(self.product.srs)
        return da
