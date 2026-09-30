"""GeoDataCatalog — fetch heterogeneous geospatial sources through one catalog.

Aggregate WFS, WMS, WMTS, WCS, STAC, OSM, REST APIs and custom Python plugins
behind a single YAML configuration, with disk caching, parallel download and
normalised outputs (GeoDataFrame, xarray, pystac items).

Quickstart::

    from geodatacatalog import load_catalog, GeoRequest

    catalog = load_catalog("catalog.yml")
    request = GeoRequest(bbox=(1.42, 43.56, 1.46, 43.59),
                         datetime="2024-06-01/2024-08-31")
    results = catalog.fetch(request, layers=["ign__communes"])
"""
from .aoi import geocode_aoi
from .base import BaseLayer, LayerKind, LayerResult
from .catalog import GeoDataCatalog, LayerSelection
from .config import load_catalog
from .request import GeoRequest
from .results import CatalogResults

__version__ = "0.1.0"

__all__ = [
    "BaseLayer",
    "CatalogResults",
    "GeoDataCatalog",
    "GeoRequest",
    "LayerKind",
    "LayerResult",
    "LayerSelection",
    "geocode_aoi",
    "load_catalog",
    "__version__",
]
