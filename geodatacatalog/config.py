"""Build a GeoDataCatalog from a YAML provider configuration.

The format is inspired by `EODAG <https://github.com/CS-SI/eodag>`_ provider
configuration files: one top-level key per provider, a ``search`` section
describing the protocol, and a ``products`` section listing the layers.
See ``catalog.yml`` at the root of the repository for a fully commented example.

Credentials are never written in the file: any ``${VAR_NAME}`` string is
resolved from the environment at load time (``${VAR_NAME:-default}`` is also
supported). Providers with ``need_auth: true`` and unresolved credentials are
skipped with a warning instead of failing.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from .base import BaseLayer
from .catalog import GeoDataCatalog
from .layers import (
    OSMNXLayer,
    PythonPluginLayer,
    RESTLayer,
    STACLayer,
    WCSLayer,
    WFSLayer,
    WMSLayer,
    WMTSLayer,
    XYZLayer,
)

log = logging.getLogger(__name__)

_ENV_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")

#: search.type values → layer family
_SEARCH_TYPES = {"StacSearch", "WFS", "WMS", "WCS", "WMTS", "OSMNX", "REST", "XYZ"}


def _resolve_env(value):
    """Recursively substitute ``${VAR}`` / ``${VAR:-default}`` from os.environ.

    An unset variable without default resolves to ``None`` so that
    ``need_auth`` checks treat it as a missing credential.
    """
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    if isinstance(value, str):
        m = _ENV_RE.fullmatch(value.strip())
        if m:
            return os.environ.get(m.group("name"), m.group("default"))
        return _ENV_RE.sub(
            lambda m: os.environ.get(m.group("name"), m.group("default") or ""), value
        )
    return value


def _clean_endpoint(url: str) -> str:
    """Strip OGC boilerplate query params (SERVICE=, REQUEST=, ...) from an endpoint."""
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(url)
    if not parsed.query:
        return url
    ogc = {"SERVICE", "REQUEST", "VERSION", "TYPENAME", "TYPENAMES", "OUTPUTFORMAT"}
    kept = {k: v for k, v in parse_qs(parsed.query).items() if k.upper() not in ogc}
    return urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in kept.items()})))


def _band_mapping_to_dict(bm) -> dict[str, str]:
    """Accept both ``{asset: name}`` dicts and eodag-style lists of single-key dicts."""
    if not bm:
        return {}
    if isinstance(bm, dict):
        return bm
    return {k: v for d in bm for k, v in d.items()}


def _auth_headers(creds: dict) -> dict[str, str]:
    """HTTP auth headers from credentials: api_key, token or username/password."""
    apikey = creds.get("api_key") or creds.get("apikey")
    if apikey:
        return {"x-api-key": str(apikey)}
    if creds.get("token"):
        return {"Authorization": f"Bearer {creds['token']}"}
    if creds.get("username") and creds.get("password"):
        import base64
        b64 = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        return {"Authorization": f"Basic {b64}"}
    return {}


def _build_layer(search_type: str, layer_id: str, api_endpoint: str,
                 product_name: str, product_cfg: dict, search_cfg: dict,
                 auth_headers: dict, aws_config: dict | None,
                 no_sign_request: bool) -> BaseLayer:
    product_type = product_cfg.get("productType", product_name)

    if search_type == "StacSearch":
        return STACLayer(
            layer_id=layer_id,
            url=api_endpoint.rstrip("/").removesuffix("/search"),
            collection=product_type,
            band_mapping=_band_mapping_to_dict(product_cfg.get("bandMapping")),
            legend=dict(product_cfg.get("legend") or {}),
            static=bool(product_cfg.get("static", False)),
            cloud_cover=product_cfg.get("cloudCover"),
            cloud_cover_aoi=product_cfg.get("cloudCoverAoi"),
            image_coverage_threshold=product_cfg.get("imageCoverageThreshold", 0),
            headers=auth_headers or None,
            aws_config=aws_config,
            no_sign_request=no_sign_request,
        )
    if search_type == "WFS":
        wfs_kwargs = {}
        if product_cfg.get("timeout"):  # some WFS (e.g. EFFIS) are slow on GetCapabilities
            wfs_kwargs["timeout"] = product_cfg["timeout"]
        return WFSLayer(layer_id=layer_id, url=api_endpoint, typename=product_type, **wfs_kwargs)
    if search_type == "WMS":
        return WMSLayer(
            layer_id=layer_id,
            url=api_endpoint,
            layer_name=product_type,
            img_format=product_cfg.get("format", "image/png"),
            zoom=product_cfg.get("zoom"),
            legend=dict(product_cfg.get("legend") or {}),
            version=product_cfg.get("version"),
        )
    if search_type == "WCS":
        return WCSLayer(layer_id=layer_id, url=api_endpoint,
                        coverage_id=product_cfg.get("productType") or None)
    if search_type == "WMTS":
        return WMTSLayer(
            layer_id=layer_id,
            url=api_endpoint,
            layer_name=product_type,
            tile_matrix_set=product_cfg.get("tileMatrixSet", "PM"),
            fmt=product_cfg.get("format", "image/png"),
            zoom=product_cfg.get("zoom"),
            legend=dict(product_cfg.get("legend") or {}),
        )
    if search_type == "XYZ":
        def cfg(key, default=None):
            return product_cfg.get(key, search_cfg.get(key, default))

        return XYZLayer(
            layer_id=layer_id,
            url=api_endpoint,
            url_template=cfg("urlTemplate"),
            manifest=cfg("manifest"),
            frames=cfg("frames"),
            frame_time=cfg("frameTime", "time"),
            product_type=product_type,
            zoom=product_cfg.get("zoom"),
            max_zoom=cfg("maxZoom"),
            tile_size=cfg("tileSize", 256),
            subdomains=cfg("subdomains"),
            legend=dict(product_cfg.get("legend") or {}),
        )
    if search_type == "OSMNX":
        return OSMNXLayer(
            layer_id=layer_id,
            url=api_endpoint or "https://nominatim.openstreetmap.org/search",
            network_type=product_type,
            tags=product_cfg.get("tags") or None,
        )
    if search_type == "REST":
        def cfg(key, default=None):
            return product_cfg.get(key, search_cfg.get(key, default))

        # bbox: either a single templated `bboxParams` dict, or the legacy
        # `bbox_key`/`bboxParam` single-parameter form.
        bbox_params = cfg("bboxParams")
        if not bbox_params:
            bbox_key_cfg = search_cfg.get("bbox_key") or {}
            if isinstance(bbox_key_cfg, dict) and bbox_key_cfg:
                key = bbox_key_cfg.get("key", "bbox")
                value = bbox_key_cfg.get("value", "{minx},{miny},{maxx},{maxy}")
            else:
                key, value = cfg("bboxParam", "bbox"), "{minx},{miny},{maxx},{maxy}"
            bbox_params = {key: value}

        # productType either extends the path (default) or fills a query param.
        query_param = cfg("queryParam")
        if query_param:
            url = api_endpoint
            params = {query_param: product_type}
        else:
            url = f"{api_endpoint}/{product_type}"
            params = {}
        params.update(cfg("params") or {"size": 200, "format": "json"})

        return RESTLayer(
            layer_id=layer_id,
            url=url,
            bbox_params=bbox_params,
            data_key=cfg("dataKey", "data"),
            lon_field=cfg("lonField", "longitude"),
            lat_field=cfg("latField", "latitude"),
            geometry_field=cfg("geometryField"),
            point_query=cfg("pointQuery", False),
            params=params,
            paginate=cfg("paginate"),
            datetime_param=cfg("datetimeParam"),
            datetime_template=cfg("datetimeTemplate", "{year_start},{year_end}"),
        )
    raise ValueError(f"Unsupported search.type: {search_type!r}")


def load_catalog(
    path: str | Path,
    cache_dir: str = "./geo_cache",
    cache_ttl: int = 86400 * 7,
    max_workers: int = 4,
    plugin_search_dirs: list[str | Path] | None = None,
) -> GeoDataCatalog:
    """Load a :class:`GeoDataCatalog` from a YAML provider configuration.

    Args:
        path: Path to the YAML file (see ``catalog.yml`` for the format).
        cache_dir: Directory of the disk cache (SQLite + GeoTIFF files).
        cache_ttl: Cache time-to-live in seconds (default: 7 days).
        max_workers: Maximum concurrent HTTP connections.
        plugin_search_dirs: Extra directories to look up ``<name>.py`` plugin
            modules. The directory of the YAML file is always searched.

    Returns:
        A ready-to-use GeoDataCatalog. Layer ids are ``{provider}__{product}``.
    """
    import yaml

    path = Path(path)
    # search: the catalog's own dir, then user dirs, then geodatacatalog's shipped plugins
    _builtin_plugins = Path(__file__).parent / "layers" / "plugins"
    plugin_dirs = [path.parent] + [Path(d) for d in (plugin_search_dirs or [])] + [_builtin_plugins]

    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    cfg = _resolve_env(cfg)

    layers: list[BaseLayer] = []

    for provider_name, provider_cfg in cfg.items():
        if not isinstance(provider_cfg, dict):
            continue

        search_cfg = provider_cfg.get("search") or {}
        raw_type = search_cfg.get("type", "")
        is_plugin = raw_type.endswith(".py")
        if not is_plugin and raw_type not in _SEARCH_TYPES:
            log.warning(f"[{provider_name}] unsupported search.type={raw_type!r} — skipped")
            continue

        auth_cfg = provider_cfg.get("auth") or {}
        creds = auth_cfg.get("credentials") or {}

        if search_cfg.get("need_auth", False) and not any(v for v in creds.values()):
            log.warning(
                f"[{provider_name}] need_auth=true but no credentials provided — skipped "
                "(set the environment variables referenced in the config to enable it)"
            )
            continue

        api_endpoint = _clean_endpoint(search_cfg.get("api_endpoint", "") or "")
        auth_headers = _auth_headers(creds)
        no_sign_request = bool(auth_cfg.get("no-sign-request", False))

        aws_config = None
        if auth_cfg.get("type") == "AwsAuth":
            aws_config = {
                "aws_access_key_id": creds.get("aws_access_key_id"),
                "aws_secret_access_key": creds.get("aws_secret_access_key"),
                "s3_endpoint": auth_cfg.get("s3_endpoint"),
                "region_name": creds.get("region_name", "default"),
                "https": auth_cfg.get("https", True),
                "virtual_hosting": auth_cfg.get("virtual_hosting", False),
                "gdal_unsafe_ssl": auth_cfg.get("gdal_unsafe_ssl", False),
            }
        elif auth_cfg.get("region"):
            # Public, unsigned bucket pinned to a specific AWS region (e.g. DEA
            # Africa COGs in af-south-1): carry the region through so GDAL targets
            # the right S3 endpoint instead of the default us-east-1.
            aws_config = {"region_name": auth_cfg["region"]}

        module_path = None
        if is_plugin:
            module_path = next(
                (str(d / raw_type) for d in plugin_dirs if (d / raw_type).exists()), None
            )
            if module_path is None:
                log.warning(f"[{provider_name}] plugin module '{raw_type}' not found in "
                            f"{[str(d) for d in plugin_dirs]} — skipped")
                continue

        for product_name, product_cfg in (provider_cfg.get("products") or {}).items():
            product_cfg = product_cfg or {}
            layer_id = f"{provider_name}__{product_name}"
            try:
                if is_plugin:
                    layer = PythonPluginLayer(
                        layer_id=layer_id,
                        url=api_endpoint,
                        module_path=module_path,
                        product_type=product_cfg.get("productType", product_name),
                        provider_config=dict(provider_cfg),
                    )
                else:
                    layer = _build_layer(
                        raw_type, layer_id, api_endpoint, product_name, product_cfg,
                        search_cfg, auth_headers, aws_config, no_sign_request,
                    )
                layers.append(layer)
            except Exception as exc:
                log.warning(f"[{provider_name}] could not create layer {layer_id}: {exc}")

    log.info(f"load_catalog: {len(layers)} layers loaded from {path}")
    return GeoDataCatalog(
        layers=layers,
        cache_dir=cache_dir,
        cache_ttl=cache_ttl,
        max_workers=max_workers,
    )
