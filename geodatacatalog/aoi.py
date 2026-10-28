"""AOI helpers — turn a place name into a ready-to-use AOI GeoDataFrame.

Thin convenience layer over :func:`geodatacatalog.explorer.geocode.geocode_place`
(pure Nominatim, no LLM) for scripts that just want
``GeoRequest(aoi_gdf=geocode_aoi("Haute-Garonne, France"))``.
"""
from __future__ import annotations

import geopandas as gpd

from .explorer.geocode import geocode_place


def geocode_aoi(query: str, *, limit: int = 1, require_polygon: bool = False) -> gpd.GeoDataFrame:
    """Geocode ``query`` via Nominatim and return it as a one-row AOI GeoDataFrame.

    Uses the real polygon/multipolygon Nominatim returns when available (e.g. an
    administrative boundary), else falls back to its bounding box. Columns:
    ``id`` (``query``, so multi-row ``aoi_gdf`` usage keeps a stable label),
    ``display_name``, ``geometry`` (EPSG:4326).

    Args:
        query: Free-form place name, e.g. ``"Haute-Garonne, France"``.
        limit: How many Nominatim candidates to consider; only the first
            (best-ranked) one is returned.
        require_polygon: Raise instead of silently falling back to a bbox when
            Nominatim has no polygon for ``query``.

    Raises:
        ValueError: Nominatim found nothing, or (with ``require_polygon``) only
            returned a bbox.
    """
    from shapely.geometry import box, shape

    hits = geocode_place(query, limit=limit)
    if not hits:
        raise ValueError(f"Nominatim found nothing for {query!r}")
    hit = hits[0]
    if hit["geometry"] is not None:
        geom = shape(hit["geometry"])
    elif require_polygon:
        raise ValueError(
            f"Nominatim only returned a bbox for {query!r} (no polygon) — "
            "try a more specific query, or pass require_polygon=False"
        )
    else:
        geom = box(*hit["bbox"])
    return gpd.GeoDataFrame(
        {"id": [query], "display_name": [hit["display_name"]]},
        geometry=[geom], crs="EPSG:4326",
    )
