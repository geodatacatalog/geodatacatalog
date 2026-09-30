"""Copernicus Climate Data Store (CDS) plugin — ERA5 reanalysis via ``cdsapi``.

The CDS API is a submit-a-job/poll/download flow (not a plain REST GET), which
the generic REST layer can't drive — ``cdsapi.Client().retrieve()`` handles that
whole flow itself (it blocks until the job is ready, then downloads the NetCDF).
Unlike Planetary Computer's ``era5-pds`` (a frozen Zarr mirror stopping ~Dec 2020,
see ``planetary_computer__era5_pds`` in this catalog), CDS is the live archive and
covers the full ERA5 period (1940-present, with ERA5T "preliminary" data for the
last ~5 days).

One catalog product per variable, named after the CDS long variable name (see
https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels):

catalog.yml::

    era5_cds:
      search:
        type: era5_cds.py
        need_auth: true
      auth:
        credentials:
          url: ${CDSAPI_URL}
          key: ${CDSAPI_KEY}
      products:
        temperature_2m:   { productType: 2m_temperature }
        dewpoint_2m:       { productType: 2m_dewpoint_temperature }
        wind_u10:          { productType: 10m_u_component_of_wind }
        wind_v10:          { productType: 10m_v_component_of_wind }
        surface_pressure:  { productType: surface_pressure }
        precipitation:     { productType: total_precipitation }
        ssrd:              { productType: surface_solar_radiation_downwards }
        strd:              { productType: surface_thermal_radiation_downwards }
        fdir:              { productType: total_sky_direct_solar_radiation_at_surface }

Credentials: free account → https://cds.climate.copernicus.eu, API key →
https://cds.climate.copernicus.eu/how-to-api (accept the ERA5 dataset licence on
the CDS website first, or the retrieve call is rejected). Set ``CDSAPI_URL`` /
``CDSAPI_KEY`` in ``.env`` — no secret is hard-coded here.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_DATASET = "reanalysis-era5-single-levels"

#: CDS request variable name → short name the returned NetCDF actually uses.
_SHORT_NAME = {
    "2m_temperature": "t2m",
    "2m_dewpoint_temperature": "d2m",
    "10m_u_component_of_wind": "u10",
    "10m_v_component_of_wind": "v10",
    "surface_pressure": "sp",
    "mean_sea_level_pressure": "msl",
    "surface_solar_radiation_downwards": "ssrd",
    "surface_thermal_radiation_downwards": "strd",
    "total_precipitation": "tp",
    "total_sky_direct_solar_radiation_at_surface": "fdir",
}


def _dates(datetime_str: str | None) -> tuple[list[str], list[str], list[str]]:
    """``request.datetime`` ("start/end" or a lone date) -> sorted (years, months, days)."""
    from datetime import date, timedelta

    if not datetime_str:
        end = date.today()
        start = end - timedelta(days=7)
    else:
        parts = datetime_str.split("/")
        start = date.fromisoformat(parts[0][:10])
        end = date.fromisoformat(parts[-1][:10]) if len(parts) > 1 else start
        if end < start:
            start, end = end, start

    span = []
    d = start
    while d <= end:
        span.append(d)
        d += timedelta(days=1)
    years = sorted({d.strftime("%Y") for d in span})
    months = sorted({d.strftime("%m") for d in span})
    days = sorted({d.strftime("%d") for d in span})
    return years, months, days


#: Native ERA5 single-levels grid resolution (degrees).
_GRID = 0.25


def _area(bbox: tuple[float, float, float, float]) -> list[float]:
    """``request.bbox`` (minx, miny, maxx, maxy, EPSG:4326) -> CDS ``area`` [N, W, S, E].

    Bounds are snapped outward to the 0.25° ERA5 grid: MARS rejects any area
    containing no grid point ("Assertion failed: Area: non-empty area crop/mask"),
    which every bbox smaller than one grid cell triggers.
    """
    import math

    minx, miny, maxx, maxy = bbox
    west = math.floor(minx / _GRID) * _GRID
    east = math.ceil(maxx / _GRID) * _GRID
    south = math.floor(miny / _GRID) * _GRID
    north = math.ceil(maxy / _GRID) * _GRID
    if east <= west:
        east = west + _GRID
    if north <= south:
        north = south + _GRID
    return [min(north, 90.0), west, max(south, -90.0), east]


def _client(config: dict):
    import cdsapi

    creds = (config.get("auth") or {}).get("credentials") or {}
    url, key = creds.get("url"), creds.get("key")
    if not key:
        raise RuntimeError(
            "CDS API needs credentials — set CDSAPI_KEY (and CDSAPI_URL) in .env "
            "(free account: https://cds.climate.copernicus.eu, key: "
            "https://cds.climate.copernicus.eu/how-to-api)"
        )
    return cdsapi.Client(url=url, key=key)


def fetch(request, product_type: str, config: dict):
    """Fetch one ERA5 variable over ``request.bbox``/``request.datetime`` from the CDS.

    ``product_type`` is the CDS long variable name (e.g. ``"2m_temperature"``,
    ``"total_precipitation"``) — one catalog product per variable, since a CDS
    request only returns a single dataset per call.
    """
    import xarray as xr

    client = _client(config)
    years, months, days = _dates(getattr(request, "datetime", None))
    area = _area(request.bbox)

    cds_request = {
        "product_type": "reanalysis",
        "variable": [product_type],
        "year": years,
        "month": months,
        "day": days,
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": area,
        "data_format": "netcdf",
    }

    log.info(f"[era5_cds] {product_type} {years[0]}-{months[0]} → "
             f"{years[-1]}-{months[-1]}, area={area}")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "era5.nc"
        client.retrieve(_DATASET, cds_request, str(target))
        ds = xr.open_dataset(target)
        ds.load()  # pull into memory — the temp file is deleted on exit

    # the new CDS backend's NetCDF (triggered by "data_format" vs the deprecated
    # "format" key) names the temporal dim "valid_time" instead of "time" — rename
    # so downstream consumers (inspect.py's `.isel(time=-1)`, etc.) keep working.
    if "valid_time" in ds.dims and "time" not in ds.dims:
        ds = ds.rename({"valid_time": "time"})

    var_name = _SHORT_NAME.get(product_type, product_type)
    if var_name not in ds.data_vars:
        var_name = next(iter(ds.data_vars))  # unknown/renamed var → the one data var present
    da = ds[var_name]

    # ERA5T (preliminary, last ~5 days) and ERA5 (final) data share a request that
    # straddles the release boundary get stacked on an "expver" dim (1=final,
    # 5=preliminary), each cell populated in exactly one of the two → skipna-mean
    # collapses it back to a plain (time, lat, lon) array without picking wrong.
    if "expver" in da.dims:
        da = da.mean(dim="expver", skipna=True)

    lat_name = "latitude" if "latitude" in da.dims else "lat"
    lon_name = "longitude" if "longitude" in da.dims else "lon"
    minx, miny, maxx, maxy = request.bbox
    lat_vals = da[lat_name].values
    lat_desc = lat_vals[0] > lat_vals[-1]
    yslice = slice(maxy, miny) if lat_desc else slice(miny, maxy)
    sub = da.sel({lat_name: yslice, lon_name: slice(minx, maxx)})
    if sub.sizes[lat_name] == 0 or sub.sizes[lon_name] == 0:
        # bbox smaller than the 0.25° grid → no grid point falls inside the exact
        # crop; keep the snapped CDS area (the enclosing grid cell) instead.
        north, west, south, east = area
        yslice = slice(north, south) if lat_desc else slice(south, north)
        sub = da.sel({lat_name: yslice, lon_name: slice(west, east)})
    da = sub

    da = da.rio.set_spatial_dims(x_dim=lon_name, y_dim=lat_name).rio.write_crs("EPSG:4326")
    if request.crs and request.crs != "EPSG:4326":
        da = da.rio.reproject(request.crs)
    return da
