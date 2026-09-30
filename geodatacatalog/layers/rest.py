"""Generic, config-driven REST API provider → GeoDataFrame.

Most bbox-filtered JSON APIs that return geolocated records can be described
entirely in YAML — no plugin needed. The query is built from templated
parameters (``{minx} {miny} {maxx} {maxy} {bbox} {year_start} {year_end}``),
and optional offset-based pagination walks every page.
"""
from __future__ import annotations

import logging

import geopandas as gpd

from ..base import BaseLayer, LayerKind, retry_on_network_error
from ..request import GeoRequest

log = logging.getLogger(__name__)

_SECRET_HINTS = ("key", "token", "secret", "password", "auth", "access")


def _redact(params: dict) -> dict:
    """Mask secret-looking query params before logging them (--debug)."""
    return {k: ("***" if any(h in str(k).lower() for h in _SECRET_HINTS) else v)
            for k, v in params.items()}


class RESTLayer(BaseLayer):
    """JSON REST API queried by bbox; records geolocated from lon/lat fields.

    Caching, executor offloading and error handling are inherited from
    :class:`BaseLayer` — this class only builds the request and parses the JSON.

    Args:
        url: Endpoint URL (the productType is appended as a path segment by the
            loader, unless ``queryParam`` puts it in the query string instead).
        bbox_params: ``{param: template}`` describing how the bbox is sent.
            Templates may reference ``{minx} {miny} {maxx} {maxy} {bbox}``.
            Default ``{"bbox": "{minx},{miny},{maxx},{maxy}"}``.
        data_key: JSON key holding the record list (when the response is an
            object rather than a bare array).
        lon_field, lat_field: record fields carrying the coordinates.
        params: static query parameters added to every request.
        paginate: ``{offset_param, limit_param, page_size, max_records,
            end_field}`` to walk pages, or ``None`` for a single request.
        datetime_param: optional param fed with ``datetime_template`` from
            ``request.datetime`` (e.g. ``year`` ← ``"{year_start},{year_end}"``).
    """

    kind = LayerKind.VECTOR

    def __init__(self, layer_id, url,
                 bbox_params: dict | None = None,
                 data_key="data", lon_field="longitude", lat_field="latitude",
                 geometry_field: str | None = None,
                 params: dict | None = None,
                 paginate: dict | None = None,
                 point_query: bool = False,
                 datetime_param: str | None = None,
                 datetime_template: str = "{year_start},{year_end}",
                 **kwargs):
        super().__init__(layer_id, url, **kwargs)
        self.bbox_params = bbox_params or {"bbox": "{minx},{miny},{maxx},{maxy}"}
        self.data_key = data_key
        self.lon_field = lon_field
        self.lat_field = lat_field
        self.geometry_field = geometry_field  # record sub-field holding a GeoJSON geometry
        # point_query: APIs that sample one location (templated with {cx},{cy}) and
        # return a single object → one feature at the bbox centre (e.g. EPQS elevation).
        self.point_query = point_query
        self.params = params or {}
        self.paginate = paginate
        self.datetime_param = datetime_param
        self.datetime_template = datetime_template

    def _template_context(self, request: GeoRequest) -> dict:
        minx, miny, maxx, maxy = request.bbox
        ctx = {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy,
               "cx": (minx + maxx) / 2, "cy": (miny + maxy) / 2,  # bbox centre (point queries)
               "bbox": f"{minx},{miny},{maxx},{maxy}",
               "year_start": "", "year_end": ""}
        if request.datetime:
            years = [p[:4] for p in request.datetime.split("/") if p[:4].isdigit()]
            if years:
                ctx["year_start"], ctx["year_end"] = years[0], years[-1]
        return ctx

    def _base_params(self, request: GeoRequest) -> dict:
        ctx = self._template_context(request)
        params = {k: str(v).format(**ctx) for k, v in self.bbox_params.items()}
        params.update(self.params)
        if self.datetime_param and ctx["year_start"]:
            params[self.datetime_param] = self.datetime_template.format(**ctx)
        return params

    @retry_on_network_error()
    def _fetch_sync(self, request: GeoRequest) -> gpd.GeoDataFrame:
        import requests
        from shapely.geometry import Point

        params = self._base_params(request)

        if self.point_query:  # one-location sampler → a single feature at the bbox centre
            log.debug("[%s] GET %s params=%s", self.layer_id, self.url, _redact(params))
            resp = requests.get(self.url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
            ctx = self._template_context(request)
            props = {k: v for k, v in payload.items()
                     if not isinstance(v, (dict, list))} if isinstance(payload, dict) else {}
            gdf = gpd.GeoDataFrame([props], geometry=[Point(ctx["cx"], ctx["cy"])], crs="EPSG:4326")
            if request.crs and request.crs != "EPSG:4326":
                gdf = gdf.to_crs(request.crs)
            log.info(f"[{self.layer_id}] REST point query at ({ctx['cx']}, {ctx['cy']})")
            return gdf

        records = self._collect(requests, params)

        geoms, rows = [], []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if rec.get("type") == "Feature":  # GeoJSON feature (Point/Line/Polygon)
                geom = rec.get("geometry")
                if geom:
                    from shapely.geometry import shape
                    geoms.append(shape(geom))
                    rows.append(rec.get("properties") or {})
                continue
            if self.geometry_field:  # record carries a GeoJSON geometry in a sub-field
                geom = rec.get(self.geometry_field)
                if geom:
                    from shapely.geometry import shape
                    geoms.append(shape(geom))
                    rows.append({k: v for k, v in rec.items() if k != self.geometry_field})
                continue
            lon = rec.get(self.lon_field)
            lat = rec.get(self.lat_field)
            if lon is not None and lat is not None:
                try:
                    geoms.append(Point(float(lon), float(lat)))
                except (TypeError, ValueError):
                    # Dirty upstream data (e.g. a malformed "2.1.37277" field) shouldn't
                    # sink the whole fetch — skip just that record.
                    log.warning(f"[{self.layer_id}] skipping record with unparsable "
                               f"coordinates {self.lon_field}={lon!r} {self.lat_field}={lat!r}")
                    continue
                rows.append(rec)

        gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
        if request.crs and request.crs != "EPSG:4326":
            gdf = gdf.to_crs(request.crs)
        log.info(f"[{self.layer_id}] REST: {len(gdf)} features")
        return gdf

    def _collect(self, requests, base_params: dict) -> list:
        """Fetch one page, or walk pages when ``paginate`` is configured."""
        def _get(extra: dict | None = None) -> dict | list:
            merged = {**base_params, **(extra or {})}
            log.debug("[%s] GET %s params=%s", self.layer_id, self.url, _redact(merged))
            resp = requests.get(self.url, params=merged, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

        def _records(payload) -> list:
            if isinstance(payload, dict):
                if payload.get("type") == "FeatureCollection":
                    return payload.get("features", [])      # GeoJSON, regardless of data_key
                if payload.get("type") == "Feature":
                    return [payload]                        # a single GeoJSON feature
            return payload if isinstance(payload, list) else payload.get(self.data_key, [])

        if not self.paginate:
            return _records(_get())

        offset_param = self.paginate.get("offset_param", "offset")
        limit_param = self.paginate.get("limit_param", "limit")
        page_size = int(self.paginate.get("page_size", 300))
        max_records = int(self.paginate.get("max_records", 5000))
        end_field = self.paginate.get("end_field")

        all_records: list = []
        offset = 0
        while offset < max_records:
            payload = _get({offset_param: offset, limit_param: page_size})
            page = _records(payload)
            all_records.extend(page)
            log.info(f"[{self.layer_id}] REST pagination: {len(all_records)} records")
            if isinstance(payload, dict) and end_field and payload.get(end_field):
                break
            if len(page) < page_size:
                break
            offset += page_size
        return all_records[:max_records]
