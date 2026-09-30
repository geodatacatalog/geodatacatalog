"""Overture Maps (vector tiles) plugin — fast bbox extraction from the official
Overture **PMTiles**, decoded to a ``GeoDataFrame``.

Why this is fast (and the default): the PMTiles archives are a pre-built, spatially
indexed pyramid, so only the few tiles covering the request bbox are fetched (HTTP
range reads) — no global scan like the GeoParquet path ([overture.py](overture.py)).

Trade-offs vs the GeoParquet path (use that when they matter):
- geometry is **generalised/quantised** to the tile grid (not exact coordinates);
- features that span several tiles are **re-merged by ``id``** (best-effort: parts
  unioned, contiguous line pieces stitched), but the geometry stays the tiled one;
- only the **tiled attribute subset** is present, not every GeoParquet column.

``productType`` selects the theme (same names as overture.py): ``places`` ·
``buildings`` · ``transportation`` · ``divisions`` · ``addresses`` · ``land`` ·
``water`` · ``infrastructure``.

catalog.yml::

    overture:
      search:
        type: overture_tiles.py
        # release: 2026-06-17.0     # optional; defaults to a pinned release,
        #                           # auto-upgraded to the latest if it is gone
      products:
        transportation: { productType: transportation }

Requires ``pmtiles``, ``mapbox-vector-tile`` and ``mercantile`` (``pip install
geodatacatalog[overture]``).
"""
from __future__ import annotations

import gzip
import logging

import geopandas as gpd
import numpy as np

# Absolute (not relative) import: the plugin loader execs this file as a top-level
# module with no parent package, so `from .overture import …` would fail.
from geodatacatalog.layers.plugins.overture import _ALIASES, _PRODUCTS

log = logging.getLogger(__name__)

#: Official Overture vector-tile PMTiles, one archive per theme and release,
#: served from tiles.overturemaps.org (the old ``overturemaps-tiles-…-beta`` S3
#: bucket was shut down — ``AllAccessDisabled``). Only the last couple of
#: releases are kept online, so a pinned release WILL eventually 404: ``fetch``
#: then resolves the current one from the Overture STAC root (the ``child`` link
#: flagged ``latest: true``) and retries. Override per layer with
#: ``search.release`` if you need a specific (still-hosted) release.
_TILES_BASE = "https://tiles.overturemaps.org"
_STAC_ROOT = "https://stac.overturemaps.org/catalog.json"
_TILES_FALLBACK_RELEASE = "2026-06-17.0"
#: Cap how many tiles we read: drop to a coarser zoom if the bbox needs more.
_MAX_TILES = 64


def _pmtiles_url(release: str, theme: str) -> str:
    return f"{_TILES_BASE}/{release}/{theme}.pmtiles"


_latest_release: str | None = None


def _resolve_latest_release(timeout: int = 15) -> str | None:
    """Current release id from the Overture STAC root, or None if unreachable."""
    global _latest_release
    if _latest_release:
        return _latest_release
    import requests

    try:
        catalog = requests.get(_STAC_ROOT, timeout=timeout).json()
        for link in catalog.get("links", []):
            if link.get("rel") == "child" and link.get("latest") is True:
                # href is "./<release>/catalog.json"
                _latest_release = link["href"].strip("./").split("/")[0]
                return _latest_release
    except Exception as e:
        log.warning(f"[overture_tiles] could not resolve latest release: {e}")
    return None


def _http_get_bytes(url: str):
    """A byte-range reader for the pmtiles ``Reader`` (only the bytes it asks for)."""
    import requests

    def get(offset: int, length: int) -> bytes:
        resp = requests.get(url, headers={"Range": f"bytes={offset}-{offset + length - 1}"},
                            timeout=30)
        resp.raise_for_status()
        return resp.content

    return get


def _pick_zoom(bbox, maxzoom: int) -> tuple[int, list]:
    """Highest zoom ≤ ``maxzoom`` whose bbox tile count stays under ``_MAX_TILES``."""
    import mercantile

    minx, miny, maxx, maxy = bbox
    for z in range(maxzoom, -1, -1):
        tiles = list(mercantile.tiles(minx, miny, maxx, maxy, z))
        if len(tiles) <= _MAX_TILES:
            return z, tiles
    return 0, list(mercantile.tiles(minx, miny, maxx, maxy, 0))


def _to_lnglat(coords, bounds, extent: int, transformer):
    """Map MVT tile-local coords (y down, [0, extent]) → EPSG:4326 lon/lat.

    ``bounds`` is the tile's Web-Mercator extent ``(left, bottom, right, top)``.
    """
    left, bottom, right, top = bounds
    mx = left + (coords[:, 0] / extent) * (right - left)
    my = top - (coords[:, 1] / extent) * (top - bottom)
    lng, lat = transformer.transform(mx, my)
    return np.column_stack([lng, lat])


def fetch(request, product_type, config):
    """Read Overture ``product_type`` features within ``request.bbox`` from PMTiles."""
    import mapbox_vector_tile
    import mercantile
    from pmtiles.reader import Reader
    from pyproj import Transformer
    from shapely import transform as shp_transform
    from shapely.geometry import box, shape

    key = _ALIASES.get(product_type, product_type)
    if key not in _PRODUCTS:
        raise ValueError(f"unknown Overture productType {product_type!r}; "
                         f"choose from {sorted(_PRODUCTS)}")
    theme, otype, _extra = _PRODUCTS[key]   # theme → PMTiles file, otype → MVT layer

    search = config.get("search") or {}
    release = search.get("release") or _TILES_FALLBACK_RELEASE
    limit = int(search.get("max_features") or getattr(request, "max_features", None) or 50000)
    minx, miny, maxx, maxy = request.bbox

    import requests

    url = _pmtiles_url(release, theme)
    reader = Reader(_http_get_bytes(url))
    try:
        maxzoom = reader.header()["max_zoom"]
    except requests.exceptions.HTTPError as e:
        # 403/404 = release no longer hosted (only the last few are kept):
        # fall forward to the current release advertised by the STAC root.
        status = e.response.status_code if e.response is not None else None
        latest = _resolve_latest_release() if status in (403, 404) else None
        if not latest or latest == release:
            raise
        log.warning(f"[overture_tiles] release {release!r} is gone (HTTP {status}) "
                    f"— retrying with latest release {latest!r}")
        release = latest
        url = _pmtiles_url(release, theme)
        reader = Reader(_http_get_bytes(url))
        maxzoom = reader.header()["max_zoom"]
    zoom, tiles = _pick_zoom(request.bbox, maxzoom)
    log.info("[overture_tiles] %s @ z%d (%d tile(s)) from %s", key, zoom, len(tiles), url)

    transformer = Transformer.from_crs(3857, 4326, always_xy=True)
    records: list[dict] = []
    for tile in tiles:
        raw = reader.get(tile.z, tile.x, tile.y)
        if not raw:
            continue
        if raw[:2] == b"\x1f\x8b":                 # gzip-compressed tile payload
            raw = gzip.decompress(raw)
        decoded = mapbox_vector_tile.decode(raw, y_coord_down=True)
        layers = [decoded[otype]] if otype in decoded else list(decoded.values())
        bounds = tuple(mercantile.xy_bounds(tile))
        for layer in layers:
            extent = layer["extent"]
            for feat in layer["features"]:
                rec = dict(feat.get("properties") or {})
                # the Overture GERS id can be the MVT feature id rather than a
                # property — keep it so we can re-merge pieces split across tiles
                if "id" not in rec and feat.get("id") is not None:
                    rec["id"] = feat["id"]
                rec["geometry"] = shp_transform(
                    shape(feat["geometry"]),
                    lambda c, b=bounds, e=extent: _to_lnglat(c, b, e, transformer))
                records.append(rec)
            if len(records) >= limit:
                break
        if len(records) >= limit:
            break

    if not records:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    gdf = gpd.GeoDataFrame(records[:limit], geometry="geometry", crs="EPSG:4326")
    # A feature spanning several tiles is emitted once per tile, clipped at the
    # border and sharing the same `id` → merge those pieces back into one feature
    # (union the parts; stitch contiguous line pieces that meet at the tile edge).
    if "id" in gdf.columns and gdf["id"].duplicated().any():
        from shapely import line_merge
        gdf = gdf.dissolve(by="id", aggfunc="first", sort=False).reset_index()
        gdf["geometry"] = [line_merge(g) if g.geom_type == "MultiLineString" else g
                           for g in gdf.geometry]
    # Tiles overshoot the bbox; keep only features that reach the requested rectangle.
    gdf = gdf[gdf.intersects(box(minx, miny, maxx, maxy))].reset_index(drop=True)
    log.info("[overture_tiles] %d %s feature(s) (release %s) in %s",
             len(gdf), key, release, request.bbox)
    if request.crs and request.crs != "EPSG:4326" and not gdf.empty:
        gdf = gdf.to_crs(request.crs)
    return gdf
