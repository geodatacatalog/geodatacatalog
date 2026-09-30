"""Python plugin provider — a local module exposing a ``fetch()`` function."""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import geopandas as gpd
import xarray as xr

from ..base import BaseLayer, LayerKind
from ..request import GeoRequest

log = logging.getLogger(__name__)


class PythonPluginLayer(BaseLayer):
    """Wrap a local Python module as a catalog provider.

    The module must expose::

        def fetch(request, product_type, config):
            '''
            request      : geodatacatalog.GeoRequest (bbox, datetime, crs, resolution, ...)
            product_type : the ``productType`` string from the catalog config
            config       : the full provider config dict (auth, search, ...)

            Returns a GeoDataFrame (vector), an xarray.DataArray/Dataset (raster),
            or a list of pystac.Item.
            '''

    See ``examples/plugin_template.py`` for a complete, commented template.

    Results are disk-cached by :class:`BaseLayer` like any other provider
    (the cache entry records the actual output kind). Note: a returned
    ``xr.Dataset`` is cached as a multi-band GeoTIFF and reloads as a
    ``DataArray``.

    YAML configuration::

        my_provider:
            search:
                type: my_plugin.py      # file looked up in plugin_search_dirs
                api_endpoint: https://...
            products:
                my_product:
                    productType: some-identifier
    """

    def __init__(self, layer_id, url, module_path: str, product_type: str,
                 provider_config: dict | None = None, **kwargs):
        super().__init__(layer_id, url, **kwargs)
        self.module_path = module_path
        self.product_type = product_type
        self.provider_config = provider_config or {}
        self._module = None

    def _load_module(self):
        if self._module is None:
            spec = importlib.util.spec_from_file_location(
                Path(self.module_path).stem, self.module_path
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not callable(getattr(mod, "fetch", None)):
                raise TypeError(
                    f"Plugin module '{self.module_path}' must define a fetch() function"
                )
            self._module = mod
        return self._module

    @property
    def kind(self) -> LayerKind:
        return LayerKind.VECTOR  # actual kind is inferred from the returned data

    def _infer_kind(self, data) -> LayerKind:
        if isinstance(data, (xr.DataArray, xr.Dataset)):
            return LayerKind.RASTER
        if isinstance(data, list):
            return LayerKind.STAC_ITEMS
        if isinstance(data, gpd.GeoDataFrame):
            return LayerKind.VECTOR
        raise TypeError(
            f"[{self.layer_id}] plugin fetch() returned {type(data).__name__}; "
            "expected GeoDataFrame, DataArray, Dataset or list[pystac.Item]"
        )

    def _fetch_sync(self, request: GeoRequest):
        mod = self._load_module()
        return mod.fetch(request, self.product_type, self.provider_config)
