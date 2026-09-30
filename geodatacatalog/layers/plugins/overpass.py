"""OpenStreetMap Overpass API plugin — OSM features in a bbox → GeoDataFrame.

The Overpass API is queried with Overpass QL over POST (the generic REST layer can
only do templated GET), so this plugin builds the QL, posts it, and converts the
elements to geometries: nodes → points, ways → lines/polygons.

``productType`` is an OSM tag filter:
* a key — ``"amenity"`` (any node/way tagged amenity),
* a key=value — ``"amenity=restaurant"``,
* several, separated by ``,`` or ``|`` (any-of) — ``"amenity=cafe|amenity=restaurant"``.

A tag filter alone matches EVERY feature carrying that tag in the bbox (e.g. every
``waterway=river``, not one river). To narrow to a SINGLE named feature, append
``;name=<text>`` — a case-insensitive match ANDed onto every tag filter, e.g.
``"waterway=river;name=Nile"``. It checks several name-ish tags (``name``,
``name:en``, ``int_name``, ``alt_name``), not just ``name``: in many regions the
primary ``name`` is in the local script (e.g. the Nile in Egypt is tagged
``name=نهر النيل``, with the Latin name only in ``name:en``), so matching ``name``
alone would silently return nothing for a large share of the world.

catalog.yml::

    overpass:
      search:
        type: overpass.py
        api_endpoint: https://overpass-api.de/api/interpreter
      products:
        restaurants: { productType: amenity=restaurant }
        roads:       { productType: highway }
        nile:        { productType: "waterway=river;name=Nile" }
"""
from __future__ import annotations

import logging
import re
import time

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

log = logging.getLogger(__name__)

#: Tags checked by a ``;name=`` filter, in addition to the primary ``name``.
_NAME_TAGS = ("name", "name:en", "int_name", "alt_name")


def _ql_escape(value: str) -> str:
    """Escape a value embedded in a double-quoted Overpass QL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _filters(product_type: str) -> list[str]:
    """OSM tag filter(s) (+ optional ``;name=`` filter) → Overpass QL selectors."""
    tags, _, name = (product_type or "").partition(";name=")
    name = name.strip()
    base = []
    for part in re.split(r"[|,]", tags):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            base.append(f'["{key.strip()}"="{value.strip()}"]')
        else:
            base.append(f'["{part}"]')
    if not base:
        base = ['["amenity"]']
    if not name:
        return base
    esc = _ql_escape(name)
    return [f'{b}["{tag}"~"{esc}",i]' for b in base for tag in _NAME_TAGS]


def _geometry(el: dict):
    if el.get("type") == "node" and el.get("lat") is not None:
        return Point(el["lon"], el["lat"])
    if el.get("type") in ("way", "relation"):
        coords = [(p["lon"], p["lat"]) for p in (el.get("geometry") or []) if "lon" in p]
        if len(coords) >= 4 and coords[0] == coords[-1]:
            return Polygon(coords)
        if len(coords) >= 2:
            return LineString(coords)
        c = el.get("center")
        if c:
            return Point(c["lon"], c["lat"])
    return None


def fetch(request, product_type, config):
    """Return OSM features matching ``product_type`` within ``request.bbox``."""
    import requests

    minx, miny, maxx, maxy = request.bbox
    s, w, n, e = miny, minx, maxy, maxx  # Overpass bbox order: south,west,north,east
    search = config.get("search") or {}
    url = search.get("api_endpoint") or "https://overpass-api.de/api/interpreter"
    timeout = int(search.get("timeout", 60))

    # A ;name= filter targets one specific feature. Once a `relation` matches, `out
    # geom` resolves and serialises ALL its members — for a long linear feature
    # (a big river, a long road) that can be the ENTIRE global geometry, far beyond
    # the query bbox, which is slow and prone to timing out the public API. The same
    # name is normally also tagged directly on the individual way segments (verified
    # against the Nile, whose relation carries thousands of members but whose
    # in-bbox `way`s are already named), so skip `relation` and keep node/way only.
    has_name_filter = ";name=" in (product_type or "")
    kinds = ("node", "way") if has_name_filter else ("node", "way", "relation")
    body = "".join(f"  {kind}{flt}({s},{w},{n},{e});\n"
                   for flt in _filters(product_type) for kind in kinds)
    ql = f"[out:json][timeout:{timeout}];\n(\n{body});\nout geom;"

    # overpass-api.de returns 406 to the default python-requests UA → set a real one.
    # The public instance is also prone to transient 502/503/504s under load, even
    # for a well-scoped query (observed directly: back-to-back identical requests
    # alternating between 504 and a 3-7s success) — retry those a couple of times.
    headers = {"User-Agent": "geodatacatalog (https://pypi.org/project/geodatacatalog/)"}
    resp = None
    for attempt in range(3):
        try:
            resp = requests.post(url, data={"data": ql}, timeout=timeout + 15, headers=headers)
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in (502, 503, 504) or attempt == 2:
                raise
            log.warning(f"[overpass] HTTP {status} (attempt {attempt + 1}/3) — retrying")
            time.sleep(2 * (attempt + 1))
    elements = resp.json().get("elements", [])

    geoms, rows = [], []
    for el in elements:
        geom = _geometry(el)
        if geom is None:
            continue
        props = dict(el.get("tags") or {})
        props["osm_id"], props["osm_type"] = el.get("id"), el.get("type")
        geoms.append(geom)
        rows.append(props)

    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    log.info(f"[overpass] {len(gdf)} OSM feature(s) for {product_type!r} in {request.bbox}")
    if request.crs and request.crs != "EPSG:4326" and not gdf.empty:
        gdf = gdf.to_crs(request.crs)
    return gdf
