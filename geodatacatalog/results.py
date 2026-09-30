"""CatalogResults — the fluent container returned by GeoDataCatalog.fetch()."""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
import xarray as xr

from .request import GeoRequest
from .utils import _export_dataset_to_raster
from .viz import _explore_layers

log = logging.getLogger(__name__)


def _load_stac(layers: dict, items: list, request, layer_id: str, bands=None):
    """Load STAC items via odc.stac.load. Returns an xr.Dataset."""
    from .layers.stac import STACLayer
    layer = layers.get(layer_id)
    if layer is None:
        raise KeyError(f"Layer '{layer_id}' absent du catalogue")
    if not isinstance(layer, STACLayer):
        raise TypeError(f"'{layer_id}' n'est pas un STACLayer")
    return layer.load(items, request, bands=bands)


def _filter_by_polygon_id(data, polygon_id, request):
    """Filter data (list[DataArray], GeoDataFrame, list[STAC item]) by polygon_id."""
    ids = {polygon_id} if isinstance(polygon_id, int) else set(polygon_id)
    if isinstance(data, list) and data and isinstance(data[0], xr.DataArray):
        return [da for da in data if da.attrs.get("polygon_id") in ids]
    if isinstance(data, gpd.GeoDataFrame):
        return data[data["polygon_id"].isin(ids)].reset_index(drop=True)
    if isinstance(data, list):
        from shapely.geometry import shape as _shape
        aoi = getattr(request, "aoi_gdf", None) if request is not None else None
        if aoi is None:
            raise ValueError("sel(polygon_id=) on a STAC layer requires request.aoi_gdf")
        aoi_4326 = aoi.to_crs("EPSG:4326")
        id_col = next((c for c in aoi_4326.columns if c.lower() in ("id", "fid", "uid", "identifiant")), None)
        sel_geoms = aoi_4326[aoi_4326[id_col].isin(ids)].geometry if id_col else aoi_4326[aoi_4326.index.isin(ids)].geometry
        if sel_geoms.empty:
            raise KeyError(f"No polygon found for polygon_id={polygon_id!r} in aoi_gdf")
        union = sel_geoms.union_all()
        return [item for item in data if _shape(item.geometry).intersects(union)]
    raise TypeError(f"sel(polygon_id=) not supported for {type(data).__name__}")


class CatalogResults:
    """Result of catalog.fetch() — a dict enriched with explore/load/sel/sort/filter/export."""

    def __init__(self, data, catalog, request):
        self._catalog = catalog
        self._request = request
        self.errors: dict[str, str] = {}  # {layer_id: message} for fetches that raised (set by catalog.fetch)
        if isinstance(data, list) and (not data or isinstance(data[0], CatalogResults)):
            self._poly_list = data  # multi-polygon mode: list[CatalogResults]
            self._data = {}
        else:
            self._poly_list = None  # single-polygon mode: dict[str, data]
            self._data = data

    # --- Dict compat ---
    def __getitem__(self, key):
        if self._poly_list is not None:
            if isinstance(key, int):
                sub = self._poly_list[key]
                catalog = sub._catalog or self._catalog
                pid = getattr(sub._request, "polygon_id", key) if sub._request else key
                # Always derive the sub-request from the parent (never cached)
                # → changes on self._request (resolution, etc.) are reflected immediately
                sub_request = sub._request
                if self._request is not None:
                    aoi = getattr(self._request, "aoi_gdf", None)
                    if aoi is not None:
                        _aoi = aoi.to_crs("EPSG:4326")
                        _ic = next((c for c in _aoi.columns if c.lower() in ("id", "fid", "uid", "identifiant")), None)
                        _rows = _aoi[_aoi[_ic] == pid] if _ic else _aoi[_aoi.index == pid]
                        if _rows.empty:
                            raise KeyError(f"polygon_id={pid} not found in aoi_gdf.")
                        sub_request = _make_sub_request(self._request, _rows.iloc[0], polygon_id=pid)
                    elif not isinstance(sub_request, GeoRequest):
                        raise ValueError("_request.json has no aoi_gdf — cannot rebuild the sub-request.")
                elif not isinstance(sub_request, GeoRequest):
                    raise FileNotFoundError(
                        "No _request.json — cannot rebuild the sub-request."
                    )
                return CatalogResults(sub._data, catalog, sub_request)
            if isinstance(key, slice):
                return CatalogResults(self._poly_list[key], self._catalog, self._request)
            if isinstance(key, str):
                return [cr._data.get(key) for cr in self._poly_list]
            raise TypeError(
                "Multi-polygon mode: index by position first (results[i]), "
                "then results[i]['layer_id']"
            )
        if isinstance(key, (int, slice)) and len(self._data) == 1:
            lid, items = next(iter(self._data.items()))
            if isinstance(items, list):
                sliced = items[key] if isinstance(key, slice) else [items[key]]
                return CatalogResults({lid: sliced}, self._catalog, self._request)
        return self._data[key]
    def __setitem__(self, key, value):
        if self._poly_list is not None:
            raise TypeError("Multi-polygon mode: direct assignment not supported")
        self._data[key] = value

    def _iter_polygons(self):
        """Iterate over _poly_list, resolving sub-requests lazily through __getitem__."""
        for i in range(len(self._poly_list)):
            yield self[i]
    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def __contains__(self, key):
        if self._poly_list is not None:
            return isinstance(key, int) and 0 <= key < len(self._poly_list)
        return key in self._data
    def __iter__(self):
        return iter(self._poly_list if self._poly_list is not None else self._data)
    def __len__(self):
        return len(self._poly_list if self._poly_list is not None else self._data)
    def items(self):                   return self._data.items()
    def keys(self):                    return self._data.keys()
    def values(self):                  return self._data.values()

    # --- API fluente ---
    @property
    def polygon_ids(self) -> list:
        if self._poly_list is not None:
            return [cr._request.polygon_id for cr in self._poly_list]
        if self._request and self._request.polygon_id is not None:
            return [self._request.polygon_id]
        return []

    def explore(self, **kwargs):
        """Render every layer on an interactive Folium map.

        Vector layers are lightened by default so the saved HTML stays
        browser-friendly: at most ``max_columns=12`` attribute columns,
        geometries simplified (``tolerance`` in degrees, auto from the extent)
        and at most ``max_features=50_000`` features per layer. A collapsible
        "Colonnes" panel on the map lists every original column (kept or
        dropped) with an example value. Use ``lighten=False`` to embed
        full-resolution data (can produce huge files). Remaining kwargs go to
        ``GeoDataFrame.explore``.
        """
        if self._poly_list is not None:
            merged: dict = {}
            for cr in self._poly_list:
                for lid, data in cr._data.items():
                    if isinstance(data, gpd.GeoDataFrame):
                        merged.setdefault(lid, []).append(data)
                    elif isinstance(data, list):
                        merged.setdefault(lid, []).extend(data)
                    elif data is not None:
                        merged.setdefault(lid, []).append(data)
            combined = {
                lid: gpd.GeoDataFrame(pd.concat(parts, ignore_index=True))
                     if parts and isinstance(parts[0], gpd.GeoDataFrame)
                     else parts
                for lid, parts in merged.items()
            }
            return _explore_layers(combined, request=self._request, **kwargs)
        return _explore_layers(self._data, request=self._request, **kwargs)

    def load(self, layer_id: str | None = None, bands=None, clip: bool = True) -> "CatalogResults":
        """Load STAC items into xr.Dataset and return a new CatalogResults.

        Without arguments, every layer still holding a list of STAC items is
        loaded. With ``layer_id``, only that layer is loaded.
        ``clip=False`` disables clipping to ``request.aoi_gdf``.
        """
        if self._poly_list is not None:
            return CatalogResults(
                [cr.load(layer_id, bands, clip=clip) for cr in self._iter_polygons()],
                self._catalog, self._request
            )
        aoi = getattr(self._request, "aoi_gdf", None) if self._request else None

        def _load_and_clip(lid, data):
            ds = _load_stac(self._catalog.layers, data, self._request, lid, bands)
            return _clip_to_aoi(ds, aoi) if (clip and aoi is not None) else ds

        new_data = dict(self._data)
        if layer_id is not None:
            new_data[layer_id] = _load_and_clip(layer_id, self._data[layer_id])
        else:
            for lid, data in self._data.items():
                if isinstance(data, list) and data:
                    try:
                        new_data[lid] = _load_and_clip(lid, data)
                    except Exception as e:
                        log.warning(f"[{lid}] load failed: {e}")
        return CatalogResults(new_data, self._catalog, self._request)

    def download(self, layer_id: str | None = None, dest: str = ".",
                bands: list | None = None) -> dict:
        """Save the raw STAC asset file(s) to disk, instead of loading them via `load()`.

        For assets that `.load()` can't (or shouldn't) turn into an xr.Dataset — e.g.
        a zipped/auth-gated format the raster loader can't open (NASA LP DAAC's
        zipped .hgt) — this fetches the original file(s) as-is. Without ``layer_id``,
        every layer still holding STAC items is downloaded, each into its own
        ``dest/{layer_id}`` subfolder. Returns ``{layer_id: [Path, ...]}``.
        """
        from .layers.stac import STACLayer

        if self._poly_list is not None:
            return {
                getattr(cr._request, "polygon_id", i) if cr._request else i:
                    cr.download(layer_id, str(Path(dest) / str(i)), bands)
                for i, cr in enumerate(self._poly_list)
            }

        def _download_layer(lid, data):
            layer = self._catalog.layers.get(lid)
            if layer is None:
                raise KeyError(f"Layer '{lid}' absent du catalogue")
            if not isinstance(layer, STACLayer):
                raise TypeError(f"'{lid}' n'est pas un STACLayer")
            return layer.download(data, self._request, Path(dest) / lid, bands=bands)

        out = {}
        if layer_id is not None:
            out[layer_id] = _download_layer(layer_id, self._data[layer_id])
        else:
            for lid, data in self._data.items():
                if isinstance(data, list) and data:
                    try:
                        out[lid] = _download_layer(lid, data)
                    except Exception as e:
                        log.warning(f"[{lid}] download failed: {e}")
        return out

    def sel(self, layer_id: str = None, item_id=None, *, layer: str = None, polygon_id=None):
        _layer = layer if layer is not None else layer_id
        if self._poly_list is not None:
            if _layer is not None and item_id is None and polygon_id is None:
                # Return a multi-polygon CatalogResults filtered on this single layer
                filtered = [
                    CatalogResults({_layer: cr._data.get(_layer)}, cr._catalog, cr._request)
                    for cr in self._poly_list
                ]
                return CatalogResults(filtered, self._catalog, self._request)
            raise TypeError(
                "Multi-polygon mode: index by position first (results[i]), "
                "then results[i].sel(layer=...)"
            )

        if _layer is None and polygon_id is not None:
            # Filtre tous les layers par polygon_id → CatalogResults
            filtered = {
                lid: _filter_by_polygon_id(data, polygon_id, self._request)
                for lid, data in self._data.items()
            }
            return CatalogResults(filtered, self._catalog, self._request)

        if _layer is None:
            raise ValueError("Provide layer_id= or layer=")
        data = self._data[_layer]
        if item_id is not None and isinstance(data, list):
            if isinstance(item_id, int):
                data = [data[item_id]]
            else:
                data = [i for i in data if i.id == item_id]
                if not data:
                    raise KeyError(f"Item {item_id!r} not found in {_layer!r}")
        if polygon_id is not None:
            data = _filter_by_polygon_id(data, polygon_id, self._request)
        return CatalogResults({_layer: data}, self._catalog, self._request)

    def sort(self, by: str = "cloud_cover") -> "CatalogResults":
        """Sort the STAC items of each layer. by='cloud_cover' (default), 'datetime' or 'coverage'.

        Items missing the requested property go to the end of the list.
        Works in both single-polygon and multi-polygon mode.
        """
        _PROP = {"cloud_cover": "eo:cloud_cover", "datetime": "datetime"}
        prop = _PROP.get(by, by)
        if prop == "datetime":
            import datetime as _dt
            _min = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
            key_fn = lambda i: i.datetime or _min
        elif by == "coverage":
            from shapely.geometry import box as _box, shape as _shape
            bbox = getattr(self._request, "bbox", None)
            aoi_shape = _box(*bbox) if bbox else None
            def key_fn(i):
                if aoi_shape is None:
                    return (True, 0)
                try:
                    cov = _shape(i.geometry).intersection(aoi_shape).area / aoi_shape.area
                    return (False, -cov)  # descending: best coverage first
                except Exception:
                    return (True, 0)
        else:
            key_fn = lambda i: (
                i.properties.get(prop) is None,
                i.properties.get(prop) if i.properties.get(prop) is not None else 0,
            )

        def _sort_dict(d: dict) -> dict:
            return {
                lid: sorted(v, key=key_fn)
                     if isinstance(v, list) and v and not isinstance(v[0], xr.DataArray)
                     else v
                for lid, v in d.items()
            }

        if self._poly_list is not None:
            def _sort_dict_for(cr):
                if by == "coverage":
                    from shapely.geometry import box as _box, shape as _shape
                    sub_bbox = getattr(cr._request, "bbox", None)
                    sub_aoi = _box(*sub_bbox) if sub_bbox else None
                    def _cov_key(i):
                        if sub_aoi is None:
                            return (True, 0)
                        try:
                            return (False, -_shape(i.geometry).intersection(sub_aoi).area / sub_aoi.area)
                        except Exception:
                            return (True, 0)
                    return {
                        lid: sorted(v, key=_cov_key)
                             if isinstance(v, list) and v and not isinstance(v[0], xr.DataArray)
                             else v
                        for lid, v in cr._data.items()
                    }
                return _sort_dict(cr._data)
            return CatalogResults(
                [CatalogResults(_sort_dict_for(cr), cr._catalog, cr._request)
                 for cr in self._iter_polygons()],
                self._catalog, self._request,
            )
        return CatalogResults(_sort_dict(self._data), self._catalog, self._request)

    def filter(self, func=None, **kwargs) -> "CatalogResults":
        """Filter the STAC items of each layer.

        func     : arbitrary predicate ``lambda item: bool``
        coverage : minimum AOI coverage in % (e.g. ``coverage=100``)
        kwargs   : equality on known properties (``cloud_cover=0``)
                   or raw STAC properties.

        Examples ::

            results.filter(cloud_cover=0, coverage=100)
            results.filter(lambda i: i.properties.get('eo:cloud_cover', 999) < 5)
        """
        _PROP = {"cloud_cover": "eo:cloud_cover", "datetime": "datetime"}
        coverage_min = kwargs.pop("coverage", None)

        def _make_pred(request):
            aoi_shape = None
            if coverage_min is not None:
                from shapely.geometry import box as _box
                _bbox = getattr(request, "bbox", None)
                aoi_shape = _box(*_bbox) if _bbox else None

            def _pred(item):
                if func is not None and not func(item):
                    return False
                for k, v in kwargs.items():
                    prop = _PROP.get(k, k)
                    if item.properties.get(prop) != v:
                        return False
                if coverage_min is not None:
                    if aoi_shape is None:
                        return False
                    try:
                        from shapely.geometry import shape as _shape
                        cov = _shape(item.geometry).intersection(aoi_shape).area / aoi_shape.area * 100
                        if cov < coverage_min:
                            return False
                    except Exception:
                        return False
                return True
            return _pred

        def _filter_dict(d: dict, request) -> dict:
            pred = _make_pred(request)
            return {
                lid: [i for i in items if pred(i)]
                     if isinstance(items, list) and items and not isinstance(items[0], xr.DataArray)
                     else items
                for lid, items in d.items()
            }

        if self._poly_list is not None:
            return CatalogResults(
                [CatalogResults(_filter_dict(cr._data, cr._request), cr._catalog, cr._request)
                 for cr in self._iter_polygons()],
                self._catalog, self._request,
            )
        return CatalogResults(_filter_dict(self._data, self._request), self._catalog, self._request)

    def first(self) -> "CatalogResults":
        """Keep the first STAC item of each layer (use after sort/filter).

        Example ::

            results.sel('layer').sort(by='cloud_cover').filter(coverage=100).first()
        """
        def _first_dict(d: dict) -> dict:
            return {
                lid: [items[0]] if isinstance(items, list) and items
                                    and not isinstance(items[0], xr.DataArray)
                     else items
                for lid, items in d.items()
            }

        if self._poly_list is not None:
            return CatalogResults(
                [CatalogResults(_first_dict(cr._data), cr._catalog, cr._request)
                 for cr in self._poly_list],
                self._catalog, self._request,
            )
        return CatalogResults(_first_dict(self._data), self._catalog, self._request)

    def assign(self, other: "CatalogResults") -> "CatalogResults":
        """Replace the layers present in `other` into `self`, polygon by polygon.

        Layers absent from `other` are kept as-is.

        Example ::

            results.assign(
                results.sel('layer').sort(by='cloud_cover').filter(coverage=100).first()
            )
        """
        if self._poly_list is not None and other._poly_list is not None:
            return CatalogResults(
                [CatalogResults({**cr._data, **cr_o._data}, cr._catalog, cr._request)
                 for cr, cr_o in zip(self._poly_list, other._poly_list)],
                self._catalog, self._request,
            )
        if self._poly_list is None and other._poly_list is None:
            return CatalogResults({**self._data, **other._data}, self._catalog, self._request)
        raise TypeError("Cannot merge a single-polygon CatalogResults with a multi-polygon one.")

    def export(self, output_dir, formats=None, layers=None):
        import json as _json
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        if self._poly_list is not None:
            if self._request is not None:
                req = self._request
                req_dict = {
                    "bbox":         list(req.bbox) if getattr(req, "bbox", None) else None,
                    "crs":          getattr(req, "crs", None),
                    "resolution":   getattr(req, "resolution", None),
                    "datetime":     getattr(req, "datetime", None),
                    "max_features": getattr(req, "max_features", None),
                    "bands":        getattr(req, "bands", None),
                    "filters":      getattr(req, "filters", {}),
                    "sort_by":      getattr(req, "sort_by", None),
                    "cloud_cover":  getattr(req, "cloud_cover", None),
                    "polygon_id":   getattr(req, "polygon_id", None),
                    "zoom":         getattr(req, "zoom", None),
                    "aoi_gdf":      req.aoi_gdf.to_crs("EPSG:4326").to_json(default=str) if getattr(req, "aoi_gdf", None) is not None else None,
                }
                (output_dir / "_request.json").write_text(
                    _json.dumps(req_dict, ensure_ascii=False), encoding="utf-8"
                )
            fmt_map = formats or {}
            for cr in self._poly_list:
                pid = cr._request.polygon_id if cr._request else self._poly_list.index(cr)
                for layer_id, data in cr._data.items():
                    if layers is not None and layer_id not in layers:
                        continue
                    if data is None:
                        continue
                    fmt = fmt_map.get(layer_id)
                    stem = f"{layer_id}_{pid}"
                    if isinstance(data, gpd.GeoDataFrame):
                        suffix = fmt or "geojson"
                        path = output_dir / f"{stem}.{suffix}"
                        drivers = {"gpkg": "GPKG", "geojson": "GeoJSON", "shp": "ESRI Shapefile"}
                        data.to_file(path, driver=drivers.get(suffix, "GPKG"))
                    elif isinstance(data, xr.DataArray):
                        path = output_dir / f"{stem}.{fmt or 'tif'}"
                        data.rio.to_raster(path)
                    elif isinstance(data, xr.Dataset):
                        suffix = fmt or "tif"
                        path = output_dir / f"{stem}.{suffix}"
                        if suffix == "tif":
                            _export_dataset_to_raster(data, path)
                        else:
                            data.to_netcdf(path)
                    elif isinstance(data, list) and data:
                        import pystac as _pystac
                        if isinstance(data[0], _pystac.Item):
                            path = output_dir / f"{stem}.json"
                            _pystac.ItemCollection(items=data).save_object(dest_href=str(path))
                    log.info(f"[{layer_id}] polygon {pid} → {path}")
            return
        if self._request is not None:
            req = self._request
            req_dict = {
                "bbox":         list(req.bbox) if req.bbox else None,
                "crs":          req.crs,
                "resolution":   req.resolution,
                "datetime":     req.datetime,
                "max_features": req.max_features,
                "bands":        req.bands,
                "filters":      req.filters,
                "sort_by":      req.sort_by,
                "cloud_cover":  req.cloud_cover,
                "polygon_id":   req.polygon_id,
                "zoom":         req.zoom,
                "aoi_gdf":      req.aoi_gdf.to_crs("EPSG:4326").to_json(default=str) if req.aoi_gdf is not None else None,
            }
            (output_dir / "_request.json").write_text(
                _json.dumps(req_dict, ensure_ascii=False), encoding="utf-8"
            )
        fmt_map = formats or {}
        items = ((k, v) for k, v in self._data.items() if layers is None or k in layers)

        for layer_id, data in items:
            if data is None:
                continue
            fmt = fmt_map.get(layer_id)

            if isinstance(data, gpd.GeoDataFrame):
                suffix = fmt or "geojson"
                path = output_dir / f"{layer_id}.{suffix}"
                drivers = {"gpkg": "GPKG", "geojson": "GeoJSON", "shp": "ESRI Shapefile"}
                data.to_file(path, driver=drivers.get(suffix, "GPKG"))
                log.info(f"[{layer_id}] → {path}")

            elif isinstance(data, list):
                if not data:
                    continue
                import pystac as _pystac
                if isinstance(data[0], xr.DataArray):
                    for i, da in enumerate(data):
                        pid = da.attrs.get("polygon_id", i)
                        path = output_dir / f"{layer_id}_{pid}.{fmt or 'tif'}"
                        da.rio.to_raster(path)
                        log.info(f"[{layer_id}] → {path}")
                elif isinstance(data[0], _pystac.Item):
                    path = output_dir / f"{layer_id}.json"
                    _pystac.ItemCollection(items=data).save_object(dest_href=str(path))
                    log.info(f"[{layer_id}] → {path}")
                else:
                    log.warning(f"[{layer_id}] list type cannot be exported: {type(data[0]).__name__}")

            elif isinstance(data, xr.DataArray):
                path = output_dir / f"{layer_id}.{fmt or 'tif'}"
                data.rio.to_raster(path)
                log.info(f"[{layer_id}] → {path}")

            elif isinstance(data, xr.Dataset):
                suffix = fmt or "tif"
                path = output_dir / f"{layer_id}.{suffix}"
                if suffix == "tif":
                    _export_dataset_to_raster(data, path)
                else:
                    data.to_netcdf(path)
                log.info(f"[{layer_id}] → {path}")

    @classmethod
    def from_files(cls, path: str) -> "CatalogResults":
        """Rebuild a CatalogResults from previously exported files.

        Args:
            path: Directory (e.g. "output/") or glob pattern (e.g. "output/*fwi*.tif")
        """
        import glob as _glob
        import io
        import json as _json
        import re
        import types

        p = Path(path)

        def _load_request(base_dir):
            req_file = base_dir / "_request.json"
            if not req_file.exists():
                return None
            d = _json.loads(req_file.read_text(encoding="utf-8"))
            _aoi_str = d.pop("aoi_gdf", None)
            d["aoi_gdf"] = gpd.read_file(io.StringIO(_aoi_str)) if _aoi_str else None
            if d.get("bbox"):
                d["bbox"] = tuple(d["bbox"])
            return GeoRequest(**d)

        if p.is_dir():
            base_dir = p
            request = _load_request(base_dir)
            files = [f for f in p.glob("*") if f.is_file() and f.name != "_request.json"]
        else:
            all_files = [Path(f) for f in _glob.glob(str(path), recursive=True)]
            base_dir = all_files[0].parent if all_files else None
            request = _load_request(base_dir) if base_dir else None
            files = [f for f in all_files if f.name != "_request.json"]

        if not files:
            raise FileNotFoundError(f"No file found: {path!r}")

        _POLY_RE = re.compile(r"^(.+)_(\d+)$")

        stem_meta: dict[str, tuple[str, int | None]] = {}
        for f in files:
            m = _POLY_RE.match(f.stem)
            stem_meta[f.stem] = (m.group(1), int(m.group(2))) if m else (f.stem, None)

        from collections import Counter
        prefix_counts = Counter(
            prefix for prefix, idx in stem_meta.values() if idx is not None
        )
        poly_prefixes = {pfx for pfx, c in prefix_counts.items() if c >= 2}

        groups: dict[str, list[Path]] = {}
        for f in sorted(files):
            prefix, idx = stem_meta[f.stem]
            key = prefix if (idx is not None and prefix in poly_prefixes) else f.stem
            groups.setdefault(key, []).append(f)

        VECTOR_EXTS = {".geojson", ".gpkg", ".shp"}
        data: dict = {}
        for layer_id, flist in groups.items():
            if len(flist) > 1:
                import rioxarray  # noqa: F401
                arrays = []
                for f in sorted(flist):
                    da = xr.open_dataarray(f, engine="rasterio", mask_and_scale=True)
                    m = _POLY_RE.match(f.stem)
                    if m:
                        da.attrs["polygon_id"] = int(m.group(2))
                    arrays.append(da)
                data[layer_id] = arrays
            else:
                f = flist[0]
                ext = f.suffix.lower()
                if ext in {".tif", ".tiff"}:
                    import rioxarray  # noqa: F401
                    data[layer_id] = xr.open_dataarray(f, engine="rasterio", mask_and_scale=True)
                elif ext == ".nc":
                    data[layer_id] = xr.open_dataset(f)
                elif ext in VECTOR_EXTS:
                    data[layer_id] = gpd.read_file(f)
                elif ext == ".json":
                    import pystac
                    data[layer_id] = list(pystac.ItemCollection.from_file(str(f)).items)
                else:
                    log.warning(f"[from_files] unrecognised extension, skipped: {f.name}")

        # Flat layer_id_N files — rebuild the multi-polygon structure
        pids: list = []
        for d in data.values():
            if isinstance(d, list) and d and isinstance(d[0], xr.DataArray):
                pids.extend(a.attrs["polygon_id"] for a in d if "polygon_id" in a.attrs)
            elif isinstance(d, gpd.GeoDataFrame) and "polygon_id" in d.columns:
                pids.extend(d["polygon_id"].unique().tolist())
        unique_pids = sorted(set(pids))

        if unique_pids:
            import types as _types
            poly_results = []
            for pid in unique_pids:
                poly_data: dict = {}
                for lid, d in data.items():
                    if isinstance(d, list) and d and isinstance(d[0], xr.DataArray):
                        matching = [a for a in d if a.attrs.get("polygon_id") == pid]
                        poly_data[lid] = matching[0] if matching else None
                    elif isinstance(d, gpd.GeoDataFrame) and "polygon_id" in d.columns:
                        poly_data[lid] = d[d["polygon_id"] == pid].drop(columns="polygon_id").reset_index(drop=True)
                    elif isinstance(d, list) and d:
                        import pystac as _pystac
                        if isinstance(d[0], _pystac.Item):
                            poly_data[lid] = [
                                item for item in d
                                if item.properties.get("_polygon_id") == pid
                                or pid in item.properties.get("_polygon_ids", [])
                            ]
                        else:
                            poly_data[lid] = d
                    else:
                        poly_data[lid] = d
                poly_results.append(cls(poly_data, catalog=None, request=_types.SimpleNamespace(polygon_id=pid)))
            return cls(poly_results, catalog=None, request=request)

        return cls(data, catalog=None, request=request)

    def compute_stats(
        self,
        stats: list[str] | None = None,
        layers: list[str] | None = None,
    ) -> "gpd.GeoDataFrame":
        """Compute descriptive statistics per polygon and per layer.

        One row per polygon, one column per ``{layer_id}_{stat}``.
        DataArrays/Datasets → min/q1/median/q3/max/mean/std (NaN ignored).
        GeoDataFrames → count (number of features).
        Lists of STAC items → count (plus cloud_cover_mean when available).
        The ``geometry`` column comes from ``request.aoi_gdf`` when available.

        Args:
            stats: statistics among ``["min","q1","median","q3","max","mean","std"]``.
                   Default: all seven.
            layers: subset of layers to include. Default: all.

        Returns:
            gpd.GeoDataFrame indexed by polygon_id (no geometry if the AOI is unknown).
        """
        import numpy as _np

        if stats is None:
            stats = ["min", "q1", "median", "q3", "max", "mean", "std"]

        def _arr_stats(arr: xr.DataArray, wanted: list[str]) -> dict:
            vals = arr.values.ravel().astype(float)
            nodata = arr.rio.nodata if hasattr(arr, "rio") else None
            mask = ~_np.isnan(vals)
            if nodata is not None:
                mask &= vals != float(nodata)
            v = vals[mask]
            if v.size == 0:
                return {s: float("nan") for s in wanted}
            out = {}
            for s in wanted:
                if s == "min":      out[s] = float(v.min())
                elif s == "max":    out[s] = float(v.max())
                elif s == "mean":   out[s] = float(v.mean())
                elif s == "median": out[s] = float(_np.percentile(v, 50))
                elif s == "q1":     out[s] = float(_np.percentile(v, 25))
                elif s == "q3":     out[s] = float(_np.percentile(v, 75))
                elif s == "std":    out[s] = float(v.std())
            return out

        def _layer_stats(data_, layer_id: str, wanted: list[str]) -> dict:
            cols = {}
            if data_ is None:
                for s in wanted:
                    cols[f"{layer_id}_{s}"] = float("nan")
            elif isinstance(data_, xr.DataArray):
                for s, v in _arr_stats(data_, wanted).items():
                    cols[f"{layer_id}_{s}"] = v
            elif isinstance(data_, xr.Dataset):
                for s, v in _arr_stats(data_.to_array(dim="band"), wanted).items():
                    cols[f"{layer_id}_{s}"] = v
            elif isinstance(data_, gpd.GeoDataFrame):
                cols[f"{layer_id}_count"] = len(data_)
            elif isinstance(data_, list) and data_:
                try:
                    import pystac as _pystac
                    if isinstance(data_[0], _pystac.Item):
                        cols[f"{layer_id}_count"] = len(data_)
                        cc_vals = [
                            float(it.properties["eo:cloud_cover"])
                            for it in data_
                            if "eo:cloud_cover" in it.properties
                        ]
                        if cc_vals:
                            cols[f"{layer_id}_cloud_cover_mean"] = float(_np.mean(cc_vals))
                    else:
                        arr = data_[0] if isinstance(data_[0], xr.DataArray) else data_[0].to_array(dim="band")
                        for s, v in _arr_stats(arr, wanted).items():
                            cols[f"{layer_id}_{s}"] = v
                except Exception:
                    cols[f"{layer_id}_count"] = len(data_)
            return cols

        # pid → geometry lookup from the parent aoi_gdf (robust even after from_files)
        geom_lookup: dict = {}
        outer_aoi = getattr(self._request, "aoi_gdf", None) if self._request else None
        if outer_aoi is not None and not outer_aoi.empty:
            outer_4326 = outer_aoi.to_crs("EPSG:4326")
            id_col = next((c for c in outer_4326.columns if c.lower() in ("id", "fid", "uid", "identifiant")), None)
            for idx, row in outer_4326.iterrows():
                pid_key = row[id_col] if id_col else idx
                geom_lookup[pid_key] = row.geometry

        def _get_geom(cr, pid):
            # Priority 1: lookup from the parent aoi_gdf
            if pid in geom_lookup:
                return geom_lookup[pid]
            # Priority 2: aoi_gdf attached to the sub-request
            aoi = getattr(cr._request, "aoi_gdf", None) if cr._request else None
            if aoi is not None and not aoi.empty:
                return aoi.to_crs("EPSG:4326").geometry.iloc[0]
            return None

        target_layers = set(layers) if layers is not None else None

        if self._poly_list is not None:
            rows = []
            for cr in self._poly_list:
                pid = getattr(cr._request, "polygon_id", None) if cr._request else None
                row = {"polygon_id": pid, "geometry": _get_geom(cr, pid)}
                for lid, data_ in cr._data.items():
                    if target_layers is not None and lid not in target_layers:
                        continue
                    row.update(_layer_stats(data_, lid, stats))
                rows.append(row)
            df = pd.DataFrame(rows).set_index("polygon_id")
        else:
            pid = getattr(self._request, "polygon_id", 0) if self._request else 0
            geom = geom_lookup.get(pid)
            if geom is None and outer_aoi is not None and not outer_aoi.empty:
                geom = outer_aoi.to_crs("EPSG:4326").geometry.iloc[0]
            row = {"geometry": geom}
            for lid, data_ in self._data.items():
                if target_layers is not None and lid not in target_layers:
                    continue
                row.update(_layer_stats(data_, lid, stats))
            df = pd.DataFrame([row], index=pd.Index([pid], name="polygon_id"))

        has_geom = df["geometry"].notna().any()
        return gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326") if has_geom else gpd.GeoDataFrame(df.drop(columns=["geometry"]))

    def __repr__(self):
        import math

        def _fmt_bytes(n: int) -> str:
            units = ["B", "KB", "MB", "GB"]
            i = min(int(math.log(max(n, 1), 1024)), len(units) - 1)
            return f"{n / 1024**i:.1f} {units[i]}"

        if self._poly_list is not None:
            pids = ", ".join(str(p) for p in self.polygon_ids)
            layers = list(self._poly_list[0]._data.keys()) if self._poly_list else []
            n_layers = len(layers)
            layers_str = ", ".join(layers) if layers else "—"
            return (
                f"CatalogResults ({len(self._poly_list)} polygons, {n_layers} layers):\n"
                f"  layers   : {layers_str}\n"
                f"  polygons : [{pids}]"
            )

        lines = [f"CatalogResults ({len(self._data)} layers):"]
        for lid, d in self._data.items():
            if d is None:
                s = "None"
            elif isinstance(d, list):
                if d and isinstance(d[0], xr.DataArray):
                    try:
                        size = _fmt_bytes(sum(a.nbytes for a in d))
                        s = f"list[DataArray] ({len(d)} polygons, {size})"
                    except Exception:
                        s = f"list[DataArray] ({len(d)} polygons)"
                else:
                    try:
                        import pystac as _pystac
                        if d and isinstance(d[0], _pystac.Item):
                            from shapely.geometry import box as _box
                            aoi_shape = None
                            try:
                                aoi_shape = _box(*self._request.bbox)
                            except Exception:
                                pass
                            item_lines = [f"list[Item] ({len(d)}) :"]
                            for item in d:
                                parts = [f"    {item.id}"]
                                if item.datetime:
                                    parts.append(str(item.datetime.date()))
                                cc = item.properties.get("eo:cloud_cover")
                                if cc is not None:
                                    parts.append(f"cloud={round(float(cc), 1)}%")
                                if aoi_shape is not None:
                                    try:
                                        from shapely.geometry import shape as _shape
                                        cov = _shape(item.geometry).intersection(aoi_shape).area / aoi_shape.area * 100
                                        parts.append(f"coverage={round(cov, 1)}%")
                                    except Exception:
                                        pass
                                item_lines.append("  ".join(parts))
                            s = "\n".join(item_lines)
                        else:
                            s = f"list ({len(d)})"
                    except Exception:
                        s = f"list[Item] ({len(d)})"
            elif isinstance(d, gpd.GeoDataFrame):
                try:
                    size = _fmt_bytes(int(d.memory_usage(deep=True).sum()))
                    s = f"GeoDataFrame ({len(d)} rows, {size})"
                except Exception:
                    s = f"GeoDataFrame ({len(d)} rows)"
            elif isinstance(d, xr.DataArray):
                try:
                    s = f"DataArray {dict(d.sizes)} ({_fmt_bytes(d.nbytes)})"
                except Exception:
                    s = f"DataArray {dict(d.sizes)}"
            elif isinstance(d, xr.Dataset):
                try:
                    nbytes = sum(d[v].nbytes for v in d.data_vars)
                    s = f"Dataset {dict(d.sizes)} ({_fmt_bytes(nbytes)})"
                except Exception:
                    s = f"Dataset {dict(d.sizes)}"
            else:
                s = type(d).__name__
            lines.append(f"  {lid}: {s}")
        return "\n".join(lines)


def _make_sub_request(request: GeoRequest, row, row_datetime=None, polygon_id=None) -> GeoRequest:
    """Build the sub-request for a single polygon."""
    sub_gdf = gpd.GeoDataFrame(geometry=[row.geometry], crs="EPSG:4326")
    return GeoRequest(
        aoi_gdf=sub_gdf,
        crs=request.crs,
        resolution=request.resolution,
        datetime=row_datetime if row_datetime is not None else request.datetime,
        max_features=request.max_features,
        bands=request.bands,
        filters=request.filters,
        sort_by=request.sort_by,
        cloud_cover=request.cloud_cover,
        polygon_id=polygon_id,
        zoom=request.zoom,
    )



def _clip_to_aoi(data, aoi_gdf: gpd.GeoDataFrame):
    """Clip the result to the AOI GeoDataFrame (vector or raster)."""
    try:
        if isinstance(data, gpd.GeoDataFrame):
            return gpd.clip(data, aoi_gdf.to_crs(data.crs))
        elif isinstance(data, xr.DataArray):
            crs = data.rio.crs
            if crs is None:
                return data
            return data.rio.clip(aoi_gdf.to_crs(crs).geometry.values, crs)
        elif isinstance(data, xr.Dataset):
            crs = data.rio.crs
            if crs is None:
                return data
            return data.rio.clip(aoi_gdf.to_crs(crs).geometry.values, crs)
    except Exception as e:
        log.debug(f"Clip AOI impossible ({e}), données non clippées")
    return data
