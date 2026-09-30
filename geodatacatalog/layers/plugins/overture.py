"""Overture Maps plugin — global harmonised features (GeoParquet) → GeoDataFrame.

Overture publishes monthly snapshots built from OSM + Meta + Microsoft + Esri +
Foursquare as cloud-native GeoParquet. This plugin reads them directly with DuckDB
(``httpfs`` over the public ``s3://overturemaps-us-west-2`` bucket), pruned to the
request bbox via Overture's ``bbox`` column — no rate limits, global, fast extracts.
Complementary to ``overpass``/``osmnx`` (live OSM, arbitrary tags): Overture is a
versioned, harmonised snapshot, better for bulk features over large areas.

``productType`` selects the theme:
``places`` · ``buildings`` · ``transportation`` · ``divisions`` · ``addresses`` ·
``land`` · ``water`` · ``infrastructure``.

catalog.yml::

    overture:
      search:
        type: overture.py
        # release: 2026-06-17.0     # optional — the latest is auto-discovered
        # max_features: 50000
      products:
        places:    { productType: places }
        buildings: { productType: buildings }

Requires the ``duckdb`` package.
"""
from __future__ import annotations

import logging
import sys
import threading

import geopandas as gpd

log = logging.getLogger(__name__)

#: A single warm DuckDB connection, reused across fetches. Re-connecting per call
#: would re-LOAD the extensions and, worse, re-read every Parquet footer over S3 on
#: each request (the dominant cost for large themes such as ``transportation``).
#: Keeping it warm lets ``enable_object_cache``/``enable_http_metadata_cache`` cache
#: that metadata between calls, so repeated/nearby bboxes hit the cache.
_con = None
_con_lock = threading.Lock()


def _connection():
    """Lazily build (once) and return the shared, extension-loaded connection."""
    global _con
    if _con is not None:
        return _con
    with _con_lock:
        if _con is not None:
            return _con
        try:
            import duckdb
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(
                "the Overture plugin needs DuckDB — install it with "
                "`pip install geodatacatalog[overture]` (or `pip install duckdb`)"
            ) from e
        con = duckdb.connect()
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; "
                    "SET s3_region='us-west-2'; SET http_retries=5; SET http_keep_alive=true;")
        # Cache Parquet footers / HTTP metadata across queries (names vary by DuckDB
        # version, so apply best-effort).
        for pragma in ("SET enable_object_cache=true",
                       "SET enable_http_metadata_cache=true"):
            try:
                con.execute(pragma)
            except Exception as e:  # pragma not supported on this DuckDB build
                log.debug("[overture] %s unsupported (%s)", pragma, e)
        _con = con
        return _con

_BUCKET = "s3://overturemaps-us-west-2/release"
_LIST_URL = "https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/"
_FALLBACK_RELEASE = "2026-06-17.0"
_release_cache: dict[str, str] = {}

#: productType → (theme, type, extra select columns). ``names``/``categories`` are structs.
_PRODUCTS = {
    "places":         ("places", "place", ["names.primary AS name", "categories.primary AS category"]),
    "buildings":      ("buildings", "building", ["names.primary AS name", "height", "class"]),
    "transportation": ("transportation", "segment",
                       ["names.primary AS name", "class", "subtype", "access_restrictions"]),
    "divisions":      ("divisions", "division_area", ["names.primary AS name", "subtype", "country"]),
    "addresses":      ("addresses", "address", ["street", "number", "postcode"]),
    "land":           ("base", "land", ["names.primary AS name", "class", "subtype"]),
    "water":          ("base", "water", ["names.primary AS name", "class", "subtype"]),
    "infrastructure": ("base", "infrastructure", ["names.primary AS name", "class", "subtype"]),
}
_ALIASES = {"place": "places", "building": "buildings", "segment": "transportation",
            "road": "transportation", "roads": "transportation", "division_area": "divisions",
            "division": "divisions", "address": "addresses"}


def _latest_release() -> str:
    """Newest Overture release tag (cached), via the public S3 listing."""
    if "v" in _release_cache:
        return _release_cache["v"]
    import re

    import requests
    version = _FALLBACK_RELEASE
    try:
        with requests.get(_LIST_URL, timeout=15, stream=True) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                text = raw.decode("utf-8", errors="ignore")
                rels = re.findall(r"release/(\d[\d-]+\.\d+)/", text)
                if rels:
                    version = max(rels)  # release tags sort chronologically
                    break
    except Exception as e:
        log.warning(f"[overture] release discovery failed ({e}); using {version}")
    _release_cache["v"] = version
    return version


def fetch(request, product_type, config):
    """Read Overture features of ``product_type`` within ``request.bbox``."""
    from shapely import wkb

    key = _ALIASES.get(product_type, product_type)
    if key not in _PRODUCTS:
        raise ValueError(f"unknown Overture productType {product_type!r}; "
                         f"choose from {sorted(_PRODUCTS)}")
    theme, otype, extra = _PRODUCTS[key]

    search = config.get("search") or {}
    # Overture only keeps the last couple of releases hosted, so a pinned constant
    # would eventually 404 — resolve the current one lazily (cached in
    # _release_cache after the first call, so only the very first fetch in a
    # process pays for the S3 listing; falls back to _FALLBACK_RELEASE itself if
    # that listing is unreachable).
    release = search.get("release") or _latest_release()
    limit = int(search.get("max_features") or getattr(request, "max_features", None) or 50000)
    minx, miny, maxx, maxy = request.bbox

    # 'transportation'/'buildings' span hundreds of S3 parquet files → the bbox scan
    # is slow and can hit transient S3 drops. Show DuckDB's progress bar in a terminal
    # (so it does not look frozen) and let httpfs retry failed connections.
    progress = "true" if sys.stdout.isatty() else "false"
    # A cursor over the shared warm connection (thread-safe for concurrent layer
    # fetches; shares the metadata caches set up in _connection()).
    cur = _connection().cursor()
    cur.execute(f"SET enable_progress_bar={progress};")
    # ST_AsWKB → standard WKB: with the spatial extension loaded the geometry column
    # is a DuckDB GEOMETRY, whose raw .df() bytes are NOT parseable by shapely.wkb.
    columns = ", ".join(["id", *extra, "ST_AsWKB(geometry) AS geometry"])
    path = f"{_BUCKET}/{release}/theme={theme}/type={otype}/*"
    # bbox-comparison-only WHERE: keep it pushable to the Parquet reader so DuckDB
    # skips non-overlapping row groups via the `bbox` column statistics. A spatial
    # function here (ST_Intersects) would defeat that pushdown → a full global scan
    # for big themes, so the precise clip to the AOI rectangle is done in pandas below.
    sql = (f"SELECT {columns} FROM read_parquet('{path}', hive_partitioning=1) "
           f"WHERE bbox.xmin <= {maxx} AND bbox.xmax >= {minx} "
           f"AND bbox.ymin <= {maxy} AND bbox.ymax >= {miny} "
           f"LIMIT {limit}")
    log.info("[overture] querying %s (release %s) over S3 — large themes (transportation, "
             "buildings) can take a while on a cold cache…", key, release)
    try:
        df = cur.execute(sql).df()
    finally:
        cur.close()

    geoms = [wkb.loads(bytes(g)) for g in df.pop("geometry")]
    gdf = gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")
    # Precise clip to the AOI rectangle (the SQL kept only the pushable bbox-overlap
    # filter, which is a superset): drops features whose envelope overlaps but whose
    # geometry doesn't actually intersect the bbox.
    if not gdf.empty:
        from shapely.geometry import box
        gdf = gdf[gdf.intersects(box(minx, miny, maxx, maxy))].reset_index(drop=True)
    log.info(f"[overture] {len(gdf)} {key} feature(s) from release {release} in {request.bbox}")
    if request.crs and request.crs != "EPSG:4326" and not gdf.empty:
        gdf = gdf.to_crs(request.crs)
    return gdf
