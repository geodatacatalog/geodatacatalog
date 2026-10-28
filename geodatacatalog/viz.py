"""Interactive Folium visualisation of catalog results."""
from __future__ import annotations

import logging

import geopandas as gpd
import xarray as xr

log = logging.getLogger(__name__)

#: Default raster basemap tiles for interactive maps. We deliberately avoid
#: ``tile.openstreetmap.org``: OSM's volunteer-run tile servers block
#: application/automated use (see https://osm.wiki/Blocked), which shows up as
#: an "Access blocked / not following the tile usage policy" error in saved
#: HTML. CARTO's free basemaps are key-less, permissive and OSM-derived.
BASEMAP_TILES = (
    "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
)
BASEMAP_ATTR = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
    'contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
)


def _new_folium_map(location=None, **kwargs):
    """A folium.Map with a tile-usage-policy-friendly basemap (see ``BASEMAP_TILES``).

    Extra keyword arguments are forwarded to ``folium.Map``. Any ``tiles`` /
    ``attr`` passed by the caller wins, so users can still pick their own
    basemap.
    """
    import folium

    kwargs.setdefault("tiles", BASEMAP_TILES)
    kwargs.setdefault("attr", BASEMAP_ATTR)
    return folium.Map(location=location, **kwargs)


def _init_folium_map(data: dict, request=None):
    """Create a folium.Map fitted to the request bbox, or to the first dataset."""
    b = None

    if request is not None and getattr(request, "aoi_gdf", None) is not None:
        try:
            tb = request.aoi_gdf.to_crs("EPSG:4326").total_bounds  # [minx, miny, maxx, maxy]
            b = tb
        except Exception:
            pass

    if b is None:
        for data_ in data.values():
            if data_ is None:
                continue
            try:
                if isinstance(data_, gpd.GeoDataFrame) and not data_.empty:
                    b = data_.to_crs("EPSG:4326").total_bounds
                elif isinstance(data_, list) and data_ and isinstance(data_[0], (xr.DataArray, xr.Dataset)):
                    import pyproj
                    all_bounds = []
                    for _item in data_:
                        if _item is None:
                            continue
                        _arr = _item if isinstance(_item, xr.DataArray) else _item.to_array(dim="band")
                        _crs = _arr.rio.crs
                        if _crs is None:
                            continue
                        _bs = _arr.rio.bounds()
                        _t = pyproj.Transformer.from_crs(_crs, "EPSG:4326", always_xy=True)
                        _xs, _ys = _t.transform([_bs[0], _bs[2]], [_bs[1], _bs[3]])
                        all_bounds.append([min(_xs), min(_ys), max(_xs), max(_ys)])
                    if not all_bounds:
                        continue
                    b = [min(bb[0] for bb in all_bounds), min(bb[1] for bb in all_bounds),
                         max(bb[2] for bb in all_bounds), max(bb[3] for bb in all_bounds)]
                elif isinstance(data_, list) and data_:
                    from shapely.geometry import shape as _s
                    b = _s(data_[0].geometry).bounds
                elif isinstance(data_, (xr.DataArray, xr.Dataset)):
                    arr = data_ if isinstance(data_, xr.DataArray) else data_.to_array(dim="band")
                    crs = arr.rio.crs
                    if crs is None:
                        continue
                    b_src = arr.rio.bounds()
                    import pyproj
                    t = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
                    cx, cy = t.transform((b_src[0] + b_src[2]) / 2, (b_src[1] + b_src[3]) / 2)
                    b = [cx - 0.5, cy - 0.5, cx + 0.5, cy + 0.5]
                else:
                    continue
                break
            except Exception:
                continue

    if b is None:
        return None

    center = [(b[1] + b[3]) / 2, (b[0] + b[2]) / 2]
    m = _new_folium_map(location=center, control_scale=True)
    m.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
    return m


def _lighten_gdf(gdf: gpd.GeoDataFrame, *, max_features: int = 50_000,
                 max_columns: int = 12, tolerance: float | None = None):
    """Reduce a GeoDataFrame (already in EPSG:4326) for inline Folium embedding.

    Folium serialises the whole frame as GeoJSON inside the HTML, so wide WFS
    extents easily produce maps of hundreds of MB. Three levers multiply:
    keep at most ``max_columns`` attribute columns, simplify geometries with
    ``tolerance`` (degrees; default derived from the layer extent) and snap
    coordinates to a ~1 m grid so they serialise with few digits. Layers above
    ``max_features`` are randomly sampled (``head()`` would bias spatially —
    WFS pagination is often spatially ordered).

    Returns ``(light_gdf, columns_info)`` where ``columns_info`` describes
    every original non-geometry column: ``{"name", "kept", "example"}`` with
    one example value, so dropped columns remain discoverable.
    """
    import shapely

    out = gdf.copy()
    geom_col = out.geometry.name

    attr_cols = [c for c in out.columns if c != geom_col]
    examples = {}
    for col in attr_cols:
        non_empty = out[col].dropna().astype(str).str.strip()
        non_empty = non_empty[non_empty != ""]
        example = "" if non_empty.empty else non_empty.iloc[0]
        if len(example) > 80:
            example = example[:77] + "..."
        examples[col] = example
    # informative columns first: a column empty over the whole extent should
    # not crowd out a populated one
    kept_cols = sorted(attr_cols, key=lambda c: examples[c] == "")[:max_columns]
    kept_cols = [c for c in attr_cols if c in kept_cols]  # original order
    columns_info = [{"name": c, "kept": c in kept_cols, "example": examples[c]}
                    for c in attr_cols]
    out = out[kept_cols + [geom_col]]

    if len(out) > max_features:
        log.warning(
            f"displaying {max_features} of {len(out)} features "
            f"(random sample) — use lighten=False for everything"
        )
        out = out.sample(max_features, random_state=0)

    if not out.empty:
        if tolerance is None:
            minx, miny, maxx, maxy = out.total_bounds
            tolerance = max((maxx - minx, maxy - miny, 0.0)) / 5000
        tolerance = max(tolerance, 1e-5)
        geom = out.geometry.simplify(tolerance)
        geom = gpd.GeoSeries(shapely.set_precision(geom.values, grid_size=1e-5),
                             index=out.index, crs=out.crs)
        out = out.set_geometry(geom)
        out = out[~(out.geometry.is_empty | out.geometry.isna())]

    return out, columns_info


def _columns_panel_html(columns_by_layer: dict) -> str:
    """Collapsible HTML panel listing every original column (kept ✓ / dropped ✗)."""
    import html as _html

    sections = []
    for layer_id, infos in columns_by_layer.items():
        rows = "".join(
            f"<tr><td>{'✓' if c['kept'] else '✗'}</td>"
            f"<td>{_html.escape(c['name'])}</td>"
            f"<td>{_html.escape(c['example'])}</td></tr>"
            for c in infos
        )
        sections.append(
            f"<details><summary>{_html.escape(layer_id)} "
            f"({sum(c['kept'] for c in infos)}/{len(infos)} colonnes gardées)</summary>"
            f"<table><tr><th></th><th>colonne</th><th>exemple</th></tr>{rows}</table>"
            f"</details>"
        )
    return (
        '<div style="position: fixed; bottom: 12px; left: 12px; z-index: 9999;'
        ' background: rgba(255,255,255,0.95); padding: 6px 10px; border-radius: 4px;'
        ' box-shadow: 0 1px 4px rgba(0,0,0,0.4); font: 11px/1.5 sans-serif;'
        ' max-height: 45vh; max-width: 38vw; overflow: auto;">'
        "<details><summary><b>Colonnes</b> (allègement actif)</summary>"
        + "".join(sections) + "</details></div>"
    )


def _explore_layers(data: dict, request=None, lighten: bool = True,
                    max_features: int = 50_000, max_columns: int = 12,
                    tolerance: float | None = None, **kwargs):
    """Render a dict of results on an interactive Folium map.

    - Vector (GeoDataFrame): geometries, lightened by default (``lighten=True``):
      at most ``max_columns`` attribute columns, geometries simplified with
      ``tolerance`` (degrees, auto-derived from the extent when None) and at
      most ``max_features`` features per layer. A collapsible "Colonnes" panel
      on the map lists every original column (kept or dropped) with an example
      value. Pass ``lighten=False`` to embed everything (can be huge).
    - Raster (DataArray / Dataset): two FeatureGroups (footprint+stats / preview).
    - STAC items: footprints + thumbnails.
    - request.aoi_gdf: request extent when provided.

    Each layer goes into its own FeatureGroup: one checkbox in the
    LayerControl toggles all of its sub-layers at once.
    """
    import folium
    from shapely.geometry import box as shapely_box

    columns_by_layer: dict = {}

    m = _init_folium_map(data, request=request)
    if m is None:
        log.warning("No result to display")
        return None

    if request is not None and getattr(request, "aoi_gdf", None) is not None:
        try:
            fg_aoi = folium.FeatureGroup(name="AOI", show=True)
            request.aoi_gdf.to_crs("EPSG:4326").explore(
                m=fg_aoi,
                style_kwds={"fillOpacity": 0.05, "weight": 2, "dashArray": "5 5", "color": "#e74c3c"},
            )
            fg_aoi.add_to(m)
        except Exception as e:
            log.debug(f"[AOI] explore failed: {e}")

    for layer_id, data_ in data.items():
        if data_ is None:
            continue
        try:
            if isinstance(data_, gpd.GeoDataFrame):
                if data_.empty:
                    continue
                gdf4326 = data_.to_crs("EPSG:4326")
                if lighten:
                    gdf4326, columns_info = _lighten_gdf(
                        gdf4326, max_features=max_features,
                        max_columns=max_columns, tolerance=tolerance,
                    )
                    columns_by_layer[layer_id] = columns_info
                    log.info(
                        f"[{layer_id}] columns:\n" + "\n".join(
                            f"  {'✓' if c['kept'] else '✗'} {c['name']} — ex: {c['example']}"
                            for c in columns_info
                        )
                    )
                    if gdf4326.empty:
                        continue
                fg = folium.FeatureGroup(name=layer_id, show=True)
                gdf4326.explore(m=fg, name=layer_id, **kwargs)
                fg.add_to(m)

            elif isinstance(data_, list) and (not data_ or not isinstance(data_[0], xr.DataArray)):
                from shapely.geometry import shape as _shape

                if not data_:
                    continue
                fg_footprint = folium.FeatureGroup(name=f"{layer_id} — footprint", show=False)
                fg_preview  = folium.FeatureGroup(name=f"{layer_id} — preview",  show=True)
                rows = []
                for item in data_:
                    props: dict = {"item_id": item.id}
                    if item.datetime:
                        props["datetime"] = str(item.datetime.date())
                    cc = item.properties.get("eo:cloud_cover")
                    if cc is not None:
                        props["cloud_cover_%"] = round(float(cc), 1)
                    rows.append({"geometry": _shape(item.geometry), **props})
                gpd.GeoDataFrame(rows, crs="EPSG:4326").explore(m=fg_footprint, name=layer_id, **kwargs)
                for item in data_:
                    thumb_href = None
                    for _a in ("thumbnail", "rendered_preview"):
                        _asset = item.assets.get(_a)
                        if _asset:
                            thumb_href = _asset.href
                            break
                    if not thumb_href:
                        _tlinks = item.get_links("thumbnail")
                        if _tlinks:
                            thumb_href = _tlinks[0].absolute_href
                    if not thumb_href:
                        continue
                    try:
                        import warnings
                        import numpy as _np
                        import rasterio as _rio
                        from rasterio.errors import NotGeoreferencedWarning
                        bx = _shape(item.geometry).bounds
                        with _rio.Env(AWS_NO_SIGN_REQUEST="YES",
                                      GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
                            with warnings.catch_warnings():
                                warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
                                with _rio.open(thumb_href) as _src:
                                    _d = _src.read()
                        if _d.shape[0] == 1:
                            _d = _np.repeat(_d, 3, axis=0)
                        if _d.shape[0] >= 4:
                            _img = _d[:4].transpose(1, 2, 0).astype(_np.uint8)
                        else:
                            _rgb = _d[:3].transpose(1, 2, 0)
                            _alpha = _np.full((*_rgb.shape[:2], 1), 255, dtype=_np.uint8)
                            _img = _np.concatenate([_rgb, _alpha], axis=2).astype(_np.uint8)
                        folium.raster_layers.ImageOverlay(
                            image=_img,
                            bounds=[[bx[1], bx[0]], [bx[3], bx[2]]],
                            name=f"{layer_id} · {item.id}",
                            opacity=0.8,
                            show=True,
                        ).add_to(fg_preview)
                    except Exception as _e:
                        log.debug(f"[{layer_id}] thumbnail {item.id}: {_e}")
                fg_footprint.add_to(m)
                fg_preview.add_to(m)

            else:
                import numpy as np

                fg_footprint = folium.FeatureGroup(name=f"{layer_id} — footprint", show=False)
                fg_preview  = folium.FeatureGroup(name=f"{layer_id} — preview",  show=True)
                arrays = data_ if isinstance(data_, list) else [data_]
                for idx, item_data in enumerate(arrays):
                    suffix = f"_{idx}" if len(arrays) > 1 else ""
                    if isinstance(item_data, xr.Dataset):
                        arr = item_data.to_array(dim="band")
                        _nd = next((v.rio.nodata for v in item_data.data_vars.values() if v.rio.nodata is not None), None)
                        if _nd is not None and arr.rio.nodata is None:
                            arr = arr.rio.write_nodata(_nd)
                    else:
                        arr = item_data
                    x_dim = arr.rio.x_dim
                    y_dim = arr.rio.y_dim
                    step_x = max(1, arr.sizes[x_dim] // 256)
                    step_y = max(1, arr.sizes[y_dim] // 256)
                    preview = arr.isel(indexers={x_dim: slice(None, None, step_x),
                                                y_dim: slice(None, None, step_y)})
                    if "time" in preview.dims:
                        preview = preview.isel(time=0)
                    preview4326 = preview.rio.reproject("EPSG:4326")
                    bounds = preview4326.rio.bounds()

                    attrs: dict = {"layer": layer_id}
                    if suffix:
                        attrs["index"] = idx
                    try:
                        if "time" in arr.coords:
                            import pandas as pd
                            t = arr.coords["time"].values
                            if t.ndim == 0:
                                attrs["date"] = str(pd.Timestamp(t.item()).date())
                            else:
                                dates = [str(pd.Timestamp(v).date()) for v in t]
                                attrs["date"] = dates[0] if len(dates) == 1 else f"{dates[0]} … {dates[-1]} ({len(dates)})"
                    except Exception:
                        pass
                    try:
                        attrs["min"]    = float(preview4326.min().item())
                        attrs["max"]    = float(preview4326.max().item())
                        attrs["mean"]   = float(preview4326.mean().item())
                        attrs["median"] = float(preview4326.median().item())
                    except Exception:
                        pass

                    gpd.GeoDataFrame(
                        [attrs], geometry=[shapely_box(*bounds)], crs="EPSG:4326",
                    ).explore(m=fg_footprint, name=f"{layer_id}{suffix}", **kwargs)

                    try:
                        vals = preview4326.values
                        if vals.ndim == 2:
                            vals = vals[np.newaxis]
                        if vals.shape[0] == 4:
                            pixel_nodata = vals[3] == 0
                            work = vals[:3]
                        else:
                            nodata = preview4326.rio.nodata
                            pixel_nodata = np.isnan(vals).any(axis=0)
                            if nodata is not None:
                                pixel_nodata = pixel_nodata | (vals == nodata).any(axis=0)
                            work = vals
                        valid = ~pixel_nodata.ravel()
                        flat = work.reshape(work.shape[0], -1)[:, valid].ravel()
                        unique_vals = np.unique(flat)
                        legend = preview4326.attrs.get("legend")
                        if legend and work.shape[0] == 1:
                            band = work[0]
                            H, W = band.shape
                            rgb = np.zeros((H, W, 3), dtype=np.uint8)
                            in_legend = np.zeros((H, W), dtype=bool)
                            for val, entry in legend.items():
                                hex_color = entry["color"].lstrip("#")
                                r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
                                mask = band == int(val)
                                rgb[mask] = [r, g, b]
                                in_legend |= mask
                            pixel_nodata = ~in_legend
                        elif len(unique_vals) < 300:
                            rgb_bands = vals[:3] if work.shape[0] >= 3 else np.repeat(vals[:1], 3, axis=0)
                            rgb = rgb_bands.transpose(1, 2, 0).astype(np.uint8)
                        else:
                            med = np.median(flat)
                            q1 = np.median(flat[flat < med])
                            q3 = np.median(flat[flat > med])
                            iqr = q3 - q1
                            vmin, vmax = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                            norm = np.clip((work - vmin) / (vmax - vmin + 1e-9), 0, 1)
                            norm_rgb = norm[:3] if work.shape[0] >= 3 else np.repeat(norm[:1], 3, axis=0)
                            rgb = (norm_rgb.transpose(1, 2, 0) * 255).astype(np.uint8)
                        alpha = np.where(pixel_nodata, 0, 255).astype(np.uint8)
                        folium.raster_layers.ImageOverlay(
                            image=np.dstack([rgb, alpha]),
                            bounds=[[bounds[1], bounds[0]], [bounds[3], bounds[2]]],
                            name=f"{layer_id}{suffix} (preview)",
                            opacity=1.0,
                        ).add_to(fg_preview)
                    except Exception as e:
                        log.debug(f"[{layer_id}] preview image failed: {e}")

                fg_footprint.add_to(m)
                fg_preview.add_to(m)

        except Exception as e:
            log.warning(f"[{layer_id}] explore failed: {e}")

    if columns_by_layer:
        m.get_root().html.add_child(folium.Element(_columns_panel_html(columns_by_layer)))
    folium.LayerControl().add_to(m)
    return m
