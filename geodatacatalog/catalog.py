"""GeoDataCatalog — multi-provider catalogue with caching and parallel fetch."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import diskcache
import pandas as pd
import xarray as xr

from .base import BaseLayer, LayerKind
from .request import GeoRequest
from .results import CatalogResults, _clip_to_aoi, _make_sub_request

log = logging.getLogger(__name__)


class _NullCache:
    """No-op cache — never hits, never writes. Used when no_cache=True."""
    _is_noop = True

    def __contains__(self, key): return False
    def __getitem__(self, key): raise KeyError(key)
    def set(self, key, value, **kwargs): pass
    def get(self, key, default=None): return default


class _FakeBool:
    """Mimics a Checkbox widget (fallback when ipywidgets is unavailable)."""
    def __init__(self, value: bool):
        self.value = value


class LayerSelection:
    """Result of :meth:`GeoDataCatalog.select_layers`.

    Tick the layers in the Jupyter widget, then call ``.get()`` to retrieve
    the list of selected layer ids.
    """

    def __init__(self, checkboxes: dict):
        self._checkboxes = checkboxes

    def get(self) -> list[str]:
        return [lid for lid, cb in self._checkboxes.items() if cb.value]

    def __repr__(self) -> str:
        selected = self.get()
        return f"LayerSelection({len(selected)} selected layers): {selected}"


class GeoDataCatalog:
    """Multi-provider catalogue with disk caching and parallel download.

    Layers are usually built from a YAML file via :func:`geodatacatalog.load_catalog`,
    but the catalog can also be assembled programmatically::

        catalog = GeoDataCatalog(
            layers=[
                WFSLayer("communes", "https://data.geopf.fr/wfs/ows",
                         typename="ADMINEXPRESS-COG.LATEST:commune"),
                WMTSLayer("foret", "https://data.geopf.fr/wmts",
                           layer_name="LANDCOVER.FORESTINVENTORY.V2",
                           legend={"#6CBB3C": "Feuillus"}),
                STACLayer("s2", "https://earth-search.aws.element84.com/v1",
                          collection="sentinel-2-l2a", bands=["red","green","blue"],
                          cloud_cover=20, band_mapping={"red":"red","green":"green","blue":"blue"}),
            ],
            cache_dir="./geo_cache",
        )

        aoi = gpd.read_file("zone.gpkg")
        request = GeoRequest(
            aoi_gdf=aoi,
            crs="EPSG:4326",
            resolution=10,
            datetime="2024-06-01/2024-08-31",
        )
        results = catalog.fetch(request)
        # → CatalogResults with .explore(), .export(), .sel(), .load()
    """

    def __init__(
        self,
        layers: list[BaseLayer],
        cache_dir: str = "./geo_cache",
        cache_ttl: int = 86400,
        max_workers: int = 4,
        http_timeout: int = 60,
        user_agent: str = "geodatacatalog (https://pypi.org/project/geodatacatalog/)",
    ):
        self.layers: dict[str, BaseLayer] = {l.layer_id: l for l in layers}
        self.cache = diskcache.Cache(cache_dir)
        self.cache.ttl = cache_ttl  # read by layers as the `expire=` of new entries
        self.max_workers = max_workers
        self.http_timeout = http_timeout
        self.user_agent = user_agent
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(
        self,
        request: GeoRequest,
        layers: list[str] | None = None,
        parallel_requests: bool | int = True,
        no_cache: bool = False,
    ) -> "CatalogResults":
        """Download the requested layers and clip them to the AOI if provided.

        If ``request.aoi_gdf`` contains several polygons, one sub-fetch per
        polygon is launched and the results are merged.

        Args:
            request: The unified :class:`GeoRequest`.
            layers: Layer ids to fetch (default: every layer in the catalog).
            parallel_requests: ``False`` disables asyncio.gather — useful for
                debugging or providers that throttle concurrent connections.
                An int limits the number of concurrent polygons (semaphore).
            no_cache: Bypass the disk cache entirely (no reads, no writes).
        """
        target = list(self.layers.keys()) if layers is None else layers
        cache = _NullCache() if no_cache else self.cache
        if request.aoi_gdf is not None and len(request.aoi_gdf) > 1:
            aoi_4326 = request.aoi_gdf.to_crs("EPSG:4326")
            id_col = next((c for c in aoi_4326.columns if c.lower() in ("id", "fid", "uid", "identifiant")), None)
            sub_requests = [
                _make_sub_request(
                    request, row,
                    row.get("datetime") if "datetime" in aoi_4326.columns else None,
                    polygon_id=row[id_col] if id_col else idx,
                )
                for idx, row in aoi_4326.iterrows()
            ]
            all_raw_list = self._run_async(self._fetch_all_polygons(sub_requests, target, parallel=parallel_requests, cache=cache))
            poly_results = []
            for raw, sub_req in zip(all_raw_list, sub_requests):
                pid = sub_req.polygon_id
                for layer_data in raw.values():
                    if isinstance(layer_data, list) and layer_data:
                        try:
                            import pystac as _pystac
                            if isinstance(layer_data[0], _pystac.Item):
                                for item in layer_data:
                                    item.properties["_polygon_id"] = pid
                        except Exception:
                            pass
                poly_results.append(CatalogResults(raw, self, sub_req))
            return CatalogResults(poly_results, self, request)
        else:
            errors: dict = {}
            raw = self._run_async(self._fetch_all(request, target, parallel=parallel_requests,
                                                  cache=cache, errors=errors))
        results = CatalogResults(raw, self, request)
        results.errors = errors  # {layer_id: message} for layers whose fetch raised
        return results

    def _run_async(self, coro):
        try:
            asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.get_event_loop().run_until_complete(coro)
        except ImportError:
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        except RuntimeError:
            return asyncio.run(coro)

    async def _fetch_all_polygons(self, sub_requests: list, layer_ids: list[str], parallel: bool | int = True, cache=None) -> dict:
        n = len(sub_requests)
        log.info(f"Fetching {n} polygons × {len(layer_ids)} layers")
        polygon_ids = [r.polygon_id for r in sub_requests]
        if cache is None:
            cache = self.cache

        counter = 0

        if parallel:
            sem = asyncio.Semaphore(parallel if isinstance(parallel, int) else 3)

            async def _fetch_with_sem(req):
                nonlocal counter
                async with sem:
                    result = await self._fetch_all(req, layer_ids, parallel=True, cache=cache)
                counter += 1
                log.info(f"Polygon {counter}/{n} OK")
                return result

            all_raw = await asyncio.gather(
                *[_fetch_with_sem(req) for req in sub_requests],
                return_exceptions=True,
            )
        else:
            all_raw = []
            for req in sub_requests:
                try:
                    r = await self._fetch_all(req, layer_ids, parallel=False, cache=cache)
                    all_raw.append(r)
                    counter += 1
                    log.info(f"Polygon {counter}/{n} OK")
                except Exception as e:
                    all_raw.append(e)

        results = []
        for i, r in enumerate(all_raw):
            if isinstance(r, Exception):
                log.warning(f"Polygon {i + 1}/{n} failed: {r}")
                results.append({lid: None for lid in layer_ids})
            else:
                results.append(r)
        return results

    def clear_cache(self, layer_id: str | None = None):
        """Clear the disk cache (index entries and cached GeoTIFF files)."""
        if layer_id:
            keys = [k for k in self.cache.iterkeys() if k.startswith(f"{layer_id}:")]
            for k in keys:
                del self.cache[k]
            for f in self._cache_dir.glob(f"{layer_id}:*.tif"):
                f.unlink(missing_ok=True)
            log.info(f"Cache cleared for {layer_id} ({len(keys)} entries)")
        else:
            self.cache.clear()
            for f in self._cache_dir.glob("*.tif"):
                f.unlink(missing_ok=True)
            log.info("Cache fully cleared")

    def add_layer(self, layer: BaseLayer):
        self.layers[layer.layer_id] = layer

    def remove_layer(self, layer_id: str):
        self.layers.pop(layer_id, None)

    def info(self) -> "pd.DataFrame":
        """Return a DataFrame describing every layer in the catalog."""
        import pandas as pd
        rows = []
        for lid, l in self.layers.items():
            parts = lid.split("__", 1)
            rows.append({
                "layer_id": lid,
                "provider": parts[0],
                "product": parts[1] if len(parts) > 1 else "",
                "type": type(l).__name__.replace("Layer", ""),
                "kind": l.kind.name,
                "url": l.url,
            })
        return pd.DataFrame(rows)

    def select_layers(
        self,
        kind: str | None = None,
        provider: str | None = None,
        default_selected: list[str] | None = None,
    ) -> "LayerSelection":
        """Display an interactive Jupyter widget to pick layers.

        Args:
            kind: "RASTER" | "VECTOR" | None — filter by data type.
            provider: filter by provider name (e.g. "ign").
            default_selected: pre-ticked layer ids (default: none).

        Returns:
            LayerSelection — call ``.get()`` to retrieve the selected layer ids.

        Example::

            sel = catalog.select_layers(kind="RASTER")
            results = catalog.fetch(request, layers=sel.get())
        """
        candidates = {
            lid: l for lid, l in self.layers.items()
            if (kind is None or l.kind.name == kind)
            and (provider is None or lid.startswith(provider + "__"))
        }

        try:
            import ipywidgets as widgets
            from IPython.display import display

            checkboxes = {}
            for lid, l in candidates.items():
                label = f"{lid}  [{type(l).__name__.replace('Layer', '')} · {l.kind.name}]"
                checked = (default_selected is not None) and (lid in default_selected)
                checkboxes[lid] = widgets.Checkbox(
                    value=checked,
                    description=label,
                    indent=False,
                    layout=widgets.Layout(width="700px"),
                )

            display(widgets.VBox(list(checkboxes.values())))
            return LayerSelection(checkboxes)

        except ImportError:
            import pandas as pd
            rows = []
            for lid, l in candidates.items():
                parts = lid.split("__", 1)
                rows.append({
                    "layer_id": lid,
                    "provider": parts[0],
                    "product": parts[1] if len(parts) > 1 else "",
                    "type": type(l).__name__.replace("Layer", ""),
                    "kind": l.kind.name,
                })
            print(pd.DataFrame(rows).to_string(index=False))
            selected = default_selected if default_selected is not None else []
            return LayerSelection({lid: _FakeBool(lid in selected) for lid in candidates})

    def from_files(self, path: str) -> "CatalogResults":
        """Reconstruit un CatalogResults depuis des fichiers exportés, avec ce catalogue attaché.

        Équivalent à CatalogResults.from_files(path) mais avec catalog=self, ce qui permet
        d'appeler .load() sur les items STAC rechargés.

        Args:
            path: Dossier (ex: "output/") ou pattern glob (ex: "output/*fwi*.tif")
        """
        results = CatalogResults.from_files(path)
        results._catalog = self
        if results._poly_list is not None:
            for cr in results._poly_list:
                cr._catalog = self
        return results

    # --- Internals --------------------------------------------------------

    async def _fetch_all(self, request: GeoRequest, layer_ids: list[str], parallel: bool | int = True,
                         cache=None, errors: dict | None = None) -> dict:
        import aiohttp

        if cache is None:
            cache = self.cache
        connector = aiohttp.TCPConnector(limit=self.max_workers)
        headers = {"User-Agent": self.user_agent}
        timeout = aiohttp.ClientTimeout(total=self.http_timeout)

        async with aiohttp.ClientSession(
            connector=connector, headers=headers, timeout=timeout
        ) as session:
            for lid in layer_ids:
                if lid not in self.layers:
                    log.warning(f"Layer '{lid}' not found in the catalog. Available: {list(self.layers.keys())}")
            tasks = {
                lid: self.layers[lid].fetch_async(request, cache, session, parallel=parallel)
                for lid in layer_ids
                if lid in self.layers
            }
            if parallel:
                results_raw = await asyncio.gather(*tasks.values(), return_exceptions=True)
            else:
                results_raw = []
                for coro in tasks.values():
                    try:
                        results_raw.append(await coro)
                    except Exception as e:
                        results_raw.append(e)

        output = {}
        for layer_id, result in zip(tasks.keys(), results_raw):
            if isinstance(result, Exception):
                import traceback as _tb
                tb = "".join(_tb.format_exception(type(result), result, result.__traceback__))
                log.error(f"[{layer_id}] Error: {result}\n{tb}")
                if errors is not None:
                    errors[layer_id] = f"{type(result).__name__}: {result}"
                output[layer_id] = None
            else:
                status = "cache" if result.cached else "downloaded"
                if result.kind == LayerKind.VECTOR:
                    count = f"{len(result.data):,} features"
                elif result.kind == LayerKind.STAC_ITEMS:
                    count = f"{len(result.data):,} STAC items"
                else:
                    d = result.data
                    if isinstance(d, xr.Dataset):
                        d = next(iter(d.data_vars.values()))
                    count = f"raster {' × '.join(str(s) for s in d.shape[-2:])}"
                layer_type = type(self.layers[layer_id]).__name__.replace("Layer", "")
                log.info(f"[{layer_id}] OK ({status}) [{layer_type}] — {count}")
                data = result.data
                if request.aoi_gdf is not None:
                    data = _clip_to_aoi(data, request.aoi_gdf)
                output[layer_id] = data

        return output
