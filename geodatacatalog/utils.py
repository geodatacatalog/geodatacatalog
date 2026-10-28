"""Shared helpers: raster export, GeoJSON serialisation, legend handling."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import xarray as xr

log = logging.getLogger(__name__)


def _export_dataset_to_raster(ds: xr.Dataset, path: Path):
    if "time" in ds.dims:
        from odc.algo import choose_first_valid
        ds = ds.map(lambda da: choose_first_valid(da) if "time" in da.dims else da).squeeze()
    data_vars = list(ds.data_vars)
    if not data_vars:
        log.warning("Empty dataset, nothing to export")
        return
    da = xr.concat([ds[v] for v in data_vars], dim="band")
    da.rio.to_raster(path)


def _gdf_to_geojson_features(gdf: gpd.GeoDataFrame) -> list:
    """Serialise a GeoDataFrame into GeoJSON features.
    Handles out-of-bounds Timestamps (e.g. dates < 1677) stored as dtype object."""
    import pandas as pd

    gdf = gdf.copy()
    geom_col = gdf.geometry.name

    for col in gdf.select_dtypes(
        include=["datetime64[ns]", "datetime64[ns, UTC]", "datetimetz"]
    ).columns:
        gdf[col] = gdf[col].astype(str)

    for col in gdf.select_dtypes(include=["object"]).columns:
        if col == geom_col:
            continue
        if gdf[col].dropna().map(lambda v: isinstance(v, pd.Timestamp)).any():
            gdf[col] = gdf[col].astype(str)

    return json.loads(gdf.to_json(default=str)).get("features", [])


def _rgba_to_classes(canvas: np.ndarray, x_coords, y_coords,
                     legend: dict[str, str]) -> xr.DataArray:
    """Convert an RGBA canvas into a categorical band following the {hex: label} legend."""
    color_to_class, class_meta = {}, {}
    for i, (hex_c, label) in enumerate(legend.items(), start=1):
        r, g, b = int(hex_c[1:3], 16), int(hex_c[3:5], 16), int(hex_c[5:7], 16)
        color_to_class[(r, g, b)] = i
        class_meta[i] = {"color": hex_c, "label": label}

    h, w = canvas.shape[1], canvas.shape[2]
    class_band = np.zeros((h, w), dtype=np.uint8)
    for (r, g, b), cls in color_to_class.items():
        class_band[(canvas[0] == r) & (canvas[1] == g) & (canvas[2] == b)] = cls

    return xr.DataArray(
        class_band[np.newaxis], dims=["band", "y", "x"],
        coords={"band": ["class"], "y": y_coords, "x": x_coords},
        attrs={"legend": class_meta},
    )


def _apply_legend(da: xr.DataArray, legend: dict[str, str]) -> xr.DataArray:
    """Apply a legend to an already-loaded RGBA DataArray (GeoTIFF)."""
    canvas = da.values
    if canvas.ndim == 3:
        x_coords = da.x.values if "x" in da.coords else np.arange(canvas.shape[2])
        y_coords = da.y.values if "y" in da.coords else np.arange(canvas.shape[1])
        return _rgba_to_classes(canvas, x_coords, y_coords, legend)
    return da


def _normalise_legend(legend: dict) -> dict:
    """Normalise a legend config into ``{int_value: {"color": ..., "label": ...}}``.

    Accepts the two shapes found in ``catalog.yml``:

    - ``{hex_color: label}`` — the WMS/WMTS/XYZ style (Corine, BD Forêt): the
      class value is the *position* of the entry (1-based), matching
      :func:`_rgba_to_classes`, and the color is the key.
    - ``{value: label}`` or ``{value: {label: ..., color: ...}}`` — the explicit
      style for already-categorical rasters (e.g. a STAC LULC collection that
      ships integer class codes): the key *is* the pixel value.

    The returned mapping is attached to ``da.attrs["legend"]`` so callers can
    resolve a category (e.g. "Water") without hardcoding its code.
    """
    out: dict[int, dict] = {}
    for i, (key, val) in enumerate(legend.items(), start=1):
        if isinstance(val, dict):  # {value: {label:..., color:...}}
            label = val.get("label")
            color = val.get("color")
        else:
            label, color = val, None
        # A hex string key → positional class value (WMS RGBA style);
        # otherwise the key is the pixel value itself (explicit style).
        if isinstance(key, str) and key.startswith("#") and len(key) == 7:
            value = i
            color = color or key
        else:
            try:
                value = int(key)
            except (TypeError, ValueError):
                value = i
        entry: dict = {"label": label}
        if color:
            entry["color"] = color
        out[value] = entry
    return out
