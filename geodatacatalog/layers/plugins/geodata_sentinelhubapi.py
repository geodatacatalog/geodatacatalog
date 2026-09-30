"""Sentinel Hub (Copernicus Data Space) plugin — Sentinel-2 spectral bands & indices → raster.

Use this when the user asks for a **spectral band** (e.g. ``B08``, ``B04``, or an
alias like ``N``/``R``) or a spectral **index** (e.g. ``NDVI``, ``NBR``, ``BAI`` —
any `spyndex` Sentinel-2 index). It calls the Sentinel Hub Process API on
Sentinel-2 L2A (cloud-masked with the SCL band), composites the least-cloudy
scenes over the request's date window, and returns an ``xarray.DataArray``
(reflectance for a band, the computed value for an index) clipped to the bbox.

**Two-date difference** (change detection): prefix any index/band with ``d``
(``dBAI``, ``dNBR``, ``dNDVI``...) and pass a datetime **range** — the plugin
composites the base index over ~30 days ending at each end of the range and
returns ``index[date2] - index[date1]`` (e.g. ``dBAI = BAI2 - BAI1``, positive
where burn increased). A **single date** compares the ~30 days before it to the
previous ~30 days (recent change at that date). A plain uppercase spyndex name
(``DVI``, ``DBSI``) is always resolved as that index, never as a difference.

catalog.yml::

    sentinelhub:
      search:
        type: geodata_sentinelhubapi.py            # shipped: resolved automatically
        api_endpoint: https://sh.dataspace.copernicus.eu/api/v1/process
        need_auth: true
      auth:
        credentials:
          client_id: ${CDSE_CLIENT_ID}
          client_secret: ${CDSE_CLIENT_SECRET}
      products:
        ndvi: { productType: NDVI }                 # spyndex index
        nir:  { productType: B08 }                  # raw spectral band
        dnbr: { productType: dNBR }                 # two-date difference (datetime range required)

Credentials: create an OAuth client at
https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings and set
``CDSE_CLIENT_ID`` / ``CDSE_CLIENT_SECRET`` (env, or auth.credentials). No secret
is hard-coded here.
"""
from __future__ import annotations

import io
import logging
import math
import os
import re
import time

log = logging.getLogger(__name__)

_DEFAULT_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_DEFAULT_DATE_FROM, _DEFAULT_DATE_TO = "2024-06-01", "2024-06-30"
_MAX_PX = 2500  # Sentinel Hub Process API per-axis limit
#: friendly alias → Sentinel-2 band; raw band codes (B01..B12, B8A) also accepted
_BAND_MAP = {"A": "B01", "B": "B02", "G": "B03", "R": "B04", "RE1": "B05",
             "RE2": "B06", "RE3": "B07", "N": "B08", "N2": "B8A", "WV": "B09",
             "S1": "B11", "S2": "B12"}
_S2_BANDS = set(_BAND_MAP.values())
#: common Sentinel-2 indices that don't need spyndex to work
_COMMON_INDEX_FORMULAS = {
    "NDMI": {"formula": "(B08 - B11) / (B08 + B11)", "bands": ["B08", "B11"]},
    "NDVI": {"formula": "(B08 - B04) / (B08 + B04)", "bands": ["B08", "B04"]},
    "NBR": {"formula": "(B08 - B12) / (B08 + B12)", "bands": ["B08", "B12"]},
    "NDWI": {"formula": "(B03 - B08) / (B03 + B08)", "bands": ["B03", "B08"]},
    "EVI": {"formula": "2.5 * (B08 - B04) / (B08 + 6 * B04 - 7.5 * B02 + 1)",
            "bands": ["B08", "B04", "B02"]},
}
#: SCL classes masked out (no data, clouds, shadows, cirrus, snow)
_SCL_MASK = [0, 1, 3, 6, 8, 9, 10, 11]
_token_cache: dict = {}          # {"token": str, "exp": float}
_s2_indices_cache: dict | None = None


def _credentials(config: dict) -> tuple[str, str]:
    creds = (config.get("auth") or {}).get("credentials") or {}
    cid = creds.get("client_id") or os.environ.get("CDSE_CLIENT_ID")
    secret = creds.get("client_secret") or os.environ.get("CDSE_CLIENT_SECRET")
    if not (cid and secret):
        raise RuntimeError(
            "Sentinel Hub needs CDSE_CLIENT_ID / CDSE_CLIENT_SECRET (env or "
            "auth.credentials) — register at https://shapps.dataspace.copernicus.eu/dashboard/"
        )
    return cid, secret


def _get_token(client_id: str, client_secret: str) -> str:
    import httpx
    if _token_cache.get("exp", 0) > time.time():
        return _token_cache["token"]
    r = httpx.post(_TOKEN_URL, data={"grant_type": "client_credentials",
                                     "client_id": client_id, "client_secret": client_secret},
                   headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Sentinel Hub auth failed {r.status_code}: {r.text}")
    payload = r.json()
    _token_cache.update(token=payload["access_token"],
                        exp=time.time() + int(payload.get("expires_in", 600)) - 60)
    return _token_cache["token"]


def _s2_indices() -> dict:
    """Known Sentinel-2 indices (lazy, cached), with a built-in fallback if spyndex fails."""
    global _s2_indices_cache
    if _s2_indices_cache is None:
        out = {}
        try:
            import spyndex as sp
        except Exception:
            sp = None

        if sp is not None:
            for name, idx in sp.indices.items():
                platforms = [str(p) for p in (idx.platforms or [])]
                bands = [str(b) for b in idx.bands]
                if any("Sentinel-2" in p for p in platforms) and all(b in _BAND_MAP for b in bands):
                    out[name] = {"formula": idx.formula, "bands": bands,
                                 "sh_bands": [_BAND_MAP[b] for b in bands],
                                 "long_name": getattr(idx, "long_name", "") or ""}
        else:
            for name, idx in _COMMON_INDEX_FORMULAS.items():
                out[name] = {"formula": idx["formula"], "bands": idx["bands"],
                             "sh_bands": idx["bands"], "long_name": ""}

        _s2_indices_cache = out
    return _s2_indices_cache


def _diff_base(product_type: str) -> str | None:
    """Base index/band of a two-date difference productType (``dNBR`` → ``NBR``).

    Returns ``None`` when ``product_type`` is not a difference — in particular a
    real spyndex index starting with ``D`` (``DVI``, ``DBSI``...) always wins
    over the difference interpretation.
    """
    pt = product_type.strip()
    if len(pt) < 2 or pt[0] not in ("d", "D") or pt in _s2_indices():
        return None
    base = pt[1:]
    if base in _s2_indices() or base.upper() in _BAND_MAP or base.upper() in _S2_BANDS:
        return base
    return None


def list_products() -> list[dict]:
    """Every productType this plugin accepts → ``[{"id", "title"}, ...]``.

    The catalog/explorer calls this to enumerate the free-form productTypes (all
    ``spyndex`` Sentinel-2 indices + the raw bands, plus their ``d``-prefixed
    two-date differences), beyond the curated few in sources.yml.
    """
    indices = _s2_indices()
    out = [{"id": name, "title": f"{name} — {meta['long_name']}" if meta.get("long_name") else name}
           for name, meta in sorted(indices.items())]
    out += [{"id": band, "title": f"Bande {band}"} for band in sorted(_S2_BANDS)]
    out += [{"id": f"d{name}",
             "title": f"d{name} — différence {name} entre deux dates (date2 − date1)"}
            for name in sorted(indices) if f"d{name}" not in indices]
    return out


def _evalscript(product_type: str) -> tuple[str, list[str]]:
    """Evalscript + the SH bands to request, for a raw band or a spyndex index."""
    pt = product_type.strip()
    band = _BAND_MAP.get(pt.upper(), pt.upper() if pt.upper() in _S2_BANDS else None)
    if band:  # a single spectral band → its surface reflectance (0–1), SCL-masked
        return (f"""//VERSION=3
function setup() {{ return {{ input: ["{band}", "SCL"],
  output: {{ bands: 1, sampleType: "FLOAT32" }} }}; }}
function evaluatePixel(s) {{ if ({_SCL_MASK}.includes(s.SCL)) return [NaN];
  return [s.{band} / 10000.0]; }}""", [band])

    indices = _s2_indices()
    if pt not in indices:
        raise ValueError(
            f"'{product_type}' is neither a Sentinel-2 band ({sorted(_S2_BANDS)} "
            f"or aliases {sorted(_BAND_MAP)}) nor a known spyndex index"
        )
    idx = indices[pt]
    decls = "\n  ".join(f"const {b} = s.{sh} / 10000.0;"
                        for b, sh in zip(idx["bands"], idx["sh_bands"]))
    formula = re.sub(r"(\w+)\s*\*\*\s*(\w+)", r"Math.pow(\1,\2)", idx["formula"])
    return (f"""//VERSION=3
function setup() {{ return {{ input: {idx["sh_bands"] + ["SCL"]},
  output: {{ bands: 1, sampleType: "FLOAT32" }} }}; }}
function evaluatePixel(s) {{ if ({_SCL_MASK}.includes(s.SCL)) return [NaN];
  {decls}
  const v = {formula};
  return [isNaN(v) ? NaN : v]; }}""", idx["sh_bands"])


def _output_size(bbox, resolution) -> tuple[int, int]:
    minx, miny, maxx, maxy = bbox
    res_m = float(resolution) if resolution else 10.0  # Sentinel-2 native ≈ 10 m
    mx = 111_320 * math.cos(math.radians((miny + maxy) / 2))
    width = max(1, min(_MAX_PX, round((maxx - minx) * mx / res_m)))
    height = max(1, min(_MAX_PX, round((maxy - miny) * 111_320 / res_m)))
    return width, height


def _date_window(datetime_str):
    if not datetime_str:
        return _DEFAULT_DATE_FROM, _DEFAULT_DATE_TO
    parts = [p[:10] for p in datetime_str.split("/") if p[:10]]
    if len(parts) > 1:
        return parts[0], parts[1]
    # A single date rarely has a clear Sentinel-2 acquisition (5-day revisit + SCL
    # cloud masking → all-nodata). Widen to a 30-day window ending on that date.
    import datetime as _dt
    try:
        end = _dt.date.fromisoformat(parts[0])
        return (end - _dt.timedelta(days=30)).isoformat(), end.isoformat()
    except ValueError:
        return parts[0], parts[0]


def _diff_windows(datetime_str, product_type) -> tuple[tuple[str, str], tuple[str, str]]:
    """Two ~30-day compositing windows for a difference index.

    A datetime range gives one window ending at each date; a single date compares
    the ~30 days before it to the previous ~30 days (recent change at that date).
    """
    import datetime as _dt

    parts = [p[:10] for p in (datetime_str or "").split("/") if p[:10]]
    try:
        dates = sorted(_dt.date.fromisoformat(p) for p in parts)
    except ValueError:
        dates = []
    if not dates:
        raise ValueError(
            f"'{product_type}' is a two-date difference index: pass a datetime range "
            f"'YYYY-MM-DD/YYYY-MM-DD' (or a single date = that month vs the month "
            f"before) — {product_type[1:]} is composited over ~30 days ending at each "
            f"date, then differenced (date2 - date1)"
        )
    win = _dt.timedelta(days=30)
    if len(dates) == 1 or dates[0] == dates[-1]:
        d2 = dates[0]
        d1 = d2 - win - _dt.timedelta(days=1)
    else:
        d1, d2 = dates[0], dates[-1]
    # keep the second window after date1 so close dates don't composite the same scenes
    d2_from = max(d2 - win, d1 + _dt.timedelta(days=1))
    return (((d1 - win).isoformat(), d1.isoformat()), (d2_from.isoformat(), d2.isoformat()))


def _process(evalscript, bbox, width, height, date_from, date_to, process_url, get_token):
    """One Process API call → float32 (h, w) array of the composited window."""
    import httpx
    import numpy as np
    import tifffile

    payload = {
        "input": {
            "bounds": {"bbox": bbox,
                       "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-2-l2a",
                      "dataFilter": {"timeRange": {"from": f"{date_from}T00:00:00Z",
                                                   "to": f"{date_to}T23:59:59Z"},
                                     "mosaickingOrder": "leastCC", "maxCloudCoverage": 30}}],
        },
        "output": {"width": width, "height": height,
                   "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
        "evalscript": evalscript,
    }

    for attempt in range(5):  # CDSE rate-limits; back off on 429
        r = httpx.post(process_url, json=payload,
                       headers={"Authorization": f"Bearer {get_token()}"}, timeout=60)
        if r.status_code == 429 and attempt < 4:
            time.sleep(2 ** (attempt + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Sentinel Hub {r.status_code}: {r.text[:300]}")
        break

    arr = np.asarray(tifffile.imread(io.BytesIO(r.content)), dtype="float32")
    if arr.ndim == 3:  # (h, w, 1) → (h, w)
        arr = arr[..., 0]
    return arr


def fetch(request, product_type, config):
    """Fetch a Sentinel-2 band/index — or its two-date difference — → xarray.DataArray."""
    import numpy as np
    import xarray as xr

    client_id, client_secret = _credentials(config)
    bbox = list(request.bbox)
    minx, miny, maxx, maxy = bbox
    width, height = _output_size(bbox, getattr(request, "resolution", None))
    process_url = (config.get("search") or {}).get("api_endpoint") or _DEFAULT_PROCESS_URL
    get_token = lambda: _get_token(client_id, client_secret)

    base = _diff_base(product_type)
    if base:  # d<INDEX>: composite the base index at each date, subtract (date2 - date1)
        evalscript, _ = _evalscript(base)
        (d1_from, d1_to), (d2_from, d2_to) = _diff_windows(
            getattr(request, "datetime", None), product_type)
        arr1 = _process(evalscript, bbox, width, height, d1_from, d1_to, process_url, get_token)
        arr2 = _process(evalscript, bbox, width, height, d2_from, d2_to, process_url, get_token)
        arr = arr2 - arr1  # NaN (masked in either epoch) propagates
        date_from, date_to = d1_from, d2_to
        attrs = {"product_type": product_type,
                 "definition": f"{base}[{d2_from}→{d2_to}] - {base}[{d1_from}→{d1_to}]",
                 "date1_from": d1_from, "date1_to": d1_to,
                 "date2_from": d2_from, "date2_to": d2_to}
    else:
        evalscript, _ = _evalscript(product_type)
        date_from, date_to = _date_window(getattr(request, "datetime", None))
        arr = _process(evalscript, bbox, width, height, date_from, date_to, process_url, get_token)
        attrs = {"product_type": product_type}

    attrs.update(date_from=date_from, date_to=date_to)
    h, w = arr.shape
    # pixel-centre coords over the bbox (y descending, north→south) → rioxarray
    # derives the transform from them, so reproject() works reliably.
    px, py = (maxx - minx) / w, (maxy - miny) / h
    xs = np.linspace(minx + px / 2, maxx - px / 2, w)
    ys = np.linspace(maxy - py / 2, miny + py / 2, h)
    da = xr.DataArray(
        arr, dims=("y", "x"), coords={"y": ys, "x": xs}, name=product_type, attrs=attrs,
    ).rio.write_crs("EPSG:4326")
    log.info(f"[sentinelhub] {product_type} {arr.shape[1]}×{arr.shape[0]}px "
             f"[{date_from}→{date_to}] bbox={bbox}")
    if getattr(request, "crs", None) and request.crs != "EPSG:4326":
        da = da.rio.reproject(request.crs)
    return da
