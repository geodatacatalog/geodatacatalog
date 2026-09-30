"""Source registry — load and save the sources.yml catalogue of known services.

The registry is the single source of truth of the explorer sub-project:
``SOURCES.md`` is generated from it and the ``check`` command writes the
probe results back into it.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

#: Registry shipped with the package (editable installs point at the repo copy).
DEFAULT_REGISTRY = Path(__file__).parent / "sources.yml"

SOURCE_TYPES = {"stac", "wfs", "wms", "wcs", "wmts", "rest", "xyz", "csw", "registry"}
AUTH_TYPES = {"none", "api_key", "oauth"}


@dataclass
class Source:
    """One geospatial data service.

    Attributes:
        id: Unique slug used by the CLI (``geodatacatalog layers <id>``).
        name: Human readable name.
        type: Protocol — one of :data:`SOURCE_TYPES`. ``rest`` is any generic
            HTTP API, ``xyz`` a tile template, ``registry`` a meta-catalogue
            (only probed with a plain GET).
        endpoint: Service root URL (no OGC query params).
        provider: Organisation operating the service.
        coverage: Spatial coverage (``global``, ``europe``, ``france``, ...).
        themes: Canonical themes (see :mod:`geodatacatalog.explorer.search`).
        auth: ``none`` | ``api_key`` | ``oauth``.
        auth_url: Optional URL where a user registers / obtains the credentials
            this source needs (shown in the frontend's info panel, and turned into
            a clickable link on the auth cell in SOURCES.md). Use it even for
            ``auth: none`` sources whose *data assets* are gated downstream
            (e.g. NASA Earthdata login, Copernicus Data Space S3 keys).
        paid: True for services with no free tier (credentials/contract are
            themselves paid, not just optional for higher quotas) — flagged with
            a small icon next to the name in SOURCES.md so a paid-only source
            doesn't look like the free/free-tier default of most of this registry.
        test: Optional probe/preview hints: ``layer`` (OGC), ``collection``
            (STAC), ``url`` (rest/xyz/registry override).
        layers: Curated layer list for sources without capabilities discovery
            (REST APIs): ``[{id, title}, ...]`` or plain strings. OGC/STAC
            sources don't need it — their layers are discovered live.
        catalog: Extra ``search`` config merged into the generated catalog.yml
            block (e.g. REST ``queryParam``/``bboxParams``/``paginate``/
            ``lonField``...). Lets ``add``/``init-catalog`` emit a working
            provider for APIs that need more than the defaults.
        discover: Keyword-search config for a non-CSW catalogue of datasets.
            ``{"api": "ckan"|"datagouv"|"opendatasoft"|"arcgis_hub"}`` searches
            an open-data platform live (CKAN ``package_search``, data.gouv.fr,
            OpenDataSoft Explore, ArcGIS Hub; ``dataset_page`` optionally
            templates the human page for CKAN ids).
            ``{"dataset_list": <url>}`` points at a single flattened JSON list
            of datasets (``[{id, title, tags, provider, ...}, ...]``) searched
            by :func:`geodatacatalog.explorer.discover.discover` (e.g. the
            Google Earth Engine data catalog). CSW sources need no config —
            ``type: csw`` is enough.
        notes: Free text shown in search results, and in full inside the
            collapsible detail of SOURCES.md's compact listing.
        summary: Optional short (~80-100 char) one-line description shown
            directly in SOURCES.md's compact listing. Falls back to a
            truncated ``notes`` when empty — fill in by hand only where the
            auto-truncation reads awkwardly.
        status: Written by ``geodatacatalog check``:
            ``{state, latency_ms, checked_at, detail}``.
    """

    id: str
    name: str
    type: str
    endpoint: str
    provider: str = ""
    coverage: str = "global"
    themes: list[str] = field(default_factory=list)
    auth: str = "none"
    auth_url: str = ""
    paid: bool = False
    test: dict = field(default_factory=dict)
    layers: list = field(default_factory=list)
    catalog: dict = field(default_factory=dict)
    discover: dict = field(default_factory=dict)
    notes: str = ""
    summary: str = ""
    status: dict | None = None
    fetch: dict | None = None

    def __post_init__(self):
        if self.type not in SOURCE_TYPES:
            raise ValueError(f"[{self.id}] unknown type {self.type!r} (expected one of {sorted(SOURCE_TYPES)})")
        if self.auth not in AUTH_TYPES:
            raise ValueError(f"[{self.id}] unknown auth {self.auth!r} (expected one of {sorted(AUTH_TYPES)})")

    def to_dict(self) -> dict:
        d = asdict(self)
        # keep the YAML lean: drop empty optional fields
        for key in ("provider", "notes", "auth_url", "summary"):
            if not d[key]:
                d.pop(key)
        if not d["paid"]:
            d.pop("paid")
        if not d["test"]:
            d.pop("test")
        if not d["layers"]:
            d.pop("layers")
        if not d["catalog"]:
            d.pop("catalog")
        if not d["discover"]:
            d.pop("discover")
        if d["status"] is None:
            d.pop("status")
        if d["fetch"] is None:
            d.pop("fetch")
        return d


def load_registry(path: str | Path | None = None) -> list[Source]:
    """Load the source registry (defaults to the one shipped with the package)."""
    import yaml

    path = Path(path) if path else DEFAULT_REGISTRY
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []

    sources = [Source(**entry) for entry in raw]
    ids = [s.id for s in sources]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate source ids in {path}: {sorted(dupes)}")
    return sources


def save_registry(sources: list[Source], path: str | Path | None = None) -> Path:
    """Write the registry back to YAML (stable key order, unicode preserved)."""
    import yaml

    path = Path(path) if path else DEFAULT_REGISTRY
    payload = [s.to_dict() for s in sources]
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=110)
    header = (
        "# Registry of open geospatial data services used by `geodatacatalog` "
        "(explorer sub-project).\n"
        "# Edit this file to add sources; SOURCES.md is GENERATED from it.\n"
        "# Run `geodatacatalog check` to refresh the status fields.\n"
    )
    path.write_text(header + text, encoding="utf-8")
    log.info(f"registry saved: {path} ({len(sources)} sources)")
    return path


def get_source(sources: list[Source], source_id: str) -> Source:
    for s in sources:
        if s.id == source_id:
            return s
    raise KeyError(f"unknown source id {source_id!r} — see `geodatacatalog search`")
