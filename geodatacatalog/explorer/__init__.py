"""Explorer sub-project: discover, check and configure geospatial data sources.

A curated registry of open services (``sources.yml``) with:
- bilingual FR/EN thematic search (:func:`search_sources`),
- service health checks (:func:`check_sources`) feeding the generated SOURCES.md,
- functional fetch smoke-tests (:func:`verify_sources`),
- layer listing / Folium previews, and ``catalog.yml`` generation.

CLI entry point: ``geodatacatalog`` (see ``geodatacatalog --help``).
"""
from .discover import discover
from .geocode import geocode_place
from .inspect import init_catalog, list_layers, merge_into_catalog, preview, to_catalog_provider
from .markdown import render_markdown, write_markdown
from .probe import check_sources
from .registry import Source, get_source, load_registry, save_registry
from .search import THEMES, search_sources
from .verify import apply_results, test_bboxes_geojson, verify_source, verify_sources

__all__ = [
    "Source",
    "THEMES",
    "apply_results",
    "check_sources",
    "discover",
    "geocode_place",
    "get_source",
    "init_catalog",
    "list_layers",
    "load_registry",
    "merge_into_catalog",
    "preview",
    "render_markdown",
    "save_registry",
    "search_sources",
    "test_bboxes_geojson",
    "to_catalog_provider",
    "verify_source",
    "verify_sources",
    "write_markdown",
]
