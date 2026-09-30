"""Bilingual (FR/EN) thematic search over the source registry."""
from __future__ import annotations

import unicodedata

from .registry import Source

#: Canonical theme → FR/EN synonyms (accents are stripped before matching,
#: so "forêt" and "foret" are equivalent).
THEMES: dict[str, list[str]] = {
    "satellite": ["satellite", "sentinel", "landsat", "modis", "spot", "pleiades",
                  "observation de la terre", "earth observation", "eo", "sar", "radar"],
    "imagery": ["imagery", "imagerie", "image", "orthophoto", "ortho", "aerial",
                "aerien", "photo aerienne", "raster"],
    "forest": ["forest", "foret", "canopee", "canopy", "arbre", "tree", "bois",
               "woodland", "sylviculture", "deforestation"],
    "soil": ["soil", "sol", "pedologie", "pedology", "argile", "clay", "sable",
             "sand", "carbone organique", "organic carbon", "humus"],
    "water": ["water", "eau", "riviere", "river", "fleuve", "hydrologie", "hydrology",
              "hydrometrie", "nappe", "groundwater", "piezometre", "lac", "lake", "crue", "flood"],
    "ocean": ["ocean", "mer", "sea", "marine", "marin", "bathymetrie", "bathymetry",
              "littoral", "coastal", "cote", "maree", "tide"],
    "elevation": ["elevation", "altitude", "dem", "dtm", "mnt", "mns", "terrain",
                  "relief", "topographie", "topography", "lidar", "pente", "slope"],
    "landcover": ["landcover", "land cover", "occupation du sol", "land use",
                  "usage des sols", "corine", "clc", "oso", "vegetation"],
    "fire": ["fire", "feu", "incendie", "wildfire", "brule", "burned", "burnt",
             "fwi", "combustible", "fuel"],
    "biodiversity": ["biodiversity", "biodiversite", "espece", "species", "faune",
                     "fauna", "flore", "flora", "habitat", "natura", "znieff",
                     "aire protegee", "protected area", "parc", "park", "ecologie", "ecology"],
    "geology": ["geology", "geologie", "geologique", "faille", "fault", "roche",
                "rock", "lithologie", "lithology", "seisme", "earthquake", "mineral"],
    "weather": ["weather", "meteo", "meteorologie", "forecast", "prevision",
                "precipitation", "pluie", "rain", "vent", "wind", "temperature"],
    "climate": ["climate", "climat", "climatique", "era5", "rechauffement",
                "warming", "atmosphere", "co2", "emission"],
    "agriculture": ["agriculture", "agricole", "culture", "crop", "parcelle",
                    "rpg", "farming", "irrigation", "prairie"],
    "admin": ["admin", "administratif", "administrative", "commune", "departement",
              "region", "boundary", "limite", "frontiere", "cadastre", "cadastral",
              "parcel", "geocodage", "geocoding", "adresse", "address"],
    "infrastructure": ["infrastructure", "route", "road", "batiment", "building",
                       "reseau", "network", "energie", "energy"],
    "urban": ["urban", "urbain", "urbanisme", "ville", "city", "plu", "zonage", "zoning"],
    "population": ["population", "demographie", "demography", "demographique", "recensement",
                   "census", "habitants", "peuplement", "worldpop", "kontur"],
    "economy": ["economy", "economie", "economique", "socioeconomic", "richesse", "wealth",
                "pauvrete", "poverty", "revenu", "income", "gdp", "pib", "emploi",
                "employment", "worldbank"],
    "transport": ["transport", "mobilite", "mobility", "trafic", "traffic"],
    "air": ["trafic aerien", "air traffic", "avion", "avions", "aeronef", "aeronefs",
            "aviation", "aircraft", "flight", "adsb"],
    "maritime": ["maritime", "transport maritime", "trafic maritime", "navire", "navires",
                 "vessel", "vessels", "ship", "shipping", "bateau", "bateaux", "ais"],
    "rail": ["rail", "railway", "chemin de fer", "train", "trains", "ferroviaire", "railroad"],
    "basemap": ["basemap", "fond de carte", "fond de plan", "carte", "map",
                "topographique", "tuile", "tile", "plan"],
    "hazards": ["hazards", "risque", "risk", "catastrophe", "disaster", "alerte",
                "alert", "inondation", "submersion", "urgence", "emergency"],
    "meta": ["meta", "catalogue", "catalog", "registre", "registry", "annuaire",
             "index", "portail", "portal"],
}


def _normalize(text: str) -> str:
    """Lowercase and strip accents so FR/EN queries compare cleanly."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))

# Normalised synonym → canonical theme (longest synonyms first for phrase matching)
_SYNONYMS: dict[str, str] = {
    _normalize(syn): theme for theme, syns in THEMES.items() for syn in syns
}


def themes_for_query(query: str) -> set[str]:
    """Resolve a free-text FR/EN query to canonical themes."""
    q = _normalize(query)
    words = q.split()
    matched: set[str] = set()
    for syn, theme in _SYNONYMS.items():
        if " " in syn:
            if syn in q:
                matched.add(theme)
        elif any(w.startswith(syn) or syn.startswith(w) and len(w) >= 4 for w in words):
            matched.add(theme)
    return matched


def search_sources(query: str, sources: list[Source],
                   coverage: str | None = None) -> list[tuple[Source, float]]:
    """Search the registry by FR/EN keywords.

    Scoring: theme match (resolved through the synonym table) counts most,
    then free-text hits in name/notes/provider/id. Sources whose last check
    failed are ranked below working ones at equal score.

    Returns ``[(source, score), ...]`` sorted by decreasing relevance.
    """
    q = _normalize(query)
    words = [w for w in q.split() if len(w) >= 3]
    wanted_themes = themes_for_query(query)

    def _hits(word: str, tokens: set[str]) -> bool:
        # whole-token match; prefix match only for long words ("bathym" → "bathymetrie")
        return word in tokens or (len(word) >= 5 and any(t.startswith(word) for t in tokens))

    results = []
    for src in sources:
        if coverage and src.coverage not in (coverage, "global"):
            continue
        score = 0.0
        score += 10.0 * len(wanted_themes & set(src.themes))
        haystack = _normalize(" ".join([src.id, src.name, src.notes, src.provider]))
        tokens = set(haystack.replace("_", " ").replace("-", " ").split())
        score += 3.0 * sum(1 for w in words if _hits(w, tokens))
        if q and len(q) >= 5 and q in haystack:
            score += 5.0
        if score <= 0:
            continue
        state = (src.status or {}).get("state")
        if state == "ok":
            score += 0.5
        elif state == "fail":
            score -= 2.0
        results.append((src, score))

    return sorted(results, key=lambda t: -t[1])


#: search.type / registry type values that yield raster data, and raster plugins.
_RASTER_TYPES = {"wms", "wmts", "wcs", "stac", "stacsearch", "xyz"}
_RASTER_PLUGINS = {"geodata_sentinelhubapi.py"}   # plugins returning an xarray DataArray


def kind_for(search_type: str | None, plugin: str | None = None) -> str:
    """``'raster'`` | ``'vector'`` for a layer, from its catalog ``search.type`` /
    registry type (+ optional plugin file). Lets the frontend pick the right action
    (GeoTIFF vs GeoJSON) for plugin-backed sources the type string alone can't tell."""
    st = (search_type or "").lower()
    pl = plugin or (search_type if st.endswith(".py") else None)
    return "raster" if (st in _RASTER_TYPES or pl in _RASTER_PLUGINS) else "vector"


#: search.type / registry types whose layers never carry a per-request datetime.
_STATIC_TYPES = {"wfs", "wmts", "osmnx", "local"}


def temporal_for(search_type: str | None, config: dict | None = None) -> bool | None:
    """Whether a layer of this type/config forwards the request datetime to the service.

    ``True`` (the chosen date is sent in the request — a temporal layer), ``False``
    (never — a static layer), or ``None`` when it depends on the live server and
    can't be told from the config alone (a WMS/WCS ``TIME`` dimension, or a free-form
    plugin). Lets the frontend flag temporal layers with a symbol. ``config`` is the
    merged search+product block. For OGC layers the live capabilities give a precise
    answer (see :func:`geodatacatalog.explorer.inspect._ogc_is_temporal`)."""
    st = (search_type or "").lower()
    cfg = config or {}
    if st in ("stac", "stacsearch"):
        return not bool(cfg.get("static"))           # STAC search always sends datetime
    if st == "rest":
        return bool(cfg.get("datetimeParam") or cfg.get("datetime_param"))
    if st == "wms":
        return True if cfg.get("time") else None      # else depends on the layer's TIME dim
    if st == "wcs":
        return None                                   # some coverages carry a time axis
    if st == "xyz":
        # a manifest-driven template (RainViewer frames) picks its frame from the
        # request datetime; a bare tile template never sees it
        return bool(cfg.get("manifest"))
    if st in _STATIC_TYPES:
        return False
    return None                                       # plugin / unknown


def _is_drillable(src: Source) -> bool:
    """Whether a source's layers can be enumerated for the catalog browser.

    Curated ``layers`` always drill; OGC/STAC sources are drilled live. A REST
    source is otherwise drillable only when its routes are actually listable —
    a product-listing plugin or a recorded ``test.openapi`` spec; a free-form
    query REST with none of these can't be enumerated (would 422). An ``xyz``
    tile template drills to its single synthetic ``tiles`` layer. Everything
    else (``registry``, ``csw``, ...) has no layer list to drill — mirrors
    :func:`~geodatacatalog.explorer.inspect.list_layers`.
    """
    if src.layers:
        return True
    if src.type == "rest":
        return bool(src.catalog.get("plugin") or src.test.get("openapi"))
    return src.type in ("stac", "wfs", "wms", "wcs", "wmts", "xyz")


def source_record(src: Source) -> dict:
    """Compact, frontend-friendly view of a registry source (catalog browser)."""
    return {
        "id": src.id,
        "name": src.name,
        "type": src.type,
        "kind": kind_for(src.type, src.catalog.get("plugin")),
        "provider": src.provider,
        "coverage": src.coverage,
        "themes": list(src.themes),
        "auth": src.auth,
        "auth_url": src.auth_url or None,   # where to register / get credentials
        "endpoint": src.endpoint,
        "notes": src.notes,
        "status": (src.status or {}).get("state"),
        "n_layers": len(src.layers),
        # registry meta-catalogues are health-checked only; CSW catalogues index
        # OTHER services and are keyword-searched (see `discoverable`) — neither
        # exposes layers to drill/list directly. A REST source is only drillable
        # when its layers can actually be enumerated: a curated `layers` list, a
        # product-listing plugin, or a recorded OpenAPI spec. A free-form REST API
        # with none of these (e.g. copernicus_cds, which needs the cdsapi client)
        # can't be drilled — don't offer it, so the frontend never gets a 422.
        "drillable": _is_drillable(src),
        # Meta-catalogues are searched with `discover` (GET /sources/{id}/discover):
        # CSW services, open-data platforms (`discover: {api: ckan|datagouv|
        # opendatasoft|arcgis_hub}`), plus sources with a `discover:
        # {dataset_list: url}` config (e.g. the Google Earth Engine data catalog).
        "discoverable": (src.type == "csw" or bool(src.discover.get("api"))
                         or bool(src.discover.get("dataset_list"))),
    }


def browse_sources(query: str | None, sources: list[Source], *,
                   theme: str | None = None, coverage: str | None = None) -> list[dict]:
    """Filtered, ranked source records for the catalog browser.

    With a free-text ``query`` → ranked via :func:`search_sources`; otherwise all
    sources, sorted by name. ``theme``/``coverage`` narrow the list further.
    """
    if query:
        picked = [s for s, _ in search_sources(query, sources, coverage=coverage)]
    else:
        picked = sorted(sources, key=lambda s: s.name.lower())
        if coverage:
            picked = [s for s in picked if s.coverage in (coverage, "global")]
    if theme:
        picked = [s for s in picked if theme in s.themes]
    return [source_record(s) for s in picked]
