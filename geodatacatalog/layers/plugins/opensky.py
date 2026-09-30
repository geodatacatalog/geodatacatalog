"""OpenSky Network plugin — live aircraft positions (ADS-B) → GeoDataFrame.

The OpenSky ``/states/all`` endpoint returns each aircraft as a *positional
array* (a "state vector"), not a JSON object, so the generic ``RESTLayer`` cannot
geolocate it. This plugin maps those arrays to points.

catalog.yml::

    opensky:
      search:
        type: opensky.py                       # shipped: resolved automatically
        api_endpoint: https://opensky-network.org/api
      # optional — a free OpenSky account raises the anonymous rate limit:
      # auth:
      #   credentials:
      #     username: ${OPENSKY_USER}
      #     password: ${OPENSKY_PASS}
      products:
        aircraft:
          productType: states/all

The bbox of the :class:`~geodatacatalog.GeoRequest` becomes the
``lamin/lomin/lamax/lomax`` filter; ``datetime`` is ignored (live snapshot).
"""
from __future__ import annotations

import logging

import geopandas as gpd
from shapely.geometry import Point

log = logging.getLogger(__name__)

#: Field order of an OpenSky state vector (https://openskynetwork.github.io/opensky-api/).
_STATE_FIELDS = [
    "icao24", "callsign", "origin_country", "time_position", "last_contact",
    "longitude", "latitude", "baro_altitude", "on_ground", "velocity",
    "true_track", "vertical_rate", "sensors", "geo_altitude", "squawk", "spi",
    "position_source", "category",
]


def fetch(request, product_type, config):
    """Return live aircraft within ``request.bbox`` as a point GeoDataFrame."""
    import requests

    if product_type and "state" not in product_type.lower():
        raise ValueError(
            f"opensky plugin only serves live state vectors (productType 'states/all'); "
            f"got {product_type!r}"
        )

    minx, miny, maxx, maxy = request.bbox
    search = config.get("search") or {}
    base = (search.get("api_endpoint") or "https://opensky-network.org/api").rstrip("/")

    creds = (config.get("auth") or {}).get("credentials") or {}
    user, password = creds.get("username"), creds.get("password")
    auth = (user, password) if user and password else None

    resp = requests.get(
        f"{base}/states/all",
        params={"lamin": miny, "lomin": minx, "lamax": maxy, "lomax": maxx},
        auth=auth,
        timeout=int(search.get("timeout", 30)),
    )
    resp.raise_for_status()
    states = (resp.json() or {}).get("states") or []

    geoms, rows = [], []
    for state in states:
        rec = dict(zip(_STATE_FIELDS, state))
        lon, lat = rec.get("longitude"), rec.get("latitude")
        if lon is None or lat is None:
            continue  # aircraft with no position fix
        if isinstance(rec.get("callsign"), str):
            rec["callsign"] = rec["callsign"].strip()
        geoms.append(Point(lon, lat))
        rows.append(rec)

    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    log.info(f"[opensky] {len(gdf)} aircraft in bbox {request.bbox}"
             + ("" if auth else " (anonymous — rate-limited)"))
    if request.crs and request.crs != "EPSG:4326" and not gdf.empty:
        gdf = gdf.to_crs(request.crs)
    return gdf
