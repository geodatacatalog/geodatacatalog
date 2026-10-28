"""Place-name geocoding via OpenStreetMap Nominatim — resolve a real AOI.

Turns a named place ("Hérault", "Haute-Garonne") into an actual area of interest
— a polygon when Nominatim has one, otherwise a bounding box — for
:func:`geodatacatalog.geocode_aoi`.

Nominatim usage policy: a descriptive ``User-Agent`` is mandatory and requests
are capped at 1/second — both are enforced here.
"""
from __future__ import annotations

import time

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

_last_call = 0.0


def _rate_limit() -> None:
    """Sleep so consecutive Nominatim calls stay at least 1 second apart."""
    global _last_call
    wait = 1.0 - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _user_agent() -> str:
    try:
        from .. import __version__
        return f"geodatacatalog/{__version__} (https://pypi.org/project/geodatacatalog/)"
    except Exception:
        return "geodatacatalog (https://pypi.org/project/geodatacatalog/)"


def geocode_place(query: str, *, limit: int = 1) -> list[dict]:
    """Resolve a place name to an AOI via Nominatim.

    Returns ``[{display_name, geometry, bbox, country_code, osm_type,
    category}, ...]`` sorted by Nominatim relevance. ``geometry`` is the GeoJSON
    geometry (Polygon/MultiPolygon/LineString/Point) in EPSG:4326 when Nominatim
    provides one (the real AOI), else ``None``. ``bbox`` is ``(lon_min, lat_min,
    lon_max, lat_max)`` in EPSG:4326 and is always present (fallback / display).
    """
    import requests

    _rate_limit()
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "polygon_geojson": 1,
                "addressdetails": 1, "limit": limit},
        headers={"User-Agent": _user_agent()},
        timeout=30,
    )
    resp.raise_for_status()
    out = []
    for r in resp.json():
        bb = r.get("boundingbox")  # ["lat_min","lat_max","lon_min","lon_max"] (strings)
        bbox = None
        if bb and len(bb) == 4:
            lat_min, lat_max, lon_min, lon_max = (float(x) for x in bb)
            bbox = (lon_min, lat_min, lon_max, lat_max)
        out.append({
            "display_name": r.get("display_name", query),
            "geometry": r.get("geojson"),  # None when unavailable
            "bbox": bbox,
            "country_code": (r.get("address") or {}).get("country_code"),
            "osm_type": r.get("osm_type"),
            "category": r.get("category") or r.get("class"),
        })
    return out
