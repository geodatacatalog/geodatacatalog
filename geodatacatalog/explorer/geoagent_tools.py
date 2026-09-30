"""GeoAgent tool pack — geodatacatalog as tools for opengeos/GeoAgent (Strands).

Wraps the non-interactive explorer surface (registry search, layer listing, CSW
discovery, geocoding, catalog.yml building, fetch/export) as ``@geo_tool``
functions so any GeoAgent integration (leafmap, browser MapLibre, QGIS) can
search sources, build a justified catalog.yml and fetch data from it.

Usage::

    from geodatacatalog.explorer.geoagent_tools import geodatacatalog_tools, make_agent

    agent = make_agent()                      # LiteLLM/Mistral by default
    agent.chat("find a source with French rivers, add it to my catalog ...")

    # or bind to an existing GeoAgent integration:
    from geoagent import for_leafmap
    agent = for_leafmap(m, extra_tools=geodatacatalog_tools())

Requires the ``geodatacatalog[geoagent]`` extra (``pip install
geodatacatalog[geoagent]``).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("geodatacatalog")

#: Caps for the GeoJSON preview returned to the agent (the full, uncapped data
#: lives in the exported files). The preview crosses the LLM context TWICE —
#: once as the tool result, once echoed by the model into add_geojson_data —
#: so it must stay small: ~150 KB ≈ 40k tokens per crossing. Dense polygon
#: layers (e.g. BD TOPO buildings) blow a 262k context at 1 MB.
PREVIEW_MAX_FEATURES = 200
PREVIEW_MAX_BYTES = 150_000
PREVIEW_MAX_COLUMNS = 12

# Mistral Small (4): far cheaper than medium and good enough for this compact
# tool set; override with LITELLM_MODEL or --model (e.g. a dated id like
# mistral/mistral-small-2603, or medium for tougher chains).
_DEFAULT_LITELLM_MODEL = "mistral/mistral-small-latest"

SYSTEM_PROMPT = """You help users discover, catalog and fetch open geospatial data
(mostly French and European) through the geodatacatalog registry.

Typical workflow — chain the tools as needed in a single request:
1. search_data_sources to find candidate sources (registry of curated services);
2. list_source_layers to inspect a source's layers (or discover_meta_catalogue
   for CSW meta-catalogues);
3. geocode_aoi to resolve the user's area of interest to a real bbox — never
   invent coordinates;
4. propose_layers to persist chosen layers into the user's catalog.yml —
   always give an honest, specific `reason` explaining why this source/layer
   answers the request;
5. fetch_catalog_layers to download data over the AOI — when the user asks for
   a specific named feature (a river, a city...), keep only it with
   attribute_filters on the name-ish column (e.g. {"toponyme": "garonne"},
   {"name": "ganges"});
6. when map tools are available, display the result: a `preview_url` goes to
   add_vector_data(url=...), an inline `preview` goes to add_geojson_data;
   to show a raster/visualisation layer (WMS/WMTS/XYZ — fire danger, weather,
   ortho...), call preview_source_layer and pass its `tile_url` VERBATIM to
   add_xyz_tile_layer — NEVER hand-build a GetMap/tile URL and never invent a
   tile endpoint: if a source is in neither the registry nor a meta-catalogue,
   say so instead; to show satellite imagery from a STAC fetch, use the
   `quicklooks` returned by fetch_catalog_layers with add_image_overlay
   (url + bounds copied verbatim), or render_stac_imagery for the real
   full-resolution pixels (downloads the COGs, returns an image_url +
   bounds for add_image_overlay);
7. for geoprocessing, use the dedicated tools (e.g. buffer_layer for a buffer
   around a fetched layer) — never fake a spatial operation by re-displaying
   the same data.

For satellite imagery of a date or period ("the least cloudy Sentinel-2
image of last week"...), do NOT hunt for a WMS/tile service or search a
meta-catalogue: use a STAC source from search_data_sources (type "stac",
e.g. a sentinel-2-l2a collection), add it with propose_layers, then
fetch_catalog_layers with datetime="YYYY-MM-DD/YYYY-MM-DD" (resolve relative
periods against the current date) over the AOI — the report's `quicklooks`
are sorted least-cloudy first for add_image_overlay, and render_stac_imagery
renders the full-resolution pixels.

Prefer open, account-free sources. Do not stop mid-workflow to ask for
permission — the `reason` you pass to propose_layers IS the
justification; complete the whole chain in one turn, then report the catalog
path, layer ids and export paths back to the user, along with your reasons.
When a result is imperfect (a raster where vectors were expected, an empty or
failing layer), do not end by asking whether to try something else: pick the
single best alternative, try it, then report what worked and what did not.
Sources of type "registry" are meta-portals: only the CSW ones work with
discover_meta_catalogue; the others cannot be listed, added or fetched — if
one rejects a tool call, move on to a different source instead of retrying
it with other tools."""


def _require_geoagent():
    try:
        import geoagent  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise ImportError(
            "The GeoAgent tool pack needs the 'geoagent' package. "
            "Install it with: pip install geodatacatalog[geoagent]"
        ) from exc
    return geoagent


class _ToolState:
    """Shared state for one tool pack instance (registry cache, workdir, AOI)."""

    def __init__(self, workdir: str | Path = ".",
                 catalog_path: str | Path | None = None,
                 sources: list | None = None,
                 preview_base_url: str | None = None):
        self.workdir = Path(workdir)
        self.catalog_path = Path(catalog_path) if catalog_path else None
        self._sources = sources
        self.preview_base_url = preview_base_url.rstrip("/") if preview_base_url else None
        self.last_aoi: dict | None = None      # last geocode_aoi hit (full geometry)
        self.last_results: dict = {}           # layer_id -> GeoDataFrame of the last fetch
        self.last_stac: dict = {}              # layer_id -> {catalog, request, items}

    @property
    def sources(self) -> list:
        if self._sources is None:
            from .registry import load_registry
            self._sources = load_registry()
        return self._sources

    def resolve_source(self, source_id: str):
        """Tolerant registry lookup — models guess ids ('geocatalogue_national'
        for 'geocatalogue_fr'); exact, then normalized, then unique substring,
        else a KeyError suggesting close matches."""
        from .registry import get_source
        try:
            return get_source(self.sources, source_id)
        except KeyError:
            import difflib
            import re
            norm = lambda s: re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
            wanted = norm(source_id)
            by_norm = {norm(s.id): s.id for s in self.sources}
            if wanted in by_norm:
                return get_source(self.sources, by_norm[wanted])
            partial = [i for n, i in by_norm.items()
                       if wanted in n or n in wanted]
            if len(partial) == 1:
                return get_source(self.sources, partial[0])
            close = difflib.get_close_matches(wanted, list(by_norm), n=3,
                                              cutoff=0.6)
            candidates = sorted({by_norm[c] for c in close} | set(partial))
            raise KeyError(
                f"unknown source id {source_id!r}"
                + (f" — did you mean one of {candidates}?" if candidates else "")
                + " Use ids exactly as returned by search_data_sources; for a"
                " service found via discover_meta_catalogue (not in the"
                " registry), pass endpoint= + service_type= instead.")

    def resolve_catalog(self, catalog_path: str | None) -> Path:
        if catalog_path:
            path = Path(catalog_path)
            return path if path.is_absolute() else self.workdir / path
        if self.catalog_path is not None:
            return self.catalog_path
        return self.workdir / "catalog.yml"


def _preview_geojson(gdf, max_features: int = PREVIEW_MAX_FEATURES,
                     max_bytes: int = PREVIEW_MAX_BYTES) -> dict | None:
    """A display-ready FeatureCollection capped in features, columns and bytes."""
    if gdf is None or getattr(gdf, "empty", True):
        return None
    preview = gdf.head(max_features).copy()
    if preview.crs is not None and str(preview.crs).upper() not in ("EPSG:4326",):
        preview = preview.to_crs("EPSG:4326")
    geom_col = preview.geometry.name
    keep = [c for c in preview.columns if c != geom_col][:PREVIEW_MAX_COLUMNS]
    preview = preview[keep + [geom_col]]
    try:                      # ~0.1 m grid: full float precision doubles the JSON
        from shapely import set_precision
        preview[geom_col] = set_precision(preview.geometry.values, grid_size=1e-6)
    except Exception:         # older shapely — precision trim is best-effort
        pass
    fc = json.loads(preview.to_json(default=str))  # e.g. Timestamp columns (SANDRE)
    while len(json.dumps(fc)) > max_bytes and len(fc.get("features", [])) > 10:
        fc["features"] = fc["features"][: max(10, len(fc["features"]) // 2)]
    # A handful of huge geometries (e.g. one dissolved buffer) can't be capped
    # by dropping features: simplify, then fall back to the envelope.
    simplified = False
    for tolerance in (1e-4, 1e-3, 1e-2):
        if len(json.dumps(fc)) <= max_bytes:
            break
        simplified = True
        preview = preview.copy()
        preview[geom_col] = preview.geometry.simplify(tolerance,
                                                      preserve_topology=True)
        fc = json.loads(preview.to_json(default=str))
    if len(json.dumps(fc)) > max_bytes:
        import geopandas as gpd
        simplified = True
        merged = (preview.union_all() if hasattr(preview, "union_all")
                  else preview.unary_union)
        envelope = gpd.GeoSeries([merged.envelope], crs=preview.crs)
        fc = json.loads(envelope.to_json(default=str))
        fc["features"][0]["properties"] = {
            "note": "geometry too large for an inline preview — envelope only; "
                    "the exported file holds the real shape"}
    fc["properties"] = {"truncated": simplified
                        or len(fc.get("features", [])) < len(gdf),
                        "total_features": int(len(gdf))}
    return fc


def _served_url(state, path) -> str | None:
    """The ``/files/`` URL a browser can fetch for a workdir file, or None
    (no file server around, or the file lives outside the served tree)."""
    if state.preview_base_url is None:
        return None
    from urllib.parse import quote
    try:
        rel = Path(path).resolve().relative_to(
            state.workdir.resolve()).as_posix()
    except ValueError:
        return None
    return f"{state.preview_base_url}/files/{quote(rel)}"


def _aoi_display(state, hit: dict) -> dict[str, Any]:
    """A map-ready handle for a geocoded AOI (outline layer + served display
    file, or inline preview), so the host can offer "add the AOI to the map"."""
    import re

    geom = hit.get("geometry")
    bbox = hit.get("bbox")
    try:
        import geopandas as gpd
        from shapely.geometry import box, shape

        shp = shape(geom) if geom else (box(*bbox) if bbox else None)
    except Exception:
        return {}
    if shp is None:
        return {}
    head = (hit.get("display_name") or "aoi").split(",")[0]
    slug = re.sub(r"[^a-z0-9]+", "_", head.lower()).strip("_") or "aoi"
    layer = f"aoi_{slug}"
    gdf = gpd.GeoDataFrame({"name": [hit.get("display_name") or head]},
                           geometry=[shp], crs="EPSG:4326")
    out: dict[str, Any] = {"aoi_layer": layer}
    url = None
    if state.preview_base_url is not None:
        export_dir = state.workdir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        path = export_dir / f"{layer}.display.geojson"
        gdf.to_file(path, driver="GeoJSON")
        url = _served_url(state, path)
    if url:
        out["preview_url"] = url
        out["note"] = ("the AOI outline can be shown with add_vector_data("
                       "url=preview_url, name=aoi_layer); hosts with a "
                       "timeline offer the user an add button instead — do "
                       "not insist if the layer does not appear")
    else:
        out["preview"] = _preview_geojson(gdf)
    return out


def _apply_attribute_filters(gdf, filters: dict[str, Any]):
    """Case-insensitive equality-or-contains filter on GeoDataFrame columns.

    Column resolution is tolerant — models guess names ('toponyme', 'nom')
    that rarely match the service's schema ('TopoOH',
    'cpx_toponyme_d_entite_de_bassin'): exact, then case-insensitive, then
    unique substring match; anything ambiguous fails with the candidates.
    """
    for column, value in filters.items():
        if column not in gdf.columns:
            wanted = str(column).lower()
            exact = [c for c in gdf.columns if c.lower() == wanted]
            fuzzy = [c for c in gdf.columns
                     if wanted in c.lower() or c.lower() in wanted]
            if exact:
                column = exact[0]
            elif len(fuzzy) == 1:
                column = fuzzy[0]
            elif len(fuzzy) > 1:
                raise KeyError(
                    f"column {column!r} is ambiguous: {sorted(fuzzy)} — pick one")
            else:
                raise KeyError(
                    f"column {column!r} not found; available: {sorted(gdf.columns)}")
        series = gdf[column].astype(str).str.lower()
        gdf = gdf[series.str.contains(str(value).lower(), regex=False, na=False)]
    return gdf


#: Web-Mercator half-extent (EPSG:3857), metres.
_MERCATOR_LIMIT = 20037508.342789244


def _tile_indices(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Slippy-map tile column/row containing a lon/lat point."""
    import math

    n = 2 ** z
    lat = max(-85.05112878, min(85.05112878, lat))
    x = int((lon + 180.0) / 360.0 * n) % n
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, min(max(y, 0), n - 1)


def _bbox_3857(z: int, x: int, y: int) -> str:
    """The EPSG:3857 "minx,miny,maxx,maxy" bbox of a slippy tile."""
    size = 2 * _MERCATOR_LIMIT / (2 ** z)
    minx = -_MERCATOR_LIMIT + x * size
    maxy = _MERCATOR_LIMIT - y * size
    return f"{minx:.6f},{maxy - size:.6f},{minx + size:.6f},{maxy:.6f}"


def _fill_tile_template(template: str, lon: float, lat: float, z: int = 6) -> str:
    """One concrete tile URL from a MapLibre raster template ({z}/{x}/{y} or
    WMS {bbox-epsg-3857})."""
    x, y = _tile_indices(lon, lat, z)
    return (template.replace("{s}", "a")
            .replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
            .replace("{bbox-epsg-3857}", _bbox_3857(z, x, y)))


def _probe_tile(template: str, lon: float, lat: float, z: int = 6) -> bytes:
    """Fetch one real tile and fail with the server's message unless it is an
    image — an unverified template dies later as per-tile MapLibre decode
    errors the agent never sees (e.g. a MapServer XML ServiceException).

    One retry on transient errors: EFFIS/GWIS intermittently answer 503 (same
    campaign quirk as the listing retry in list_source_layers), and a single
    hiccup here must not read as "this layer cannot be displayed" — that is
    what sends models off to invent tile URLs on other endpoints."""
    import time as _time

    import requests

    from .probe import USER_AGENT

    url = _fill_tile_template(template, lon, lat, z)
    for attempt in (1, 2):
        try:
            resp = requests.get(url, timeout=60,
                                headers={"User-Agent": USER_AGENT})
        except requests.RequestException as exc:
            if attempt == 1:
                _time.sleep(2)
                continue
            raise ValueError(f"tile probe failed on {url}: {exc}. If the "
                             "service is temporarily down, tell the user and "
                             "retry later — never hand-build a tile URL on "
                             "another endpoint instead.") from exc
        ctype = resp.headers.get("content-type", "")
        if resp.ok and ctype.lower().startswith("image/"):
            return resp.content
        if resp.status_code in (500, 502, 503, 504) and attempt == 1:
            _time.sleep(2)
            continue
        body = " ".join(resp.text[:300].split())
        raise ValueError(
            f"tile probe failed (HTTP {resp.status_code}, "
            f"{ctype or 'no content-type'}) on {url}"
            + (f" — server said: {body}" if body else "")
            + (". Temporary server error: tell the user and retry later — "
               "never hand-build a tile URL on another endpoint instead."
               if resp.status_code in (500, 502, 503, 504) else ""))
    raise AssertionError("unreachable")  # pragma: no cover


def _tile_has_content(image_bytes: bytes) -> bool | None:
    """Whether a probed tile draws anything (any non-transparent pixel), or
    ``None`` when Pillow is unavailable. A 200 image/png that is 100%%
    transparent is how a WMS answers "no data for this TIME" — e.g. EFFIS
    time-dimensioned layers silently render their stale default date blank."""
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:  # pragma: no cover - pillow ships with the geo stack
        return None
    try:
        alpha = Image.open(BytesIO(image_bytes)).convert("RGBA").getchannel("A")
        return alpha.getextrema()[1] > 0
    except Exception:
        return None


def _verified_temporal_wms(template: str, time: str | None, lon: float,
                           lat: float, source_id: str, layer: str, *,
                           name: str, attribution: str,
                           legend_url: str) -> dict:
    """Bake a TIME into a time-dimensioned GetMap template and verify it.

    With an explicit ``time``, probe that date. Otherwise walk back from
    today (UTC) and keep the most recent date whose probe tile draws
    something; a date that probes as an image but fully transparent means
    "no data for this TIME" on most servers. When every dated probe is
    blank, probe once WITHOUT TIME before settling: some layers declare a
    time dimension yet only draw when TIME is omitted (EFFIS hotspots
    all.hs/viirs.hs render current detections bare but empty for any
    explicit date).
    """
    import datetime as _dt

    if time:
        candidates = [str(time)]
    else:
        today = _dt.datetime.now(_dt.timezone.utc).date()
        candidates = [(today - _dt.timedelta(days=d)).isoformat()
                      for d in range(4)]
    blank_ok: tuple[str, str] | None = None   # (date, template) — image, no pixels
    last_err: Exception | None = None
    for date in candidates:
        candidate = f"{template}&TIME={date}"
        try:
            tile = _probe_tile(candidate, lon, lat)
        except ValueError as exc:
            last_err = exc
            continue
        has_content = _tile_has_content(tile)
        if has_content or has_content is None:   # None: no Pillow, trust the 200
            return {"tile_url": candidate, "name": name,
                    "attribution": attribution, "time": date,
                    "legend_url": legend_url,
                    "note": ("verified: one tile with data was fetched OK for "
                             f"TIME={date} — pass tile_url verbatim to "
                             "add_xyz_tile_layer(url=tile_url, name=name)")}
        if blank_ok is None:
            blank_ok = (date, candidate)
    if not time:
        try:
            bare = _probe_tile(template, lon, lat)
        except ValueError as exc:
            last_err = exc
        else:
            if _tile_has_content(bare):
                return {"tile_url": template, "name": name,
                        "attribution": attribution,
                        "legend_url": legend_url,
                        "note": ("verified: every dated probe rendered blank "
                                 "but the layer draws WITHOUT TIME — this "
                                 "server always serves its current data when "
                                 "TIME is omitted (e.g. EFFIS hotspots), so "
                                 "tile_url has no TIME baked in; pass it "
                                 "verbatim to add_xyz_tile_layer")}
    if blank_ok:
        date, candidate = blank_ok
        today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
        return {"tile_url": candidate, "name": name,
                "attribution": attribution, "time": date,
                "legend_url": legend_url,
                "note": (f"tiles answer OK for TIME={date} but render blank at "
                         "the probe location — the layer may be sparse there "
                         "(e.g. fire hotspots), or the date may be wrong "
                         f"(today UTC is {today}; a user date without a year "
                         "means the current year, not your training year): "
                         "retry with an explicit `time` if the map stays empty")}
    raise ValueError(
        f"[{source_id}] no displayable tile for {layer!r} on "
        f"{', '.join(candidates)}"
        + (f" — last error: {last_err}" if last_err else "")
        + ". Retry with an explicit `time` the layer covers.")


def _stac_footprints(data):
    """A footprint GeoDataFrame from a list of STAC items, else ``None``.

    STAC fetches return ``list[pystac.Item]`` — not displayable as such, so
    the model used to invent a preview URL for them; their footprints + key
    metadata (id, datetime, cloud cover) are what belongs on the map."""
    if not (isinstance(data, list) and data):
        return None
    feats = []
    for item in data:
        geom = getattr(item, "geometry", None)
        if not isinstance(geom, dict) or not hasattr(item, "properties"):
            return None
        props = {"id": getattr(item, "id", None),
                 "datetime": str(getattr(item, "datetime", "") or "") or None,
                 "collection": getattr(item, "collection_id", None)}
        cloud = (item.properties or {}).get("eo:cloud_cover")
        if cloud is not None:
            props["eo:cloud_cover"] = cloud
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": props})
    import geopandas as gpd

    return gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")


def _stac_quicklooks(items: list, limit: int = 3) -> list[dict]:
    """Browser-displayable quicklooks of the least-cloudy STAC items.

    Keeps only public http(s) image assets (role thumbnail/overview, e.g.
    CDSE's 343px true-colour JPEG — CORS *) — s3:// data assets need creds
    and a renderer, the browser cannot drape them."""
    ranked = sorted(items, key=lambda it: (
        (getattr(it, "properties", None) or {}).get("eo:cloud_cover")
        is None,
        (getattr(it, "properties", None) or {}).get("eo:cloud_cover", 0)))
    out = []
    for item in ranked:
        bbox = getattr(item, "bbox", None)
        assets = getattr(item, "assets", None) or {}
        if not bbox or len(bbox) != 4:
            continue
        href = None
        for key, asset in assets.items():
            roles = set(getattr(asset, "roles", None) or [])
            candidate = getattr(asset, "href", "") or ""
            if ((roles & {"thumbnail", "overview"}
                 or key in ("thumbnail", "quicklook", "preview", "rendered_preview"))
                    and candidate.startswith("http")):
                href = candidate
                break
        if not href:
            continue
        props = getattr(item, "properties", None) or {}
        out.append({"item_id": getattr(item, "id", None),
                    "datetime": str(getattr(item, "datetime", "") or "") or None,
                    "eo:cloud_cover": props.get("eo:cloud_cover"),
                    "url": href,
                    "bounds": [float(v) for v in bbox]})
        if len(out) >= limit:
            break
    return out


def _render_rgb_png(ds, path) -> list[float]:
    """Write a display PNG from a loaded raster Dataset; return WGS84 bounds.

    Per-band 2–98%% percentile stretch (robust across sensors), nodata/NaN
    → transparent. The dataset is expected in a metric CRS (EPSG:3857) so
    MapLibre's linear image warp is correct."""
    import numpy as np
    from PIL import Image
    from pyproj import Transformer

    if "time" in ds.dims:
        ds = (ds.isel(time=0) if ds.sizes["time"] == 1
              else ds.median("time", skipna=True))
    bands = list(ds.data_vars)[:3]
    if not bands:
        raise ValueError("loaded dataset has no bands to render")
    layers = []
    for name in bands:
        arr = ds[name].values.astype("float32")
        nodata = getattr(ds[name].rio, "nodata", None)
        if nodata is not None and not np.isnan(nodata):
            arr = np.where(arr == nodata, np.nan, arr)
        layers.append(np.squeeze(arr))
    if len(layers) == 1:                      # single band → grayscale
        layers = layers * 3
    valid = ~np.isnan(layers[0])
    if not valid.any():
        raise ValueError("rendered area is fully nodata — nothing to display")
    rgba = np.zeros((*layers[0].shape, 4), dtype="uint8")
    for i, arr in enumerate(layers):
        lo, hi = np.nanpercentile(arr, [2, 98])
        if not np.isfinite(hi) or hi <= lo:
            lo, hi = np.nanmin(arr), max(float(np.nanmax(arr)), lo + 1)
        rgba[..., i] = np.nan_to_num(
            np.clip((arr - lo) / (hi - lo) * 255, 0, 255)).astype("uint8")
    rgba[..., 3] = np.where(valid, 255, 0)
    Image.fromarray(rgba, "RGBA").save(path)

    left, bottom, right, top = ds.rio.bounds()
    to_wgs84 = Transformer.from_crs(ds.rio.crs, "EPSG:4326", always_xy=True)
    west, south = to_wgs84.transform(left, bottom)
    east, north = to_wgs84.transform(right, top)
    return [west, south, east, north]


def _resolve_layer_name(wanted: str, available: list[str], source_id: str) -> str:
    """Exact, then case-insensitive, then unique-substring layer name match —
    models quote capability names loosely ('fwi' for 'mf010.fwi')."""
    if wanted in available:
        return wanted
    low = wanted.lower()
    exact = [a for a in available if a.lower() == low]
    if exact:
        return exact[0]
    partial = [a for a in available if low in a.lower()]
    if len(partial) == 1:
        return partial[0]
    import difflib
    close = sorted(set(partial) | set(
        difflib.get_close_matches(wanted, available, n=3, cutoff=0.4)))
    raise KeyError(
        f"[{source_id}] layer {wanted!r} is not served"
        + (f" — did you mean one of {close}?" if close else "")
        + " Use names exactly as returned by list_source_layers.")


def geodatacatalog_tools(*, workdir: str | Path = ".",
                         catalog_path: str | Path | None = None,
                         sources: list | None = None,
                         confirm_writes: bool = True,
                         preview_base_url: str | None = None,
                         propose_writes: bool = False) -> list:
    """Build the geodatacatalog tool pack for a GeoAgent agent.

    ``workdir`` anchors relative catalog/export paths; ``catalog_path`` sets the
    default catalog.yml; ``sources`` overrides the shipped registry (tests).

    ``confirm_writes`` gates catalog writes and fetches behind GeoAgent's
    confirmation hook. Keep it on where a confirm UI exists (QGIS dock,
    terminal); turn it off for the browser WS backend, whose policy
    (``auto_approve_safe_only``) has no client round-trip and would silently
    cancel confirmation-gated tools.

    ``preview_base_url``: when the tool pack runs behind an HTTP server that
    exposes ``workdir`` under ``{preview_base_url}/files/`` (geoagent_serve),
    fetch results carry a ``preview_url`` instead of an inline GeoJSON preview —
    the data never crosses the LLM context; the map client fetches it directly.

    ``propose_writes``: the agent never edits the user's catalog.yml —
    propose_layers returns a ``proposal`` instead of writing, and the
    host UI (GeoLibre timeline) offers per-layer buttons (add to catalog /
    display / download / info) backed by the catalog sidecar. Turn it on for
    frontends that implement those buttons.
    """
    geoagent = _require_geoagent()
    geo_tool = geoagent.geo_tool

    state = _ToolState(workdir=workdir, catalog_path=catalog_path, sources=sources,
                       preview_base_url=preview_base_url)

    @geo_tool(category="geodatacatalog", available_in=("full", "fast"))
    def search_data_sources(query: str, theme: Optional[str] = None,
                            coverage: Optional[str] = None) -> dict:
        """Search the curated registry of open geospatial services (FR/EU focus).

        Args:
            query: Free-text keywords, French or English (e.g. "rivières",
                "flood zones", "occupation du sol").
            theme: Optional theme filter (e.g. "hydrographie", "risques").
            coverage: Optional coverage filter (e.g. "france", "europe", "global").

        Returns:
            {count, sources: [{id, name, type, coverage, themes, auth, notes, ...}]}
            — pass a source `id` to list_source_layers or propose_layers.
        """
        from .search import browse_sources

        records = browse_sources(query or None, state.sources,
                                 theme=theme, coverage=coverage)
        return {"count": len(records), "sources": records[:25]}

    @geo_tool(category="geodatacatalog")
    def list_source_layers(source_id: str, grep: Optional[str] = None,
                           endpoint: Optional[str] = None,
                           service_type: Optional[str] = None) -> dict:
        """List the layers/collections a source actually serves (live request).

        Args:
            source_id: Registry source id from search_data_sources. For an
                ad-hoc service found via discover_meta_catalogue, pass any name
                and set `endpoint` + `service_type` instead.
            grep: Optional case-insensitive substring filter on layer id/title.
            endpoint: Ad-hoc service URL (with service_type, bypasses registry).
            service_type: Ad-hoc service type (WFS/WMS/WMTS/WCS/STAC).

        Returns:
            {count, layers: [{id/name, title, ...}]}
        """
        import time as _time

        from .inspect import list_layers, list_layers_from
        from .registry import get_source

        def _list():
            if endpoint and service_type:
                return list_layers_from(service_type, endpoint, grep=grep)
            return list_layers(state.resolve_source(source_id), grep=grep)

        from .inspect import NotEnumerable

        try:
            layers = _list()
        except NotEnumerable as exc:
            # message written for the interactive assistant (make_catalog_entry)
            # — rephrase it for this tool pack
            raise ValueError(
                f"[{source_id}] this source's routes can't be enumerated. If it "
                "takes a free-form query (a species, a place...), call "
                "propose_layers with that query value as the layer name; "
                f"original hint: {exc}") from exc
        except Exception as exc:  # one retry: capability endpoints throw
            # transient 5xx (EFFIS/GWIS during the workflow campaign)
            transient = any(t in str(exc) for t in
                            ("500", "502", "503", "504", "timed out", "timeout",
                             "Connection", "connection"))
            if not transient:
                raise
            log.info("[%s] transient listing error, retrying once: %s",
                     source_id, exc)
            _time.sleep(2)
            layers = _list()
        out = {"count": len(layers), "layers": layers[:200]}
        if not (endpoint and service_type):
            src = state.resolve_source(source_id)
            query_param = (src.catalog or {}).get("queryParam")
            if query_param:
                out["note"] = (
                    f"query-driven source: products are free-form "
                    f"'{query_param}' values — pass the actual query (e.g. a "
                    "species name like 'Lutra lutra') as the layer name in "
                    "propose_layers, not a route name.")
        return out

    @geo_tool(category="geodatacatalog")
    def preview_source_layer(source_id: str, layer: Optional[str] = None,
                             endpoint: Optional[str] = None,
                             service_type: Optional[str] = None,
                             time: Optional[str] = None) -> dict:
        """Build a VERIFIED tile URL to display a raster layer on the map.

        The only sanctioned way to show a WMS/WMTS/XYZ layer: the URL is
        assembled from the source's endpoint (correct protocol parameters,
        layer name checked against the live capabilities) and one real tile
        is fetched before answering. Never hand-build a GetMap/tile URL.

        Args:
            source_id: Registry source id from search_data_sources. For an
                ad-hoc service, pass any name and set `endpoint` +
                `service_type` instead.
            layer: Layer name as served by the source (see
                list_source_layers). Required for WMS/WMTS; ignored for XYZ.
            endpoint: Ad-hoc service URL (with service_type, bypasses registry).
            service_type: Ad-hoc service type (WMS/WMTS/XYZ).
            time: ISO date (e.g. "2026-07-10") for time-dimensioned WMS layers
                (daily indices, weather...). Omit to auto-pick the most recent
                date that actually renders data — never rely on the server's
                default date, it is often stale and renders blank tiles.

        Returns:
            {tile_url, name, attribution, time?, legend_url?, note} — pass
            `tile_url` VERBATIM to add_xyz_tile_layer(url=tile_url, name=name).
        """
        from urllib.parse import quote

        if endpoint and service_type:
            base, stype = endpoint, service_type.lower()
            title = attribution = source_id
        else:
            src = state.resolve_source(source_id)
            base, stype = src.endpoint, src.type
            title = src.name
            attribution = getattr(src, "provider", None) or src.name

        def _attach_tile_proposal(result: dict) -> dict:
            """In propose mode the host renders the verified tile as a full
            per-layer offer (tiles / + catalog / download / info) — the same
            box propose_layers gets, so the user acts on it, not the agent."""
            if not (propose_writes and layer and result.get("tile_url")):
                return result
            result["proposal"] = {
                "source_id": source_id, "source_name": title,
                "layers": [layer], "endpoint": base,
                "service_type": stype.upper(), "kind": "raster",
                "catalog_path": str(state.resolve_catalog(None)),
                "adhoc": bool(endpoint and service_type),
                "tile_url": result["tile_url"],
                # layer-manager name for the tiles (never the tool's name)
                "display_name": result.get("name") or f"{title} — {layer}",
            }
            result["note"] = ("verified: one tile was fetched OK — the user "
                              "gets a « Charger les tuiles » button (plus add "
                              "to catalog / download / info); do not call "
                              "add_xyz_tile_layer yourself unless asked.")
            return result
        # probe near the active AOI so "no data here" reads as a blank tile,
        # not a false failure; default to the registry's FR/EU centre of mass
        lon, lat = 2.5, 46.6
        if state.last_aoi and state.last_aoi.get("bbox"):
            b = state.last_aoi["bbox"]
            lon, lat = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        sep = "&" if "?" in base else "?"
        legend_url = None
        ignored_time = None

        if stype == "xyz":
            template = base
            if "{z}" not in template and not (endpoint and service_type):
                # manifest-driven XYZ (e.g. RainViewer): the endpoint is an API
                # root; the layer resolves the real template (host + newest frame)
                from .inspect import _build_layer
                lyr = _build_layer(src, layer or "tiles")
                if type(lyr).__name__ == "XYZLayer":
                    template = (lyr.tile_url_template(time)
                                .replace("{s}", lyr.product.subdomains[0])
                                .replace("{r}", ""))
            if "{z}" not in template:
                raise ValueError(
                    f"[{source_id}] endpoint is not an XYZ template "
                    f"({base}) — no {{z}}/{{x}}/{{y}} placeholders.")
        elif stype == "wms":
            if not layer:
                raise ValueError(
                    f"[{source_id}] `layer` is required for a WMS source — "
                    "pick one with list_source_layers.")
            from .inspect import list_layers_from
            served = list_layers_from("wms", base)
            by_id = {str(l.get("id") or l.get("name")): l for l in served}
            layer = _resolve_layer_name(layer, list(by_id), source_id)
            temporal = bool(by_id[layer].get("sends_datetime"))
            # STYLES= is mandatory even empty (MapServer 8, e.g. EFFIS/GWIS);
            # MapLibre substitutes {bbox-epsg-3857} natively in raster tiles
            template = (f"{base}{sep}SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
                        f"&LAYERS={quote(layer)}&STYLES=&CRS=EPSG:3857"
                        "&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256"
                        "&FORMAT=image/png&TRANSPARENT=true")
            legend_url = (f"{base}{sep}SERVICE=WMS&VERSION=1.3.0"
                          f"&REQUEST=GetLegendGraphic&LAYER={quote(layer)}"
                          "&FORMAT=image/png")
            if temporal:
                # a time-dimensioned layer rendered without TIME falls back to
                # the server's default date, often stale → 200 but fully
                # transparent tiles (EFFIS defaults to 2021-01-01)
                return _attach_tile_proposal(
                    _verified_temporal_wms(template, time, lon, lat,
                                           source_id, layer,
                                           name=(f"{title} — {layer}"),
                                           attribution=attribution,
                                           legend_url=legend_url))
            if time:
                # QGIS-style rule: TIME is only ever sent to a layer whose
                # capabilities declare a time dimension — baking a TIME the
                # model invented onto any other layer pins rolling-window
                # layers (e.g. GWIS nrt.ba) to one arbitrary date
                ignored_time = time
        elif stype == "wmts":
            if not layer:
                raise ValueError(
                    f"[{source_id}] `layer` is required for a WMTS source — "
                    "pick one with list_source_layers.")
            from owslib.wmts import WebMapTileService
            wmts = WebMapTileService(base)
            layer = _resolve_layer_name(layer, list(wmts.contents), source_id)
            lyr = wmts.contents[layer]
            # need a web-mercator pyramid whose matrix ids are plain zoom
            # numbers, otherwise TILEMATRIX={z} cannot work
            matrix_set = None
            for name in getattr(lyr, "tilematrixsetlinks", {}) or []:
                ms = wmts.tilematrixsets.get(name)
                ids = list(ms.tilematrix) if ms else []
                crs = str(getattr(ms, "crs", ""))
                if (ids and all(i.isdigit() for i in ids)
                        and ("3857" in crs or "900913" in crs
                             or name in ("PM", "GoogleMapsCompatible"))):
                    matrix_set = name
                    break
            if matrix_set is None:
                raise ValueError(
                    f"[{source_id}] layer {layer!r} has no web-mercator tile "
                    "matrix set with numeric zoom ids — this WMTS layer cannot "
                    "be displayed on this map; pick another source.")
            formats = list(getattr(lyr, "formats", None) or ["image/png"])
            fmt = "image/png" if "image/png" in formats else formats[0]
            styles = getattr(lyr, "styles", None) or {}
            style = next((s for s, v in styles.items()
                          if (v or {}).get("isDefault")), None)
            style = style or next(iter(styles), "normal")
            template = (f"{base}{sep}SERVICE=WMTS&REQUEST=GetTile"
                        f"&VERSION=1.0.0&LAYER={quote(layer)}"
                        f"&STYLE={quote(style)}&TILEMATRIXSET={matrix_set}"
                        "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
                        f"&FORMAT={quote(fmt, safe='/')}")
        elif stype == "stac":
            raise ValueError(
                f"[{source_id}] STAC sources serve imagery items, not tiles — "
                "add the collection with propose_layers, call "
                "fetch_catalog_layers with a datetime range, then display a "
                "`quicklook` (sorted least-cloudy first) with "
                "add_image_overlay, or render_stac_imagery for the "
                "full-resolution pixels.")
        else:
            raise ValueError(
                f"[{source_id}] preview_source_layer is for raster "
                f"visualisation (wms/wmts/xyz), not type={stype!r} — fetch "
                "the data with fetch_catalog_layers and display its preview "
                "with add_vector_data/add_geojson_data instead.")

        _probe_tile(template, lon, lat)
        out = {"tile_url": template,
               "name": f"{title} — {layer}" if layer else title,
               "attribution": attribution,
               "note": ("verified: one tile was fetched OK — pass tile_url "
                        "verbatim to add_xyz_tile_layer(url=tile_url, "
                        "name=name)")}
        if ignored_time:
            out["note"] += (f"; time={ignored_time!r} was IGNORED: this layer "
                            "declares no time dimension in its capabilities, "
                            "so the server always serves its current data — "
                            "do not retry with another time")
        if legend_url:
            out["legend_url"] = legend_url
        return _attach_tile_proposal(out)

    @geo_tool(category="geodatacatalog")
    def discover_meta_catalogue(source_id: str, query: str) -> dict:
        """Keyword-search a CSW meta-catalogue source (type "registry").

        Args:
            source_id: A registry source flagged as a meta-catalogue (e.g.
                geocatalogue national). Use search_data_sources to find them.
            query: Dataset keywords.

        Returns:
            {matches, records: [{identifier, title, urls: [{kind, url}], ...}]}
            — a WFS/WMS url can then feed propose_layers (endpoint mode).
        """
        from .discover import discover
        from .registry import get_source

        return discover(state.resolve_source(source_id), query)

    @geo_tool(category="geodatacatalog", available_in=("full", "fast"))
    def geocode_aoi(place: str, limit: int = 1) -> dict:
        """Resolve a place name to a real area of interest via Nominatim.

        Never invent coordinates: always use this tool for the user's area.
        The resolved geometry is remembered and used to clip later fetches.

        Args:
            place: Place name (e.g. "Toulouse", "bassin versant de la Garonne").
            limit: Number of candidates to return.

        Returns:
            {candidates: [{display_name, bbox: [lon_min, lat_min, lon_max,
            lat_max], country_code, geometry_kind}], aoi_layer, and
            preview_url or preview (the active AOI's outline, displayable
            like a fetch result)} — the first candidate becomes the active
            AOI.
        """
        from .geocode import geocode_place

        hits = geocode_place(place, limit=limit)
        result: dict[str, Any] = {"candidates": [
            {"display_name": h.get("display_name"),
             "bbox": list(h["bbox"]) if h.get("bbox") else None,
             "country_code": h.get("country_code"),
             "geometry_kind": (h.get("geometry") or {}).get("type")}
            for h in hits]}
        if hits:
            state.last_aoi = hits[0]
            result.update(_aoi_display(state, hits[0]))
        return result

    @geo_tool(category="geodatacatalog", requires_confirmation=confirm_writes)
    def create_catalog(catalog_path: str, theme: Optional[str] = None,
                       overwrite: bool = False) -> dict:
        """Create a fresh project catalog.yml — blank, or seeded from a theme.

        Args:
            catalog_path: Where to write the catalog (e.g. "flood_catalog.yml").
            theme: Optional registry theme; seeds one starter layer per matching
                source (e.g. "hydrographie", "risques").
            overwrite: Replace an existing file.

        Returns:
            {catalog, n_providers, n_layers}
        """
        from .inspect import catalog_summary, new_catalog
        from .search import themes_for_query

        seed = None
        if theme:
            # registry themes are canonical English tokens; map the user's
            # wording (French or English, e.g. "risques") onto them
            wanted = themes_for_query(theme) | {theme}
            seed = [s for s in state.sources if wanted & set(s.themes)] or None
            if seed is None:
                raise ValueError(
                    f"no registry source matches theme {theme!r} — try one of "
                    "the canonical themes returned by search_data_sources, or "
                    "create a blank catalog and add layers explicitly.")
            # seeding resolves each source's description live: cap it so a
            # broad theme doesn't turn into dozens of slow requests
            seed = seed[:8]
        target = state.resolve_catalog(catalog_path)
        try:
            path = new_catalog(target, sources=seed, overwrite=overwrite)
        except FileExistsError:
            raise FileExistsError(
                f"{target} already exists — call propose_layers to "
                "extend it, or pass overwrite=True to replace it.")
        summary = catalog_summary(path)
        return {"catalog": str(path), "n_providers": summary["n_providers"],
                "n_layers": summary["n_layers"]}

    @geo_tool(category="geodatacatalog", requires_confirmation=confirm_writes)
    def propose_layers(source_id: str, layers: list[str], reason: str,
                              catalog_path: Optional[str] = None,
                              endpoint: Optional[str] = None,
                              service_type: Optional[str] = None) -> dict:
        """Add layers from a source into the user's catalog.yml, with a reason.

        Args:
            source_id: Registry source id — or the provider name to use when
                adding an ad-hoc service (then set `endpoint` + `service_type`).
            layers: Layer/collection ids as served by the source (from
                list_source_layers).
            reason: Honest, specific justification for why these layers answer
                the user's request. Shown to the user and kept in the trace.
            catalog_path: Target catalog.yml (defaults to the pack's catalog).
            endpoint: Ad-hoc service URL (bypasses the registry).
            service_type: Ad-hoc service type (WFS/WMS/WMTS/WCS/STAC/REST).

        Returns:
            {catalog, provider, layer_ids, reason} — layer_ids are the
            "{provider}__{product}" ids to pass to fetch_catalog_layers.
            With the pack in proposal mode (GeoLibre), {proposed, proposal,
            note} instead: nothing is written, the user gets per-layer
            buttons (add to catalog / display / download / info).
        """
        from .inspect import (_slug, merge_into_catalog, to_catalog_provider,
                              to_catalog_provider_raw)

        if not reason or not reason.strip():
            raise ValueError("`reason` is required: justify why these layers "
                             "answer the request.")
        if propose_writes:
            from .search import kind_for

            proposal: dict[str, Any] = {
                "source_id": source_id, "layers": list(layers),
                "reason": reason.strip(),
                "catalog_path": str(state.resolve_catalog(catalog_path)),
            }
            if endpoint and service_type:
                proposal.update(endpoint=endpoint, source_name=source_id,
                                service_type=service_type.upper(),
                                kind=kind_for(service_type.lower()), adhoc=True)
            else:
                source = state.resolve_source(source_id)
                proposal.update(endpoint=source.endpoint,
                                source_name=source.name,
                                service_type=source.type.upper(),
                                kind=kind_for(source.type,
                                              source.catalog.get("plugin")),
                                adhoc=False)
            return {"proposed": True, "proposal": proposal,
                    "note": ("nothing was written: the USER decides via "
                             "per-layer buttons (add to catalog, display, "
                             "download, info). Treat the add as done and do "
                             "not call this tool again for these layers; "
                             "fetch_catalog_layers only works for layers the "
                             "user has actually added.")}
        if endpoint and service_type:
            block = to_catalog_provider_raw(source_id, service_type, endpoint, layers)
        else:
            source = state.resolve_source(source_id)
            block = to_catalog_provider(source, layers, resolve_descriptions=True)
        path = merge_into_catalog(block, state.resolve_catalog(catalog_path))
        provider = next(iter(block))
        return {"catalog": str(path), "provider": provider,
                "layer_ids": [f"{provider}__{_slug(l)}" for l in layers],
                "reason": reason.strip()}

    @geo_tool(category="geodatacatalog", available_in=("full", "fast"))
    def catalog_info(catalog_path: Optional[str] = None) -> dict:
        """Summarise a catalog.yml: providers, layer ids, auth needs.

        Args:
            catalog_path: Catalog to read (defaults to the pack's catalog).

        Returns:
            {path, n_providers, n_layers, providers: [{name, type, layers}]}
        """
        from .inspect import catalog_summary

        return catalog_summary(state.resolve_catalog(catalog_path))

    # long_running also routes through the confirmation hook
    # (needs_confirmation = requires_confirmation OR destructive OR long_running),
    # so it must follow confirm_writes or headless backends deny the fetch.
    @geo_tool(category="geodatacatalog", requires_confirmation=confirm_writes,
              long_running=confirm_writes)
    def fetch_catalog_layers(layer_ids: list[str],
                             catalog_path: Optional[str] = None,
                             bbox: Optional[list[float]] = None,
                             place: Optional[str] = None,
                             datetime: Optional[str] = None,
                             attribute_filters: Optional[dict] = None,
                             out_dir: str = "exports",
                             format: Optional[str] = None) -> dict:
        """Fetch catalog.yml layers over an AOI, export them and return a preview.

        Args:
            layer_ids: "{provider}__{product}" ids (see catalog_info /
                propose_layers).
            catalog_path: Catalog to fetch from (defaults to the pack's catalog).
            bbox: [lon_min, lat_min, lon_max, lat_max] in EPSG:4326. Omit to use
                `place` or the AOI from the last geocode_aoi call.
            place: Place name to geocode into the AOI (alternative to bbox).
            datetime: Optional ISO date or "start/end" range.
            attribute_filters: Optional {column: value} filters kept after the
                fetch — case-insensitive "contains" match on vector attributes
                (e.g. {"toponyme": "garonne"} keeps only the Garonne river).
            out_dir: Export directory (relative to the pack's workdir).
            format: Export format for every layer (default geojson for vector,
                tif for raster; also gpkg/shp/nc).

        Returns:
            {layers: {layer_id: {features/kind, exported: [paths], and either
            preview_url (display it with add_vector_data(url=...)) or an
            inline capped preview FeatureCollection}}, out_dir}
        """
        from ..config import load_catalog
        from .inspect import _request_for

        aoi = None
        if place:
            from .geocode import geocode_place
            hits = geocode_place(place)
            if not hits:
                raise ValueError(f"could not geocode {place!r}")
            state.last_aoi = hits[0]
        if bbox is None:
            if state.last_aoi is None:
                raise ValueError("no AOI: pass `bbox` or `place`, or call "
                                 "geocode_aoi first.")
            aoi = state.last_aoi.get("geometry")
            bbox = list(state.last_aoi["bbox"])
        request = _request_for(tuple(bbox), datetime, aoi)

        catalog_file = state.resolve_catalog(catalog_path)
        try:
            catalog = load_catalog(catalog_file)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"catalog {catalog_file} does not exist yet — call "
                "propose_layers (or create_catalog) before fetching.")
        # Models often rebuild ids from the raw layer name ("PYR-PNG" instead
        # of the slugged "PYR_PNG"): remap through a tolerant slug so a merely
        # mis-punctuated id doesn't dead-end the whole workflow.
        import re
        norm = lambda s: re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
        by_norm = {norm(k): k for k in catalog.layers}
        layer_ids = [lid if lid in catalog.layers else by_norm.get(norm(lid), lid)
                     for lid in layer_ids]

        def _by_product(lid: str) -> str:
            # second chance: same product under another provider prefix
            # (the model wrote ign_wmts__X for a layer added as ign_wfs__X)
            tail = norm(lid.split("__", 1)[-1])
            hits = [k for k in catalog.layers
                    if norm(k.split("__", 1)[-1]) == tail]
            return hits[0] if len(hits) == 1 else lid

        layer_ids = [lid if lid in catalog.layers else _by_product(lid)
                     for lid in layer_ids]
        missing = [l for l in layer_ids if l not in catalog.layers]
        if missing:
            raise KeyError(f"layer ids not in catalog: {missing}; "
                           f"available: {sorted(catalog.layers)} — use one of "
                           "these exact ids, do not retry the same id")
        results = catalog.fetch(request, layers=layer_ids)

        import geopandas as gpd

        export_dir = state.workdir / out_dir

        def _note(entry: dict, text: str) -> None:
            entry["note"] = f"{entry['note']}; {text}" if entry.get("note") else text

        def _attach_display(entry: dict, data, lid: str) -> None:
            """preview_url (served) or inline capped preview for a GeoDataFrame."""
            if not len(data):
                _note(entry, "empty result — nothing to display for this layer; "
                             "do not invent a preview_url")
                return
            if state.preview_base_url is None:
                # no file server around: inline (capped) preview fallback
                preview = _preview_geojson(data)
                entry["preview"] = preview
                if preview and preview["properties"].get("truncated"):
                    _note(entry, "preview truncated for display — the exported "
                                 "file holds all features; pass the preview "
                                 "verbatim to the map, do not enumerate its "
                                 "features")
                return
            # Served display copy: the data never crosses the chat context —
            # the map fetches this file straight from the server. Always
            # WGS84: exports keep their native CRS (e.g. BD TOPO in
            # Lambert-93), but MapLibre needs lon/lat ("Invalid LngLat
            # latitude" on projected coordinates).
            from urllib.parse import quote
            export_dir.mkdir(parents=True, exist_ok=True)
            display = data
            if data.crs is not None and str(data.crs).upper() != "EPSG:4326":
                display = data.to_crs("EPSG:4326")
            dpath = export_dir / f"{lid}.display.geojson"
            display.to_file(dpath, driver="GeoJSON")
            rel = dpath.resolve().relative_to(
                state.workdir.resolve()).as_posix()
            entry["preview_url"] = (
                f"{state.preview_base_url}/files/{quote(rel)}")
            _note(entry, "display with add_vector_data(url=preview_url, "
                         "name=<layer name>), using preview_url EXACTLY as "
                         "given — never inline the data in chat; hosts with "
                         "a timeline turn the add into a user button, so do "
                         "not retry if the layer does not appear")

        report: dict[str, dict] = {}
        for lid in layer_ids:
            data = results[lid]
            if isinstance(data, gpd.GeoDataFrame):
                if attribute_filters:
                    data = _apply_attribute_filters(data, attribute_filters)
                    results[lid] = data
                state.last_results[lid] = data   # geoprocessing (buffer_layer...)
                report[lid] = {"kind": "vector", "features": int(len(data))}
                _attach_display(report[lid], data, lid)
            elif (footprints := _stac_footprints(data)) is not None:
                state.last_stac[lid] = {"catalog": catalog, "request": request,
                                        "items": data}
                report[lid] = {"kind": "stac_items",
                               "features": int(len(footprints))}
                _note(report[lid], "the preview shows the scene footprints + "
                                   "metadata, not the imagery — the images "
                                   "themselves are in the exported files")
                _attach_display(report[lid], footprints, lid)
                quicklooks = _stac_quicklooks(data)
                if quicklooks:
                    report[lid]["quicklooks"] = quicklooks
                    _note(report[lid],
                          "to show the actual imagery, pick a quicklook "
                          "(sorted least-cloudy first) and call "
                          "add_image_overlay(url=<url>, bounds=<bounds>, "
                          "name=<item_id>) with BOTH values copied verbatim")
            else:
                report[lid] = {"kind": type(data).__name__}
                _note(report[lid], "no map display for this result kind — "
                                   "report the exported file paths to the "
                                   "user; they are local paths, NOT URLs: "
                                   "never turn them into an http://.../files/ "
                                   "URL or call add_vector_data with them")

        # A vector format hint (geojson/gpkg/shp) must not leak onto raster
        # layers: rio.to_raster("*.geojson") dies with "Unable to detect driver".
        fmt_map = ({lid: format for lid in layer_ids
                    if isinstance(results[lid], gpd.GeoDataFrame)}
                   if format else None)
        results.export(export_dir, formats=fmt_map, layers=layer_ids)
        for lid in layer_ids:
            prefix = lid.replace("__", "_")
            exported = sorted(
                str(p) for p in export_dir.glob(f"*{lid}*")) or sorted(
                str(p) for p in export_dir.glob(f"*{prefix}*"))
            report[lid]["exported"] = [
                p for p in exported if not p.endswith(".display.geojson")]
            urls = [u for u in (_served_url(state, p)
                                for p in report[lid]["exported"]) if u]
            if urls:
                # browser-downloadable copies of the exports (timeline ⬇ links)
                report[lid]["exported_urls"] = urls
        return {"out_dir": str(export_dir), "layers": report}

    @geo_tool(category="geodatacatalog", requires_confirmation=confirm_writes)
    def buffer_layer(layer_id: str, distance_km: float, dissolve: bool = True,
                     out_dir: str = "exports") -> dict:
        """Buffer a fetched layer by a distance in kilometres (server-side).

        Real geoprocessing on the result of fetch_catalog_layers (this
        session): metric buffer computed in the layer's UTM zone.

        Args:
            layer_id: A layer id already fetched with fetch_catalog_layers.
            distance_km: Buffer radius in kilometres (e.g. 10).
            dissolve: Merge all buffers into a single polygon (default), or
                keep one buffer per feature.
            out_dir: Export directory.

        Returns:
            {layer, features, exported, and preview_url or inline preview —
            display it like a fetch result}
        """
        import geopandas as gpd

        if not 0 < float(distance_km) <= 500:
            hint = ""
            if float(distance_km) > 500:
                hint = (f" — {float(distance_km):g} looks like metres; pass "
                        f"distance_km={float(distance_km) / 1000:g}")
            raise ValueError(f"distance_km is in kilometres and must be in "
                             f"(0, 500]{hint}.")

        data = state.last_results.get(layer_id)
        if data is None:
            # tolerate a file path or a mis-punctuated id: match the stem
            # against the fetched layer ids
            import re
            norm = lambda s: re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
            stem = norm(Path(layer_id).stem)
            hits = [k for k in state.last_results
                    if norm(k) == stem or norm(k).endswith(stem)
                    or stem.endswith(norm(k))]
            if len(hits) == 1:
                layer_id = hits[0]
                data = state.last_results[layer_id]
        if data is None:
            raise ValueError(
                f"{layer_id} has not been fetched in this session — call "
                f"fetch_catalog_layers first (fetched: "
                f"{sorted(state.last_results) or 'none'}).")
        if data.crs is None:
            data = data.set_crs("EPSG:4326")
        # services routinely ship a stray null/empty geometry (Natural Earth,
        # SANDRE): shapely's buffer refuses None — drop them first
        data = data[data.geometry.notna() & ~data.geometry.is_empty]
        if not len(data):
            raise ValueError(f"{layer_id} has no valid geometries to buffer.")
        metric = data.to_crs(data.estimate_utm_crs())
        buffered = metric.geometry.buffer(float(distance_km) * 1000.0)
        if dissolve:
            merged = (buffered.union_all() if hasattr(buffered, "union_all")
                      else buffered.unary_union)
            buffered = gpd.GeoSeries([merged], crs=metric.crs)
        out = gpd.GeoDataFrame(geometry=buffered, crs=metric.crs).to_crs("EPSG:4326")

        export_dir = state.workdir / out_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        name = f"{layer_id}_buffer_{distance_km:g}km"
        path = export_dir / f"{name}.geojson"
        out.to_file(path, driver="GeoJSON")
        result = {"layer": name, "features": int(len(out)),
                  "exported": [str(path)]}
        state.last_results[name] = out           # chainable (buffer of buffer...)
        url = _served_url(state, path)
        if url:
            result["preview_url"] = url
            result["exported_urls"] = [url]
            result["note"] = ("display with add_vector_data(url=preview_url, "
                              "name=<layer name>) — never inline the data in chat")
        else:
            result["preview"] = _preview_geojson(out)
        return result

    @geo_tool(category="geodatacatalog", requires_confirmation=confirm_writes,
              long_running=confirm_writes)
    def render_stac_imagery(layer_id: str,
                            item_ids: Optional[list[str]] = None,
                            bands: Optional[list[str]] = None,
                            resolution: Optional[float] = None,
                            out_dir: str = "exports") -> dict:
        """Download STAC imagery (COGs) over the fetched AOI and render a
        georeferenced image the map can drape.

        Loads the actual pixels server-side (odc-stac, with the provider's
        credentials from the environment), exports a GeoTIFF, and writes a
        display PNG. Full-resolution counterpart to the low-res `quicklooks`
        of fetch_catalog_layers.

        Args:
            layer_id: A STAC layer id already fetched with
                fetch_catalog_layers in this session.
            item_ids: Optional STAC item ids to keep (e.g. the least cloudy
                one from the fetch report); default: all fetched items,
                mosaicked.
            bands: Optional band names; default true-colour RGB.
            resolution: Ground resolution in metres; default sized for
                display (~1280 px across the AOI).
            out_dir: Export directory.

        Returns:
            {image_url?, image_path, bounds, exported, items_used,
            resolution_m} — display with add_image_overlay(url=image_url,
            bounds=bounds, name=<layer or item id>).
        """
        import dataclasses
        import math

        stash = state.last_stac.get(layer_id)
        if stash is None:
            raise ValueError(
                f"{layer_id} has no STAC items in this session — call "
                f"fetch_catalog_layers first (have: "
                f"{sorted(state.last_stac) or 'none'}).")
        items = stash["items"]
        if item_ids:
            wanted = set(item_ids)
            items = [it for it in items if getattr(it, "id", None) in wanted]
            if not items:
                available = [getattr(i, "id", None) for i in stash["items"]]
                raise KeyError(f"none of {sorted(wanted)} among the fetched "
                               f"items: {available[:20]}")

        request = stash["request"]
        if resolution is None:
            west, south, east, north = request.bbox
            lat_c = math.radians((south + north) / 2)
            width_m = (east - west) * 111320.0 * math.cos(lat_c)
            resolution = max(10.0, width_m / 1280)
        # mercator pixels: what MapLibre's linear image warp expects
        req = dataclasses.replace(request, crs="EPSG:3857",
                                  resolution=float(resolution))

        from ..results import CatalogResults
        from ..utils import _export_dataset_to_raster
        loaded = CatalogResults({layer_id: items}, stash["catalog"], req)
        ds = loaded.load(layer_id, bands=bands)[layer_id]
        if "time" in ds.dims:                 # one image: slice or composite
            ds = (ds.isel(time=0) if ds.sizes["time"] == 1
                  else ds.median("time", skipna=True))

        export_dir = state.workdir / out_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        tif_path = export_dir / f"{layer_id}_imagery.tif"
        _export_dataset_to_raster(ds, tif_path)
        png_path = export_dir / f"{layer_id}_imagery.png"
        bounds = _render_rgb_png(ds, png_path)

        result = {"image_path": str(png_path), "bounds": bounds,
                  "exported": [str(tif_path)],
                  "items_used": [getattr(i, "id", None) for i in items],
                  "resolution_m": float(resolution)}
        image_url = _served_url(state, png_path)
        if image_url:
            result["image_url"] = image_url
            tif_url = _served_url(state, tif_path)
            if tif_url:
                result["exported_urls"] = [tif_url]
            result["note"] = ("display with add_image_overlay(url=image_url, "
                              "bounds=bounds, name=...), both copied verbatim")
        else:
            result["note"] = ("no file server: the PNG/GeoTIFF are on disk "
                              "for the user; do not invent an image URL")
        return result

    return [search_data_sources, list_source_layers, preview_source_layer,
            discover_meta_catalogue, geocode_aoi, create_catalog,
            propose_layers, catalog_info, fetch_catalog_layers,
            buffer_layer, render_stac_imagery]


def _default_provider_model(provider: str | None, model_id: str | None):
    """LiteLLM/Mistral by default; anthropic when only ANTHROPIC_API_KEY is set."""
    if provider is None:
        if os.environ.get("MISTRAL_API_KEY"):
            provider = "litellm"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            provider = "litellm"
    if model_id is None and provider == "litellm":
        model_id = os.environ.get("LITELLM_MODEL", _DEFAULT_LITELLM_MODEL)
    return provider, model_id


def make_agent(provider: str | None = None, model_id: str | None = None,
               *, workdir: str | Path = ".",
               catalog_path: str | Path | None = None,
               confirm_writes: bool = False, **kwargs):
    """A standalone GeoAgent with the geodatacatalog tool pack (no map bound).

    ``confirm_writes`` defaults to False here: without a ``confirm=`` callback
    GeoAgent falls back to ``auto_approve_safe_only``, which silently cancels
    confirmation-gated tools — pass ``confirm_writes=True`` together with your
    own ``confirm=`` callback to gate catalog writes and fetches.
    """
    geoagent = _require_geoagent()

    provider, model_id = _default_provider_model(provider, model_id)
    context = geoagent.GeoAgentContext(metadata={"system_prompt": SYSTEM_PROMPT})
    return geoagent.create_agent(
        context=context,
        tools=geodatacatalog_tools(workdir=workdir, catalog_path=catalog_path,
                                   confirm_writes=confirm_writes),
        provider=provider, model_id=model_id, **kwargs)


def for_map(m, provider: str | None = None, model_id: str | None = None,
            *, workdir: str | Path = ".",
            catalog_path: str | Path | None = None,
            confirm_writes: bool = False, **kwargs):
    """Bind a leafmap Map to GeoAgent with the geodatacatalog tools attached.

    Same ``confirm_writes`` default as :func:`make_agent` (notebooks have no
    confirm UI either).
    """
    geoagent = _require_geoagent()

    provider, model_id = _default_provider_model(provider, model_id)
    return geoagent.for_leafmap(
        m, extra_tools=geodatacatalog_tools(workdir=workdir,
                                            catalog_path=catalog_path,
                                            confirm_writes=confirm_writes),
        provider=provider, model_id=model_id, **kwargs)
