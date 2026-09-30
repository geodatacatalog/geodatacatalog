"""Movebank plugin — animal tracking (GPS) → GeoDataFrame of points.

Movebank's public JSON returns nested individuals→locations (and is gzip/rate-limit
sensitive), which the generic REST layer can't parse. This plugin handles it:

* ``productType`` is a numeric **study id** → that study's GPS fixes, one point per
  fix (``individual``, ``timestamp`` columns);
* a non-numeric ``productType`` (e.g. ``public/json``) → the **public study list**,
  one point per study at its reference location (``study_id``, ``name``).

Most studies require a free Movebank login (``auth.credentials`` username/password)
and per-study licence acceptance; public studies work anonymously.

catalog.yml::

    movebank:
      search:
        type: movebank.py
        api_endpoint: https://www.movebank.org/movebank/service
        # study_id: 2911040          # optional default study
      # auth:
      #   credentials: { username: ${MOVEBANK_USER}, password: ${MOVEBANK_PASS} }
      products:
        studies: { productType: public/json }
"""
from __future__ import annotations

import logging

import geopandas as gpd
from shapely.geometry import Point

log = logging.getLogger(__name__)

_EVENT_ATTRS = "individual_local_identifier,timestamp,location_long,location_lat"


def fetch(request, product_type, config):
    """Return GPS points for a study, or the public study locations."""
    import requests

    search = config.get("search") or {}
    base = (search.get("api_endpoint") or "https://www.movebank.org/movebank/service").rstrip("/")
    creds = (config.get("auth") or {}).get("credentials") or {}
    auth = (creds["username"], creds["password"]) if creds.get("username") else None

    pt = str(product_type or "").strip()
    study_id = pt if pt.isdigit() else (search.get("study_id"))

    sess = requests.Session()
    sess.headers["User-Agent"] = "geodatacatalog"
    url = f"{base}/public/json"

    geoms, rows = [], []
    if study_id:  # GPS fixes for one study
        data = sess.get(url, params={"entity_type": "event", "study_id": study_id,
                                     "attributes": _EVENT_ATTRS}, auth=auth, timeout=90).json()
        for ind in (data.get("individuals") or []):
            ident = ind.get("individual_local_identifier")
            for loc in (ind.get("locations") or []):
                lon, lat = loc.get("location_long"), loc.get("location_lat")
                if lon is None or lat is None:
                    continue
                geoms.append(Point(lon, lat))
                rows.append({"individual": ident, "timestamp": loc.get("timestamp")})
    else:  # public study list → one point per study reference location
        data = sess.get(url, params={"entity_type": "study"}, auth=auth, timeout=90).json()
        studies = data if isinstance(data, list) else (data.get("individuals") or data.get("study") or [])
        for st in studies:
            lon, lat = st.get("main_location_long"), st.get("main_location_lat")
            if lon is None or lat is None:
                continue
            geoms.append(Point(lon, lat))
            rows.append({"study_id": st.get("id"), "name": st.get("name")})

    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    log.info(f"[movebank] {len(gdf)} point(s) "
             + (f"for study {study_id}" if study_id else "(public studies)"))
    if request.crs and request.crs != "EPSG:4326" and not gdf.empty:
        gdf = gdf.to_crs(request.crs)
    return gdf
