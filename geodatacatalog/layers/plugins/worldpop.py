"""WorldPop plugin — gridded population total for the bbox → 1-row GeoDataFrame.

The WorldPop stats API is asynchronous (submit a geometry → poll a task id →
``total_population``), which the generic REST layer can't drive. This plugin runs
that flow and returns one feature: the bbox polygon carrying the population sum.

``productType`` selects the dataset (default ``wpgppop``); the year comes from
``request.datetime`` (else 2020).

catalog.yml::

    worldpop:
      search:
        type: worldpop.py
        api_endpoint: https://api.worldpop.org/v1
      products:
        population: { productType: wpgppop }
"""
from __future__ import annotations

import json
import logging
import time

import geopandas as gpd
from shapely.geometry import box

log = logging.getLogger(__name__)

_POLL_TRIES, _POLL_DELAY = 30, 2


def _year(datetime_str) -> str:
    year = 2020
    if datetime_str:
        years = [int(p[:4]) for p in datetime_str.split("/") if p[:4].isdigit()]
        if years:
            year = years[-1]
    return str(min(max(year, 2000), 2020))  # wpgppop covers 2000–2020


def fetch(request, product_type, config):
    """Submit the bbox to the WorldPop stats API, poll, return the population total."""
    import requests

    minx, miny, maxx, maxy = request.bbox
    search = config.get("search") or {}
    base = (search.get("api_endpoint") or "https://api.worldpop.org/v1").rstrip("/")
    dataset = product_type if product_type and "/" not in product_type else "wpgppop"
    year = _year(getattr(request, "datetime", None))
    # WorldPop wants the bare geometry (not a Feature)
    geom = json.dumps({"type": "Polygon", "coordinates": [[
        [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]]})

    sub = {}
    for attempt in range(3):  # WorldPop throttles rapid submits → back off and retry
        sub = requests.get(f"{base}/services/stats",
                           params={"dataset": dataset, "year": year, "geojson": geom}, timeout=60).json()
        if not sub.get("error") and sub.get("taskid"):
            break
        time.sleep(_POLL_DELAY * (attempt + 1))
    if sub.get("error") or not sub.get("taskid"):
        raise RuntimeError(f"WorldPop: {sub.get('message') or sub.get('error_message') or sub}")
    taskid = sub["taskid"]

    for _ in range(_POLL_TRIES):
        task = requests.get(f"{base}/tasks/{taskid}", timeout=30).json()
        pop = (task.get("data") or {}).get("total_population")
        if pop is not None:  # the total appears as soon as it is computed (status may still be "started")
            log.info(f"[worldpop] {dataset} {year}: total_population={pop} over {request.bbox}")
            gdf = gpd.GeoDataFrame(
                [{"dataset": dataset, "year": year, "total_population": pop}],
                geometry=[box(minx, miny, maxx, maxy)], crs="EPSG:4326")
            if request.crs and request.crs != "EPSG:4326":
                gdf = gdf.to_crs(request.crs)
            return gdf
        if task.get("status") == "finished":  # finished without data → real error
            raise RuntimeError(f"WorldPop: {task.get('error_message') or 'no data'}")
        time.sleep(_POLL_DELAY)
    raise TimeoutError(f"WorldPop task {taskid} did not finish in {_POLL_TRIES * _POLL_DELAY}s")
