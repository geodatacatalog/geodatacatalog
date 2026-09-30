"""Generic XYZ tile provider → xarray.DataArray (slippy-map tiles, web mercator).

Mosaics ``{z}/{x}/{y}`` tiles from a template URL into a georeferenced
DataArray, like :class:`WMTSLayer` but without capabilities. The template may
carry extra placeholders resolved at fetch time from a JSON *manifest* — e.g.
RainViewer's ``weather-maps.json``, whose timestamped frames build the tile
path: ``{host}{frame.path}/256/{z}/{x}/{y}/2/1_1.png``.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import numpy as np
import xarray as xr

from ..base import BaseLayer, LayerKind, retry_on_network_error
from ..request import GeoRequest
from ..utils import _rgba_to_classes
from .wmts import WMTSLayer

log = logging.getLogger(__name__)

USER_AGENT = "geodatacatalog (XYZ tile fetcher)"

#: template placeholders filled per tile, not from the manifest
_TILE_PLACEHOLDERS = {"z", "x", "y", "-y", "s", "r", "product"}


def _dig(doc, path: str):
    """Resolve a dotted path (``radar.past``, ``frame.0.path``) in JSON data."""
    cur = doc
    for part in path.split("."):
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


class XYZLayer(BaseLayer):
    """
    Downloads XYZ tiles and mosaics them into a georeferenced DataArray
    (EPSG:3857). Zoom auto-selected from the bbox width unless provided.
    When ``legend`` is given ({hex_color: label}), output is a categorical band.

    The template is ``url_template`` (default: the endpoint itself, for plain
    tile URLs like ``https://tile.example.org/{z}/{x}/{y}.png``). Placeholders
    other than ``{z}/{x}/{y}`` (plus ``{-y}``, ``{s}``, ``{r}``, ``{product}``)
    are resolved from a JSON ``manifest``: ``{host}`` reads the manifest's
    ``host`` key, ``{frame.path}`` reads ``path`` in the frame chosen from the
    ``frames`` list — the one nearest the requested datetime, else the newest.

    Example (RainViewer)::
        XYZLayer(
            layer_id="rainviewer__radar",
            url="https://api.rainviewer.com/public",
            url_template="{host}{frame.path}/256/{z}/{x}/{y}/2/1_1.png",
            manifest="https://api.rainviewer.com/public/weather-maps.json",
            frames="radar.past",
        )
    """

    kind = LayerKind.RASTER

    def __init__(self, layer_id, url, url_template: str | None = None,
                 manifest: str | None = None, frames: str | None = None,
                 frame_time: str = "time", product_type: str | None = None,
                 zoom: int | None = None, max_zoom: int | None = None,
                 tile_size: int = 256, subdomains: list[str] | str | None = None,
                 legend: dict[str, str] | None = None, **kwargs):
        from types import SimpleNamespace
        super().__init__(layer_id, url, **kwargs)
        self.product = SimpleNamespace(
            url_template=url_template or url,
            manifest=manifest,
            frames=frames,
            frame_time=frame_time,
            product_type=product_type,
            zoom=zoom,
            max_zoom=max_zoom,
            tile_size=int(tile_size or 256),
            subdomains=list(subdomains) if subdomains else ["a", "b", "c"],
            legend=legend,
        )

    # ------------------------------------------------------------------ template

    @retry_on_network_error(retries=3, delay=1.0)
    def _manifest_doc(self) -> dict:
        import requests

        url = self.product.manifest
        if not url.startswith(("http://", "https://")):
            url = f"{self.url.rstrip('/')}/{url.lstrip('/')}"
        resp = requests.get(url, timeout=self.timeout,
                            headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _parse_time(value):
        """Epoch seconds or ISO date/datetime → aware UTC datetime."""
        from datetime import datetime, timezone
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _select_frame(self, doc, datetime_str: str | None) -> dict:
        frames = _dig(doc, self.product.frames)
        if not frames:
            raise ValueError(
                f"[{self.layer_id}] manifest has no frames under "
                f"{self.product.frames!r} — no tiles to fetch right now")
        if not datetime_str:
            return frames[-1]
        # end of the requested window; a bare date means "up to that day", so the
        # default last-7-days window still lands on the newest frame
        target_str = datetime_str.split("/")[-1].strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_str):
            target_str += "T23:59:59+00:00"
        try:
            target = self._parse_time(target_str)
        except (ValueError, TypeError):
            return frames[-1]
        key = self.product.frame_time
        best = min(frames, key=lambda f: abs(self._parse_time(f[key]) - target))
        log.info(f"[{self.layer_id}] frame {best.get(key)} selected for "
                 f"datetime={datetime_str}")
        return best

    def tile_url_template(self, datetime: str | None = None) -> str:
        """The XYZ template with every non-tile placeholder resolved.

        ``{product}`` takes the productType; the rest come from the manifest
        (``{frame.<key>}`` from the frame chosen for ``datetime``). The result
        still holds ``{z}/{x}/{y}`` — ready for a map client or ``_fetch_sync``.
        """
        template = self.product.url_template
        if "{product}" in template:
            template = template.replace("{product}", str(self.product.product_type or ""))
        names = set(re.findall(r"\{([^{}]+)\}", template)) - _TILE_PLACEHOLDERS
        if names:
            if not self.product.manifest:
                raise ValueError(
                    f"[{self.layer_id}] template holds placeholders {sorted(names)} "
                    f"but no `manifest` is configured to resolve them")
            doc = self._manifest_doc()
            frame = self._select_frame(doc, datetime) if self.product.frames else None
            for name in names:
                value = _dig(frame, name[6:]) if name.startswith("frame.") else _dig(doc, name)
                template = template.replace("{" + name + "}", str(value))
        if not all(p in template for p in ("{z}", "{x}", "{y}")):
            raise ValueError(
                f"[{self.layer_id}] not an XYZ template (missing {{z}}/{{x}}/{{y}}): "
                f"{template}")
        return template

    def _tile_url(self, template: str, zoom: int, tx: int, ty: int) -> str:
        url = (template.replace("{z}", str(zoom))
               .replace("{x}", str(tx)).replace("{y}", str(ty)))
        if "{-y}" in url:                       # TMS flipped-y scheme
            url = url.replace("{-y}", str(2 ** zoom - 1 - ty))
        if "{s}" in url:
            subs = self.product.subdomains
            url = url.replace("{s}", subs[(tx + ty) % len(subs)])
        if "{r}" in url:                        # leaflet retina suffix
            url = url.replace("{r}", "")
        return url

    # --------------------------------------------------------------------- fetch

    @retry_on_network_error()
    def _fetch_sync(self, request: GeoRequest) -> xr.DataArray:
        import requests
        from PIL import Image

        assert request.bbox is not None
        minx, miny, maxx, maxy = request.bbox
        template = self.tile_url_template(request.datetime)

        zoom = request.zoom or self.product.zoom or WMTSLayer._auto_zoom(maxx - minx)
        if self.product.max_zoom is not None:
            zoom = min(zoom, int(self.product.max_zoom))
        n = 2 ** zoom
        # web-mercator validity bounds; out-of-range tiles simply don't exist
        lat_lo, lat_hi = max(miny, -85.0511), min(maxy, 85.0511)
        x_min, y_max_tile = WMTSLayer._deg_to_tile(minx, lat_lo, zoom)
        x_max, y_min_tile = WMTSLayer._deg_to_tile(maxx, lat_hi, zoom)
        x_min, x_max = max(0, x_min), min(n - 1, x_max)
        y_min_tile, y_max_tile = max(0, y_min_tile), min(n - 1, y_max_tile)

        mx_tl, my_tl = WMTSLayer._tile_to_mercator(x_min, y_min_tile, zoom)
        mx_br, my_br = WMTSLayer._tile_to_mercator(x_max + 1, y_max_tile + 1, zoom)

        nx, ny = x_max - x_min + 1, y_max_tile - y_min_tile + 1
        log.info(f"[{self.layer_id}] XYZ {nx}×{ny} tiles at zoom {zoom}")

        tiles = [
            (ix, tx, iy, ty)
            for ix, tx in enumerate(range(x_min, x_max + 1))
            for iy, ty in enumerate(range(y_min_tile, y_max_tile + 1))
        ]

        errors: list[str] = []

        @retry_on_network_error(retries=3, delay=1.0)
        def _get_tile(args):
            ix, tx, iy, ty = args
            resp = requests.get(self._tile_url(template, zoom, tx, ty),
                                timeout=self.timeout,
                                headers={"User-Agent": USER_AGENT})
            if resp.status_code != 200:         # no tile here → stay transparent
                return ix, iy, None, f"HTTP {resp.status_code}"
            try:
                img = Image.open(BytesIO(resp.content)).convert("RGBA")
            except Exception as e:
                return ix, iy, None, f"{type(e).__name__}: {e}"
            return ix, iy, np.array(img).transpose(2, 0, 1), None

        fetched: dict[tuple[int, int], np.ndarray] = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_get_tile, t): t for t in tiles}
            for fut in as_completed(futures):
                ix, iy, arr, err = fut.result()
                if arr is None:
                    errors.append(err)
                    continue
                fetched[(ix, iy)] = arr
                if len(fetched) % max(1, len(tiles) // 5) == 0 or len(fetched) == len(tiles):
                    log.info(f"[{self.layer_id}] XYZ {len(fetched)}/{len(tiles)} tiles")

        if not fetched:
            raise RuntimeError(
                f"[{self.layer_id}] all {len(tiles)} tiles failed "
                f"(e.g. {errors[0] if errors else '?'}) — wrong template or zoom?")
        if errors:
            log.warning(f"[{self.layer_id}] {len(errors)}/{len(tiles)} tiles "
                        f"missing (left transparent), e.g. {errors[0]}")

        # the real tile size comes from the pixels, not the config — some servers
        # serve 512 px tiles behind a plain template (e.g. OpenRailwayMap)
        sizes = {a.shape[1] for a in fetched.values()} | {a.shape[2] for a in fetched.values()}
        if len(sizes) > 1:
            raise ValueError(f"[{self.layer_id}] mixed tile sizes {sorted(sizes)}px")
        TILE = sizes.pop()
        if TILE != self.product.tile_size:
            log.info(f"[{self.layer_id}] {TILE}px tiles detected "
                     f"(tileSize={self.product.tile_size} in config)")

        canvas = np.zeros((4, ny * TILE, nx * TILE), dtype=np.uint8)
        for (ix, iy), arr in fetched.items():
            canvas[:, iy * TILE:(iy + 1) * TILE, ix * TILE:(ix + 1) * TILE] = arr

        height, width = ny * TILE, nx * TILE
        x_coords = np.linspace(mx_tl, mx_br, width, endpoint=False) + (mx_br - mx_tl) / (2 * width)
        y_coords = np.linspace(my_tl, my_br, height, endpoint=False) + (my_br - my_tl) / (2 * height)

        if self.product.legend:
            da = _rgba_to_classes(canvas, x_coords, y_coords, self.product.legend)
        else:
            da = xr.DataArray(
                canvas, dims=["band", "y", "x"],
                coords={"band": ["red", "green", "blue", "alpha"],
                        "y": y_coords, "x": x_coords},
            )
        return da.rio.write_crs("EPSG:3857")
