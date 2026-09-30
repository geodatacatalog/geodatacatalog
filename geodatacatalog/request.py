"""Unified request object shared by every provider."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
from shapely.geometry import box

def _resolution_from_bbox(bbox: tuple, crs: str = "EPSG:4326") -> float:
    """Natural resolution derived from the bbox via XYZ/WMTS zoom levels.

    Always returns metres/pixel regardless of the CRS; layers convert to CRS
    units on the fly when needed.
    """
    import math
    lon_min, lat_min, lon_max, lat_max = bbox
    dlon = max(lon_max - lon_min, 1e-9)
    lat_c = (lat_min + lat_max) / 2
    z = math.log2(720.0 / dlon)
    z = max(0.0, min(z, 24.0))
    return 156543.034 * math.cos(math.radians(lat_c)) / (2.0 ** z)


@dataclass
class GeoRequest:
    """Parameters shared by every fetch, whatever the provider.

    Provide either ``bbox`` or ``aoi_gdf`` (or both). If only ``aoi_gdf`` is
    given, ``bbox`` is derived from its ``total_bounds``.

    Attributes:
        bbox: Extent in EPSG:4326 (minx, miny, maxx, maxy). Derived from
            ``aoi_gdf`` when omitted.
        aoi_gdf: GeoDataFrame used to clip results after fetch. With several
            rows, ``fetch`` launches one sub-request per polygon in parallel.
            A ``"datetime"`` column (ISO string, e.g. ``"2024-06-01/2024-08-31"``)
            overrides ``request.datetime`` per polygon. Columns named ``"id"``,
            ``"fid"``, ``"uid"`` or ``"identifiant"`` populate ``polygon_id``
            in the results.
        crs: Target CRS of the result (default ``"EPSG:3857"``).
        resolution: Resolution in metres/pixel, regardless of the CRS.
        datetime: ISO time window, e.g. ``"2024-01-01/2024-12-31"``.
            If ``None`` and ``aoi_gdf`` has a single row with a ``"datetime"``
            column, that value is used. In multi-polygon mode each sub-request
            uses its own row value, falling back to this field.
            Default (when neither source is set): the last 7 days.
        max_features: Maximum number of features (vector layers).
        bands: STAC spectral bands to download (e.g. ``["red", "nir"]``).
        filters: Free-form extra filters forwarded to the provider.
        sort_by: STAC item ordering (``"cloud_cover"``, ``"date"`` for most
            recent first, or ``"date_asc"`` for oldest first).
        cloud_cover: Maximum cloud cover (%) — overrides the layer setting.
        polygon_id: Identifier of the source polygon; set automatically in
            multi-polygon mode.
        zoom: Forced XYZ zoom level (replaces the automatic computation).
    """
    bbox: tuple[float, float, float, float] | None = None
    aoi_gdf: gpd.GeoDataFrame | None = None
    crs: str | None = "EPSG:3857"
    resolution: float | None = None
    datetime: str | None = None
    max_features: int | None = None
    bands: list[str] | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    sort_by: Literal["cloud_cover", "date", "date_asc"] | None = None
    cloud_cover: int | None = None
    polygon_id: Any = None
    zoom: int | None = None

    def __post_init__(self):
        if self.bbox is None and self.aoi_gdf is not None:
            self.bbox = tuple(self.aoi_gdf.to_crs("EPSG:4326").total_bounds.tolist())
        elif self.bbox is None:
            raise ValueError("Provide either bbox or aoi_gdf")
        if self.resolution is None:
            self.resolution = _resolution_from_bbox(self.bbox, self.crs)
        if self.datetime is None:
            if self.aoi_gdf is not None and "datetime" in self.aoi_gdf.columns:
                if len(self.aoi_gdf) == 1:
                    self.datetime = str(self.aoi_gdf.iloc[0]["datetime"])
                else:
                    _s = self.aoi_gdf["datetime"].dropna()
                    _starts = _s.str.split("/").str[0]
                    _ends = _s.str.split("/").str[-1]
                    self.datetime = f"{_starts.min()}/{_ends.max()} (aoi_gdf['datetime'])"
            else:
                from datetime import timedelta
                today = date.today()
                self.datetime = f"{(today - timedelta(days=7)).isoformat()}/{today.isoformat()}"

    def save(self, path):
        """Serialise the request to JSON (aoi_gdf embedded as GeoJSON)."""
        import json
        from pathlib import Path as _Path
        d = {
            "bbox":         list(self.bbox) if self.bbox else None,
            "crs":          self.crs,
            "resolution":   self.resolution,
            "datetime":     self.datetime,
            "max_features": self.max_features,
            "bands":        self.bands,
            "filters":      self.filters,
            "sort_by":      self.sort_by,
            "cloud_cover":  self.cloud_cover,
            "polygon_id":   self.polygon_id,
            "zoom":         self.zoom,
            "aoi_gdf":      self.aoi_gdf.to_crs("EPSG:4326").to_json(default=str) if self.aoi_gdf is not None else None,
        }
        _Path(path).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    @property
    def shapely_box(self):
        return box(*self.bbox)

    def explore(self, **kwargs):
        """Show the AOI and bbox on an interactive map (geopandas .explore())."""
        import geopandas as gpd

        meta = {
            "bbox":       str(self.bbox),
            "crs":        self.crs,
            "resolution": self.resolution,
            "datetime":   self.datetime,
            "bands":      str(self.bands) if self.bands else None,
            "sort_by":    self.sort_by,
        }
        meta = {k: v for k, v in meta.items() if v is not None}

        bbox_gdf = gpd.GeoDataFrame([meta], geometry=[box(*self.bbox)], crs="EPSG:4326")

        m = None
        styles = [
            {"color": "#3388ff", "fillOpacity": 0.15},
            {"color": "#ff4444", "fillOpacity": 0.0, "weight": 2, "dashArray": "6 4"},
        ]

        if self.aoi_gdf is not None:
            m = self.aoi_gdf.to_crs("EPSG:4326").explore(
                m=m, name="AOI", style_kwds=styles[0], **kwargs
            )
        m = bbox_gdf.explore(m=m, name="bbox", style_kwds=styles[1], **kwargs)

        try:
            import folium
            folium.LayerControl().add_to(m)
        except ImportError:
            pass

        return m

    def cache_key(self, layer_id: str) -> str:
        """Deterministic SHA-256 key of the request parameters for one layer."""
        payload = json.dumps({
            "layer": layer_id,
            "bbox": self.bbox,
            "crs": self.crs,
            "resolution": self.resolution,
            "datetime": self.datetime,
            "bands": self.bands,
            "cloud_cover": self.cloud_cover,
            "filters": self.filters,
            "zoom": self.zoom,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
