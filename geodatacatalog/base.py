"""Core types: LayerKind, LayerResult and the abstract BaseLayer."""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

import diskcache
import geopandas as gpd
import rioxarray  # noqa: F401 — registers the .rio accessor on xarray
import xarray as xr

from .request import GeoRequest
from .utils import _export_dataset_to_raster, _gdf_to_geojson_features

log = logging.getLogger(__name__)


def retry_on_network_error(retries: int = 4, delay: float = 2.0, extra_exceptions: tuple = ()):
    """Exponential-backoff retry decorator for synchronous network calls.

    extra_exceptions: additional exception types to retry (e.g. pystac_client APIError).
    """
    import functools
    import requests

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retriable = (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                *extra_exceptions,
            )
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except retriable as e:
                    if attempt < retries - 1:
                        wait = delay * (2 ** attempt)
                        log.warning(f"{type(e).__name__} (attempt {attempt+1}/{retries}), retry in {wait}s")
                        time.sleep(wait)
                    else:
                        raise RuntimeError(f"Failed after {retries} attempts: {e}") from e
                except Exception:
                    raise
        return wrapper
    return decorator


class LayerKind(str, Enum):
    VECTOR = "vector"       # → GeoDataFrame
    RASTER = "raster"       # → xarray.DataArray ou Dataset
    STAC_ITEMS = "stac_items"  # → list[pystac.Item]


@dataclass
class LayerResult:
    layer_id: str
    kind: LayerKind
    data: gpd.GeoDataFrame | xr.DataArray | xr.Dataset | list
    crs: str
    bbox: tuple[float, float, float, float]
    source_url: str
    cached: bool = False
    cloud_coverage: float | None = None   # % nuages dans l'AOI (STAC SCL)
    image_coverage: float | None = None   # % de l'AOI couvert par l'image

    def to_file(self, path: str | Path):
        path = Path(path)
        if self.kind == LayerKind.STAC_ITEMS:
            raise NotImplementedError("STAC items must be loaded via odc.stac.load before export")
        if self.kind == LayerKind.VECTOR:
            assert isinstance(self.data, gpd.GeoDataFrame)
            suffix = path.suffix or ".gpkg"
            self.data.to_file(path.with_suffix(suffix), driver="GPKG")
        elif isinstance(self.data, xr.DataArray):
            self.data.rio.to_raster(path.with_suffix(".tif"))
        else:
            assert isinstance(self.data, xr.Dataset)
            _export_dataset_to_raster(self.data, path.with_suffix(".tif"))
        log.info(f"[{self.layer_id}] → {path}")


class BaseLayer(ABC):
    #: Set to False on layers whose results must not be disk-cached
    #: (local files, dynamic plugins, ...).
    cacheable: bool = True

    def __init__(self, layer_id: str, url: str, **kwargs):
        self.layer_id = layer_id
        self.url = url
        self.timeout: int = int(kwargs.get('timeout', 60))
        self.kwargs = kwargs

    @property
    @abstractmethod
    def kind(self) -> LayerKind: ...

    async def fetch_async(
        self,
        request: GeoRequest,
        cache: diskcache.Cache,
        session,
        parallel: bool = True,
    ) -> LayerResult:
        """Template method: cache check → _fetch_sync (inline or executor) → cache save.

        Caching is handled entirely here: subclasses only implement
        ``_fetch_sync(request)`` and never touch the cache. A stale cache entry
        (missing GeoTIFF, corrupted payload) is evicted and the data is fetched
        again instead of raising.
        """
        key = self._cache_key(request)
        if self.cacheable and key in cache:
            try:
                result = self._cache_load(key, cache, request)
                log.info(f"[{self.layer_id}] Cache hit ({type(self).__name__.replace('Layer', '')})")
                return result
            except Exception as e:
                log.warning(f"[{self.layer_id}] Stale cache entry ({e}) — refetching")
                try:
                    del cache[key]
                except KeyError:
                    pass

        domain = urlparse(self.url).netloc or self.url
        kind_label = type(self).__name__.replace("Layer", "")
        log.info(f"[{self.layer_id}] \U0001f310 requesting {domain} ({kind_label}) …")
        t0 = time.monotonic()
        if parallel:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._fetch_sync, request)
        else:
            data = self._fetch_sync(request)
        log.info(f"[{self.layer_id}] ✓ {domain} responded in {time.monotonic() - t0:.1f}s")

        if self.cacheable and not getattr(cache, '_is_noop', False):
            self._cache_save(data, key, cache)
        return LayerResult(self.layer_id, self._infer_kind(data), data,
                           request.crs, request.bbox, self.url)

    def _infer_kind(self, data) -> LayerKind:
        """Kind of the fetched data — static by default, overridable for
        layers whose output type is only known at runtime (plugins)."""
        return self.kind

    def _fetch_sync(self, request: GeoRequest):
        raise NotImplementedError(
            f"{type(self).__name__} must implement _fetch_sync or override fetch_async"
        )

    def _cache_load(self, key: str, cache, request: GeoRequest) -> LayerResult:
        """Rebuild data from a self-describing cache entry.

        Entries carry their own kind, so layers whose output type is only
        known at runtime (plugins) are cached like any other layer. Any
        malformed entry raises and is evicted by ``fetch_async``.
        """
        entry = cache[key]
        kind = LayerKind(entry["kind"])

        if kind == LayerKind.RASTER:
            # The entry stores the GeoTIFF file name; resolve it against the
            # cache directory so entries survive cwd changes and dir moves.
            data = xr.open_dataarray(Path(cache.directory) / entry["file"])
        elif kind == LayerKind.STAC_ITEMS:
            import pystac
            data = [pystac.Item.from_dict(d) for d in entry["items"]]
        else:
            features = entry["features"]
            if not features:
                data = gpd.GeoDataFrame(geometry=gpd.GeoSeries([], dtype="geometry"), crs="EPSG:4326")
            else:
                data = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
            if request.crs != "EPSG:4326":
                data = data.to_crs(request.crs)
        return LayerResult(self.layer_id, kind, data, request.crs, request.bbox, self.url, cached=True)

    def _cache_save(self, data, key: str, cache) -> None:
        ttl = getattr(cache, "ttl", None)
        kind = self._infer_kind(data)

        if kind == LayerKind.RASTER:
            cache_path = Path(cache.directory) / f"{key}.tif"
            if isinstance(data, xr.Dataset):
                _export_dataset_to_raster(data, cache_path)
            else:
                data.rio.to_raster(cache_path, driver="GTiff")
            entry = {"kind": kind.value, "file": cache_path.name}
        elif kind == LayerKind.STAC_ITEMS:
            entry = {"kind": kind.value, "items": [item.to_dict() for item in data]}
        else:
            features = _gdf_to_geojson_features(data.to_crs("EPSG:4326"))
            entry = {"kind": kind.value, "features": features}
        cache.set(key, entry, expire=ttl)

    def _cache_key(self, request: GeoRequest) -> str:
        return f"{self.layer_id}:{request.cache_key(self.layer_id)}"

    def __repr__(self):
        from types import SimpleNamespace
        lines = [f"{type(self).__name__}"]
        lines.append(f"  layer_id : {self.layer_id!r}")
        lines.append(f"  url      : {self.url!r}")
        for k, v in self.__dict__.items():
            if k.startswith("_") or k in ("kwargs", "layer_id", "url"):
                continue
            if isinstance(v, SimpleNamespace):
                attrs = {sk: sv for sk, sv in vars(v).items()
                         if sv is not None and sv != {} and sv != []}
                if attrs:
                    lines.append(f"  {k}:")
                    for sk, sv in attrs.items():
                        lines.append(f"    {sk} : {sv!r}")
            elif v is not None and v != {} and v != []:
                lines.append(f"  {k} : {v!r}")
        return "\n".join(lines)
