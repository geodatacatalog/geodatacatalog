"""All layer types, re-exported for convenience."""
from .wfs import WFSLayer
from .wcs import WCSLayer
from .wms import WMSLayer
from .wmts import WMTSLayer
from .stac import STACLayer
from .plugin import PythonPluginLayer
from .local import LocalLayer
from .osmnx import OSMNXLayer
from .rest import RESTLayer
from .xyz import XYZLayer

__all__ = [
    "WFSLayer",
    "WCSLayer",
    "WMSLayer",
    "WMTSLayer",
    "STACLayer",
    "PythonPluginLayer",
    "LocalLayer",
    "OSMNXLayer",
    "RESTLayer",
    "XYZLayer",
]
