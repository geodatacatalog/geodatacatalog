"""Place-name geocoding via OpenStreetMap Nominatim — resolve a real AOI.

Used by the AI chat (``geodatacatalog chat``) to turn a named place ("the Lys
river", "Hérault") into an actual area of interest — a polygon when Nominatim
has one, otherwise a bounding box — instead of letting the model invent
coordinates.

Nominatim usage policy: a descriptive ``User-Agent`` is mandatory and requests
are capped at 1/second — both are enforced here.
"""
from __future__ import annotations

import time

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

#: ISO 3166-1 alpha-2 codes considered European, so a European AOI also accepts
#: ``europe``-wide sources. Not exhaustive — extend as needed.
_EUROPE = {
    "fr", "be", "nl", "lu", "de", "es", "pt", "it", "ch", "at", "gb", "ie",
    "dk", "se", "no", "fi", "is", "pl", "cz", "sk", "hu", "si", "hr", "ro",
    "bg", "gr", "ee", "lv", "lt", "ua", "rs", "ba", "me", "mk", "al", "md",
    "by", "li", "mt", "cy",
}

#: ISO country code → the registry ``coverage`` label naming that country. Only
#: the labels the registry actually uses need an entry; others fall back to
#: continent/global.
_COUNTRY_COVERAGE = {
    "fr": "france",
    "de": "germany",
    "be": "belgium",
    "us": "usa",
}

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


def accepted_coverages(country_code: str | None) -> set[str]:
    """Registry ``coverage`` labels acceptable for an AOI in ``country_code``.

    Always includes ``global``; adds ``europe`` for European countries and the
    country's own label (e.g. ``france``) when the registry uses one. Used to
    keep proposed sources within the requested area.
    """
    accepted = {"global"}
    if not country_code:
        return accepted
    cc = country_code.lower()
    if cc in _EUROPE:
        accepted.add("europe")
    if cc in _COUNTRY_COVERAGE:
        accepted.add(_COUNTRY_COVERAGE[cc])
    return accepted


#: coverage label → approximate extent (lon_min, lat_min, lon_max, lat_max). Lets
#: us filter sources for an AOI that has no country code (e.g. a sea such as the
#: Mediterranean), so a country-specific source (``finland``) is not offered for a
#: disjoint area.
_COVERAGE_BBOX: dict[str, tuple] = {
    "france": (-5.5, 41.0, 9.8, 51.5),
    "europe": (-25.0, 34.0, 45.0, 72.0),
    "usa": (-125.0, 24.0, -66.0, 49.5),
    "uk": (-8.7, 49.8, 2.0, 61.0),
    "germany": (5.8, 47.2, 15.1, 55.1),
    "spain": (-9.5, 35.9, 4.4, 43.8),
    "norway": (4.0, 57.9, 31.5, 71.4),
    "finland": (19.0, 59.5, 31.6, 70.1),
    "netherlands": (3.3, 50.7, 7.3, 53.6),
    "belgium": (2.5, 49.5, 6.4, 51.5),
    "switzerland": (5.9, 45.8, 10.5, 47.9),
    "canada": (-141.0, 41.6, -52.6, 83.2),
    "australia": (112.9, -43.7, 153.7, -10.0),
    "new-zealand": (166.0, -47.4, 178.6, -34.0),
    "africa": (-20.0, -35.0, 52.0, 38.0),
}


def _bbox_intersect(a: tuple, b: tuple) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def coverages_for_bbox(bbox) -> set[str]:
    """Coverage labels whose approximate extent intersects an AOI ``bbox``.

    Always includes ``global``; empty when ``bbox`` is unknown (caller keeps all).
    """
    if not bbox:
        return set()
    accepted = {"global"}
    for label, ext in _COVERAGE_BBOX.items():
        if _bbox_intersect(ext, tuple(bbox)):
            accepted.add(label)
    return accepted


def recommended_coverages(country_code: str | None, bbox=None) -> set[str]:
    """Accepted coverage labels for an AOI: by country code when known, else by
    bbox extent (so marine/uncoded AOIs still filter out disjoint countries)."""
    return accepted_coverages(country_code) if country_code else coverages_for_bbox(bbox)
