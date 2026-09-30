"""Functional smoke-tests: actually FETCH each layer over a tiny AOI.

``check`` (:mod:`geodatacatalog.explorer.probe`) only pings an endpoint
(GetCapabilities / a plain GET) — it never proves a layer returns usable data.
``geodatacatalog check --fetch`` calls this module to build the layer exactly as
``add``/``fetch`` would (through
:func:`geodatacatalog.explorer.inspect.to_catalog_provider` →
:func:`geodatacatalog.load_catalog`) and perform a real fetch on a small bbox,
reporting one of:

* ``ok``            — fetched ≥1 feature / item / non-empty raster
* ``empty``         — fetched with no error but no data over the test AOI
* ``fail``          — raised (the exception message is kept)
* ``skipped``       — not fetchable (registry/csw/xyz, no testable layer,
                      free-form productType without a ``test`` value)
* ``auth_required`` — needs credentials that are not in the environment

This hits live servers, so it is **not** part of the offline pytest suite —
run it with ``geodatacatalog check --fetch`` (optionally filtered) on your network.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from .registry import Source

log = logging.getLogger(__name__)

#: coverage → a small land bbox (EPSG:4326, lon/lat) over a populated/featured area
_BBOX_BY_COVERAGE: dict[str, tuple] = {
    "france": (1.40, 43.55, 1.50, 43.65),      # Toulouse
    "europe": (1.40, 43.55, 1.50, 43.65),
    "usa": (-77.05, 38.88, -76.95, 38.95),     # Washington DC
    "uk": (-0.12, 51.50, -0.08, 51.52),        # London
    "germany": (13.36, 52.50, 13.42, 52.53),   # Berlin
    "spain": (-3.72, 40.40, -3.68, 40.43),     # Madrid
    "norway": (10.70, 59.90, 10.78, 59.94),    # Oslo
    "finland": (24.90, 60.13, 25.00, 60.18),   # Helsinki harbour (water → AIS)
    "netherlands": (4.88, 52.36, 4.92, 52.38), # Amsterdam
    "belgium": (4.34, 50.83, 4.38, 50.86),     # Brussels
    "switzerland": (7.43, 46.94, 7.47, 46.96), # Bern
    "canada": (-75.71, 45.41, -75.67, 45.43),  # Ottawa
    "australia": (151.20, -33.88, 151.24, -33.85),   # Sydney
    "new-zealand": (174.76, -36.86, 174.80, -36.84), # Auckland
    "africa": (18.40, -33.94, 18.44, -33.91),  # Cape Town
    "global": (1.40, 43.55, 1.50, 43.65),
}
#: marine layers need a sea bbox, not a land one (Gulf of Lion, off France)
_OCEAN_BBOX = (4.00, 42.40, 4.40, 42.80)
#: a recent fair-weather window for time-aware sources
_DEFAULT_DATETIME = "2023-06-01/2023-09-30"
_TEMPORAL_THEMES = {"satellite", "imagery", "weather", "climate", "fire"}

_NOT_FETCHABLE = {"registry", "csw", "xyz"}


@dataclass
class VerifyResult:
    """Outcome of one (source, layer) fetch attempt."""
    source_id: str
    layer: str | None
    state: str
    detail: str
    count: int | None = None
    latency_ms: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _aoi_for(source: Source) -> tuple[tuple, str | None]:
    """Pick a small test bbox and (optional) datetime window for a source."""
    bbox = source.test.get("bbox")
    if isinstance(bbox, str):
        bbox = tuple(float(x) for x in bbox.split(","))
    if not bbox:
        if "ocean" in source.themes and source.coverage in ("global", "europe"):
            bbox = _OCEAN_BBOX
        else:
            bbox = _BBOX_BY_COVERAGE.get(source.coverage, _BBOX_BY_COVERAGE["global"])
    dt = source.test.get("datetime")
    if dt is None and set(source.themes) & _TEMPORAL_THEMES:
        dt = _DEFAULT_DATETIME
    return tuple(bbox), dt


def _smoke_zoom(bbox) -> int:
    """A low XYZ/WMTS zoom rendering the bbox in ~1 tile — a fast raster smoke-test
    (auto-zoom would otherwise fetch a large multi-tile image, e.g. 1792×1536)."""
    width = max(float(bbox[2]) - float(bbox[0]), 1e-3)
    return max(4, min(12, round(math.log2(360.0 / width)) - 1))


def test_bboxes_geojson(sources: list[Source]) -> dict:
    """GeoJSON FeatureCollection of the distinct AOIs the fetch smoke-test uses.

    One rectangle per distinct (bbox, datetime), with the sources sharing it — drop
    it into geojson.io / QGIS to see where each source is exercised.
    """
    from shapely.geometry import box, mapping

    groups: dict[tuple, list[str]] = {}
    for s in sources:
        if s.type in _NOT_FETCHABLE:
            continue  # registry/csw/xyz are never fetch-tested
        bbox, dt = _aoi_for(s)
        groups.setdefault((bbox, dt), []).append(s.id)
    feats = []
    for (bbox, dt), ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        feats.append({"type": "Feature", "geometry": mapping(box(*bbox)),
                      "properties": {"bbox": list(bbox), "datetime": dt,
                                     "n_sources": len(ids), "sources": sorted(ids)}})
    return {"type": "FeatureCollection", "features": feats}


def _has_creds(source: Source) -> bool:
    """True if the env looks like it holds this source's credentials.

    Checks the default ``{ID}_API_KEY``/``{ID}_TOKEN`` and any ``${VAR}`` named in
    the source's ``catalog.credentials`` (e.g. a plugin's CDSE_CLIENT_ID)."""
    import os
    import re
    keys = [f"{source.id.upper()}_API_KEY", f"{source.id.upper()}_TOKEN"]
    for value in (source.catalog.get("credentials") or {}).values():
        keys += re.findall(r"\$\{(\w+)", str(value))
    return any(os.environ.get(k) for k in keys)


def _layers_to_test(source: Source, max_layers: int) -> list[str]:
    """Choose representative layer name(s) to fetch for this source."""
    hint = source.test.get("layer") or source.test.get("collection")
    if hint:
        return [hint]
    if source.layers:
        from .inspect import _curated_layers
        return [l["id"] for l in _curated_layers(source)][:max_layers]
    if source.type in ("stac", "wfs", "wms", "wcs", "wmts"):
        from .inspect import list_layers
        return [l["id"] for l in list_layers(source)][:max_layers]
    return []


def _measure(kind, data) -> tuple[int | None, bool, str]:
    """Return (count, is_empty, human_detail) for a fetched payload."""
    from ..base import LayerKind
    if kind == LayerKind.VECTOR:
        n = len(data)
        return n, n == 0, f"{n} feature(s)"
    if kind == LayerKind.STAC_ITEMS:
        n = len(data)
        return n, n == 0, f"{n} STAC item(s)"
    # raster (xarray DataArray/Dataset)
    import xarray as xr
    d = data
    if isinstance(d, xr.Dataset):
        d = next(iter(d.data_vars.values()), None)
    if d is None or int(getattr(d, "size", 0) or 0) == 0:
        return None, True, "empty raster"
    shape = " × ".join(str(s) for s in d.shape[-2:])
    empty = False
    try:  # a blank tile (all-nodata) counts as empty
        empty = bool(d.isnull().all())
    except Exception:
        pass
    # count stays None for rasters — the pixel total is not a meaningful "feature"
    # count; the dimensions go in the detail instead.
    return None, empty, f"raster {shape}"


async def _fetch_once(layer, request):
    """Fetch one layer with no cache, inline (so the real exception propagates)."""
    from ..catalog import _NullCache
    return await layer.fetch_async(request, _NullCache(), None, parallel=False)


def verify_source(source: Source, *, bbox=None, datetime=None,
                  max_layers: int = 1, layer: str | None = None) -> list[VerifyResult]:
    """Smoke-test a source by fetching one or more of its layers on a tiny AOI."""
    if source.type in _NOT_FETCHABLE:
        why = {"registry": "portal/download dataset, no live layer API (e.g. GeoParquet via CLI)",
               "csw": "metadata catalogue — use `discover`, not fetch",
               "xyz": "raster tile template, no queryable layer"}.get(source.type, "not fetchable")
        return [VerifyResult(source.id, None, "skipped", f"{source.type}: {why}; see Status for reachability")]
    if source.auth != "none" and not _has_creds(source):
        return [VerifyResult(source.id, None, "auth_required", f"auth={source.auth}, no creds in env")]
    if source.catalog.get("queryParam") and not (layer or source.test.get("layer")):
        return [VerifyResult(source.id, None, "skipped",
                             f"free-form productType ('{source.catalog['queryParam']}') — "
                             f"set test.layer to a real value to verify")]

    try:
        names = [layer] if layer else _layers_to_test(source, max_layers)
    except Exception as e:
        return [VerifyResult(source.id, None, "fail", f"layer listing failed: {type(e).__name__}: {e}")]
    if not names:
        return [VerifyResult(source.id, None, "skipped", "no testable layer (REST without curated layers)")]

    b, dt = (bbox, datetime)
    if b is None:
        b, auto_dt = _aoi_for(source)
        dt = dt if dt is not None else auto_dt
    return [_verify_one(source, name, b, dt) for name in names]


def _verify_one(source: Source, name: str, bbox: tuple, datetime: str | None) -> VerifyResult:
    import tempfile

    import yaml

    from ..config import load_catalog
    from ..request import GeoRequest
    from .inspect import to_catalog_provider

    t0 = time.perf_counter()
    try:
        block = to_catalog_provider(source, [name])
        if source.type in ("wms", "wmts"):  # keep raster smoke-tests small & fast
            for product in next(iter(block.values()))["products"].values():
                product.setdefault("zoom", _smoke_zoom(bbox))
        with tempfile.TemporaryDirectory(prefix="gdc_verify_") as tmp:
            yml = Path(tmp) / "catalog.yml"
            yml.write_text(yaml.safe_dump(block, sort_keys=False, allow_unicode=True), encoding="utf-8")
            catalog = load_catalog(yml, cache_dir=f"{tmp}/cache")
            if not catalog.layers:
                return _r(source, name, "fail", "provider produced no loadable layer", t0)
            lid, built = next(iter(catalog.layers.items()))
            request = GeoRequest(bbox=tuple(bbox), datetime=datetime)
            result = asyncio.run(_fetch_once(built, request))
    except Exception as e:
        return _r(source, name, "fail", f"{type(e).__name__}: {e}"[:300], t0)

    count, empty, detail = _measure(result.kind, result.data)
    return _r(source, name, "empty" if empty else "ok", detail, t0, count)


def _r(source, name, state, detail, t0, count=None) -> VerifyResult:
    ms = round((time.perf_counter() - t0) * 1000)
    icon = {"ok": "✓", "empty": "∅", "fail": "✗", "skipped": "–", "auth_required": "🔐"}.get(state, "?")
    log.info(f"  {icon} {source.id:<24} {(name or ''):<32} {state:<13} {detail}")
    return VerifyResult(source.id, name, state, detail, count, ms)


#: representative outcome when several layers of one source are tested (best wins):
#: a source that returns data anywhere is "ok"; otherwise surface the failure.
_STATE_RANK = {"ok": 0, "fail": 1, "empty": 2, "auth_required": 3, "skipped": 4}


def apply_results(sources: list[Source], results: list[VerifyResult]) -> int:
    """Write verify outcomes back into each source's ``fetch`` field (like ``check``
    does for ``status``). Returns the number of sources updated."""
    by_src: dict[str, list[VerifyResult]] = {}
    for r in results:
        by_src.setdefault(r.source_id, []).append(r)
    index = {s.id: s for s in sources}
    today = date.today().isoformat()
    updated = 0
    for sid, rs in by_src.items():
        src = index.get(sid)
        if src is None:
            continue
        rep = sorted(rs, key=lambda r: _STATE_RANK.get(r.state, 9))[0]
        src.fetch = {"state": rep.state, "detail": rep.detail[:200],
                     "layer": rep.layer, "checked_at": today}
        if rep.count is not None:
            src.fetch["count"] = rep.count
        updated += 1
    return updated


def verify_sources(sources: list[Source], *, max_layers: int = 1,
                   bbox=None, datetime=None) -> tuple[list[VerifyResult], dict[str, int]]:
    """Verify many sources sequentially; returns (results, {state: count})."""
    log.info(f"Verifying {len(sources)} source(s) — real fetches on small AOIs "
             f"({date.today().isoformat()})")
    results: list[VerifyResult] = []
    for src in sources:
        results.extend(verify_source(src, bbox=bbox, datetime=datetime, max_layers=max_layers))
    summary: dict[str, int] = {}
    for r in results:
        summary[r.state] = summary.get(r.state, 0) + 1
    return results, summary
