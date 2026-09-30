"""WMTS provider → xarray.DataArray (owslib + legend + web mercator)."""
from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import numpy as np
import xarray as xr

from ..base import BaseLayer, LayerKind, retry_on_network_error
from ..request import GeoRequest
from ..utils import _rgba_to_classes

log = logging.getLogger(__name__)


class WMTSLayer(BaseLayer):
    """
    Downloads WMTS tiles through owslib and mosaics them into a georeferenced
    DataArray. Zoom auto-selected from the bbox width unless provided.
    When ``legend`` is given ({hex_color: label}), output is a categorical band.

    Example::
        WMTSLayer(
            layer_id="foret_ign",
            url="https://data.geopf.fr/wmts",
            layer_name="LANDCOVER.FORESTINVENTORY.V2",
            tile_matrix_set="PM",
            legend={"#6CBB3C": "Forêt feuillue", "#006400": "Forêt conifères"},
        )
    """

    kind = LayerKind.RASTER

    HALF_CIRC = 20037508.342789244

    def __init__(self, layer_id, url, layer_name,
                 tile_matrix_set="PM", fmt="image/png",
                 style="normal", zoom: int | None = None,
                 legend: dict[str, str] | None = None,
                 **kwargs):
        from types import SimpleNamespace
        super().__init__(layer_id, url, **kwargs)
        self.product = SimpleNamespace(
            layer_name=layer_name,
            tile_matrix_set=tile_matrix_set,
            fmt=fmt,
            style=style,
            zoom=zoom,
            legend=legend,
        )

    @staticmethod
    def _auto_zoom(width_deg: float) -> int:
        return (9 if width_deg > 5 else 10 if width_deg > 2 else
                11 if width_deg > 1 else 12 if width_deg > 0.5 else
                13 if width_deg > 0.2 else 14 if width_deg > 0.1 else
                15 if width_deg > 0.05 else 16)

    @staticmethod
    def _deg_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
        lat_r = math.radians(lat)
        n = 2 ** z
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
        return x, y

    @staticmethod
    def _tile_to_mercator(x: int, y: int, z: int) -> tuple[float, float]:
        half = WMTSLayer.HALF_CIRC
        tile_size_m = 2 * half / (2 ** z)
        mx = x * tile_size_m - half
        my = half - y * tile_size_m
        return mx, my

    def _select_tile_matrix_set(self, wmts) -> str:
        """A Web-Mercator TileMatrixSet valid for this layer.

        The configured set wins when the layer supports it; otherwise we pick a
        Web-Mercator-compatible one (GoogleMapsCompatible / WebMercatorQuad /
        EPSG:3857) from the layer's links, since the tile maths here are
        Mercator. Raises if the layer only offers other CRSs (e.g. EPSG:4326).
        """
        layer = wmts.contents[self.product.layer_name]
        links = list(getattr(layer, "tilematrixsetlinks", {}) or {})
        if not links:
            links = list(getattr(layer, "tilematrixsets", []) or [])
        if self.product.tile_matrix_set in links:
            return self.product.tile_matrix_set

        def _is_mercator(name: str) -> bool:
            crs = (getattr(wmts.tilematrixsets.get(name), "crs", "") or "").lower()
            n = name.lower()
            return ("3857" in crs or "900913" in crs or "googlemapscompatible" in n
                    or "webmercator" in n or name == "PM")

        for name in links:
            if _is_mercator(name):
                if name != self.product.tile_matrix_set:
                    log.info(f"[{self.layer_id}] TileMatrixSet '{self.product.tile_matrix_set}' "
                             f"invalid for this layer — using '{name}'")
                return name
        raise ValueError(
            f"[{self.layer_id}] no Web-Mercator TileMatrixSet for layer "
            f"'{self.product.layer_name}' (available: {links}). This WMTS reader "
            f"only supports Web-Mercator tiling."
        )

    def _matrix_levels(self, wmts, tms: str) -> list[int]:
        """Numeric zoom levels available in ``tms`` (e.g. GIBS offers only 0..9)."""
        out = []
        try:
            for m in wmts.tilematrixsets[tms].tilematrix.keys():
                mm = re.search(r"\d+$", m.split(":")[-1])
                if mm:
                    out.append(int(mm.group()))
        except Exception:
            pass
        return sorted(out)

    def _matrix_id(self, wmts, tms: str, zoom: int) -> str:
        """The tilematrix identifier for ``zoom`` within ``tms`` (ids vary by server)."""
        try:
            matrices = list(wmts.tilematrixsets[tms].tilematrix.keys())
        except Exception:
            return str(zoom)
        if str(zoom) in matrices:
            return str(zoom)
        for m in matrices:                       # e.g. 'GoogleMapsCompatible:6' / '...Level6'
            if m.split(":")[-1] == str(zoom) or m.endswith(str(zoom)):
                return m
        return matrices[zoom] if 0 <= zoom < len(matrices) else str(zoom)

    @retry_on_network_error()
    def _fetch_sync(self, request: GeoRequest) -> xr.DataArray:
        from owslib.wmts import WebMapTileService

        wmts = WebMapTileService(self.url)
        if self.product.layer_name not in wmts.contents:
            raise ValueError(f"Layer '{self.product.layer_name}' not found. Available: {list(wmts.contents.keys())}")

        tms = self._select_tile_matrix_set(wmts)
        # Honour the layer's advertised formats (GIBS true-colour is JPEG, not PNG).
        formats = list(getattr(wmts.contents[self.product.layer_name], "formats", []) or [])
        fmt = self.product.fmt if self.product.fmt in formats else (formats[0] if formats else self.product.fmt)

        bbox = request.bbox
        from PIL import Image
        assert bbox is not None

        minx, miny, maxx, maxy = bbox
        zoom = self.product.zoom or self._auto_zoom(maxx - minx)
        levels = self._matrix_levels(wmts, tms)
        if levels:  # clamp to what the layer offers (GIBS true-colour caps at 9)
            zoom = min(max(zoom, levels[0]), levels[-1])
        matrix_id = self._matrix_id(wmts, tms, zoom)
        log.info(f"[{self.layer_id}] WMTS zoom={zoom} tms={tms} matrix={matrix_id} fmt={fmt}")

        x_min, y_max_tile = self._deg_to_tile(minx, miny, zoom)
        x_max, y_min_tile = self._deg_to_tile(maxx, maxy, zoom)

        mx_tl, my_tl = self._tile_to_mercator(x_min, y_min_tile, zoom)
        mx_br, my_br = self._tile_to_mercator(x_max + 1, y_max_tile + 1, zoom)

        nx = x_max - x_min + 1
        ny = y_max_tile - y_min_tile + 1
        TILE = 256
        log.info(f"[{self.layer_id}] WMTS {nx}×{ny} tiles at zoom {zoom}")

        canvas = np.zeros((4, ny * TILE, nx * TILE), dtype=np.uint8)
        tiles = [
            (ix, tx, iy, ty)
            for ix, tx in enumerate(range(x_min, x_max + 1))
            for iy, ty in enumerate(range(y_min_tile, y_max_tile + 1))
        ]

        @retry_on_network_error(retries=3, delay=1.0)
        def _get_tile(args):
            ix, tx, iy, ty = args
            resp = wmts.gettile(
                layer=self.product.layer_name,
                tilematrixset=tms,
                tilematrix=matrix_id,
                row=ty, column=tx,
                format=fmt,
            )
            img = Image.open(BytesIO(resp.read())).convert("RGBA")
            return ix, iy, np.array(img).transpose(2, 0, 1)

        from concurrent.futures import as_completed
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_get_tile, t): t for t in tiles}
            done = 0
            for fut in as_completed(futures):
                ix, iy, arr = fut.result()
                canvas[:, iy * TILE:(iy + 1) * TILE, ix * TILE:(ix + 1) * TILE] = arr
                done += 1
                if done % max(1, len(tiles) // 5) == 0 or done == len(tiles):
                    log.info(f"[{self.layer_id}] WMTS {done}/{len(tiles)} tiles")

        height, width = ny * TILE, nx * TILE
        x_coords = np.linspace(mx_tl, mx_br, width, endpoint=False) + (mx_br - mx_tl) / (2 * width)
        y_coords = np.linspace(my_tl, my_br, height, endpoint=False) + (my_br - my_tl) / (2 * height)

        if self.product.legend:
            da = _rgba_to_classes(canvas, x_coords, y_coords, self.product.legend)
        else:
            for style_val in wmts.contents[self.product.layer_name].styles.values():
                urls = style_val.get("legendURLs") or style_val.get("legend", [])
                if urls:
                    legend_url = urls if isinstance(urls, str) else urls[0]
                    log.warning(
                        f"[{self.layer_id}] No legend configured. "
                        f"Hint: {legend_url}"
                    )
                    break
            da = xr.DataArray(
                canvas, dims=["band", "y", "x"],
                coords={"band": ["red", "green", "blue", "alpha"],
                        "y": y_coords, "x": x_coords},
            )
        da = da.rio.write_crs("EPSG:3857")
        return da
