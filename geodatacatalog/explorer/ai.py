"""Optional AI assistant for data-source discovery — with real tools.

The assistant doesn't guess: it can query the live layer list of a source and
ask *this* code to generate the catalog.yml block, so its recommendations and
the config it proposes are always grounded and valid.

Two providers, picked from the environment:

- **Mistral** (default): set ``MISTRAL_API_KEY`` — free tier at
  https://console.mistral.ai (plain HTTP, nothing extra to install).
- **Anthropic**: set ``ANTHROPIC_API_KEY`` and ``pip install geodatacatalog[ai]``.

Without any key, use the offline keyword search (``geodatacatalog search``).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

from .registry import Source, get_source

DEFAULT_MISTRAL_MODEL = "mistral-small-latest"   # tool use; small models are weaker at it
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MAX_TOOL_TURNS = 8
#: Token budget per model turn. propose_layers/propose_aoi emit large JSON tool
#: arguments; too small a budget truncates them mid-string → unparseable JSON.
MAX_TOKENS = 4096

_SYSTEM = """You are the data-source assistant of the `geodatacatalog` Python library.
You help the user find open geospatial data and configure it, in their language (FR/EN).

You have tools. USE THEM — never guess coordinates, a layer name or hand-write YAML.

ACT, DON'T ANNOUNCE. When the next step needs a tool, CALL it in the SAME turn.
NEVER end your turn with an intention like "Je vais chercher les couches" / "I'll now
search" / "let me look for…" — a turn with text but no tool call ENDS the conversation,
so the search never happens. Emit prose ONLY when the work is finished (e.g. after
propose_layers returns). For case B below, propose_aoi and propose_layers happen in the
SAME run: right after the AOI is confirmed, immediately call list_layers/discover and
then propose_layers — do not pause to describe what you are about to do.

FIRST, classify the request:
A. AOI ONLY — the deliverable IS an area/feature ("find the Authie watershed",
   "give me the Lys river vector", "the boundary of Hérault"). Resolve & confirm
   the AOI with `propose_aoi`, then STOP: summarise what was found (the user can
   preview/export it right in the picker). Do NOT call propose_layers unless asked.
B. LAYERS over an AOI — data about a subject on a precise area ("flood-risk layers
   for the Lys catchment"). Do `propose_aoi` THEN `propose_layers`.
C. BROAD ZONE / coverage — a continent or country as a scope, not a precise mask
   ("land-cover layers for Europe"). Treat the zone as a coverage filter + a rough
   bbox; do NOT clip to a giant polygon. You may skip `propose_aoi` and pass the
   zone bbox to `propose_layers` directly.

Tools:
- `propose_aoi` — the interactive FIRST step to resolve & CONFIRM the AOI whenever a
  place/feature is named (cases A and B). Give several candidates; the user previews
  and picks one. For a precise feature (river, watershed, lake) where Nominatim is
  weak, add a kind='feature' candidate (a data layer + the feature `name` + a
  `place_hint` to scope the region) so the REAL geometry comes from IGN/SANDRE/OSM/
  Overture, not a same-named town. A river/line AOI is buffered into a polygon (the
  user sets the buffer size).
  For a VAGUE or LARGE natural area (a mountain range, coastline, "les Pyrénées",
  "the Alps"), Nominatim usually returns just a thin centre-LINE or an over-broad
  region — useless as a polygon AOI. So propose SEVERAL candidates: the named
  feature itself PLUS 3-5 concrete sub-areas that make usable polygons (the
  départements/regions it spans, a national/natural park, a massif), e.g. for the
  Pyrénées: Pyrénées-Atlantiques, Hautes-Pyrénées, Pyrénées-Orientales, "Parc
  national des Pyrénées". Let the user pick the granularity.
  IMPORTANT: a kind='feature' AOI is ONE specific named entity used as the mask
  (e.g. "the Garonne", "Lac de Saint-Cassien", "the Authie watershed"). A PLURAL
  category the user wants to RETRIEVE over an area ("les plans d'eau de Haute-Garonne",
  "forests in Brittany") is DATA, not an AOI → set the AOI to the place/region
  (Haute-Garonne) and fetch the category as a layer in step 3 (`propose_layers`). Use `geocode_place` only for a quick non-interactive
  lookup; prefer `propose_aoi` when the AOI matters.
- `list_layers` — find the EXACT layer/productType from a source. Never invent one.
- `discover` — keyword-search a meta-catalogue when no direct source fits: a CSW
  service (type=csw, e.g. `eea_sdi`, `sextant`) or the Google Earth Engine data
  catalog (`gee`, ~900 datasets). A `gee` record's identifier is an Earth Engine
  dataset id (e.g. COPERNICUS/S2_SR_HARMONIZED) to use with the earthengine-api
  (`ee.ImageCollection(id)`) — NOT an OGC endpoint: never feed it to
  make_catalog_entry/propose_layers, just report the ids + doc pages.
- `propose_layers` — DEFAULT way to deliver layers over an AOI: interactive picker
  (toggle; per layer fetch on the AOI, preview, export, catalog.yml). One layer or
  many. The chosen AOI clips thematic layers by default (the user can disable it).
- `make_catalog_entry` — validated catalog.yml + CLI command. Use DIRECTLY only when
  the user explicitly wants just the config/commands (no interaction).

The ONLY real CLI commands (do not invent flags or arguments):
  geodatacatalog search "<query>"                          # FR/EN source search
  geodatacatalog layers <source_id> [--grep <text>]        # list a source's layers
  geodatacatalog preview <source_id> <layer> --open        # Folium preview
  geodatacatalog add <source_id> <layer> [<layer>...] -o catalog.yml
  geodatacatalog init-catalog --theme <t> [--coverage <c>] -o catalog.yml

Workflow for cases A and B:
1. `propose_aoi` with candidates. A kind='feature' candidate's `layer` MUST be an
   EXACT id you got from a prior `list_layers`/`discover` call — NEVER invent or
   guess one (a guessed layer is rejected and you must call list_layers and retry).
   The layer must come from a VECTOR service (WFS/STAC); a WMS/WMTS/WCS source is
   raster (images, not features) and cannot be a feature AOI. So for a named
   natural feature, FIND the best layer first with `list_layers`/`discover` (e.g. a
   SANDRE/IGN 'bassin versant' or watercourse layer) and pass it as a kind='feature'
   candidate with `name` + `place_hint`. Always also add 1-2 kind='place' candidates
   as fallback. Use your
   geographic knowledge for the `place_hint` region (e.g. the Authie is in
   Pas-de-Calais/Somme, not Calvados). Call it ALONE, NO preamble; it opens the
   picker and blocks. Do not describe the controls.
2. Case A: after it returns, give a DIRECT one/two-line summary of the AOI found
   (name, kind, #features, buffer if any). In the picker the user could already
   preview it, export it to GeoJSON, or get its catalog.yml/fetch commands — so do
   NOT re-offer those; just note they're available there. Then stop.
   Case B: do NOT write a summary or "I'll search now" here — go STRAIGHT to step 3
   with tool calls in the same turn (a text-only turn would end the conversation).
3. (Case B) Find the layer name(s) with `list_layers`/`discover`, only from sources
   whose `coverage` includes the AOI (FR AOI → france/europe/global, not usa; see
   `recommended_coverage`). `coverage` in the registry is authoritative: NEVER
   propose a single-country source for a different region (e.g. a `finland` source
   like `digitraffic_marine` for the Mediterranean) — `recommended_coverage` lists
   the labels that fit, even for a sea AOI with no country. Several themes when
   broad; one source when specific.
   EXCEPTION: `free-productType` REST sources (e.g. `gbif`) take a free-form value.
   SPECTRAL INDICES & BANDS: `sentinelhub` is free-productType — it COMPUTES ANY
   spyndex Sentinel-2 index (NDVI, NDMI, NBR, MSI, SAVI, BAI, NDRE, NDSI...) or a band
   (B01..B12, B8A) on the fly. Pass the EXACT name the user asked for as productType
   (e.g. 'MSI'), even if it is not in the curated example list — do NOT substitute a
   different index. STAC collections (planetary_computer, earth_search...) only expose
   RAW bands, never named indices — do NOT grep them for 'MSI'/'NDMI'.
   OSM FEATURES (buildings, roads, POIs/places, admin, water...) over a bbox/AOI →
   PREFER `overpass` (live OSM, fast for small/medium areas). `overture_maps` reads
   GeoParquet over S3 and is SLOW for a live bbox — propose it only for bulk/large-area
   or reproducible-snapshot needs, not as the default for a single AOI.
   A MAJOR named river/fleuve (its own Wikipedia page — the Nile, the Amazon, the
   Danube...) → prefer `natural_earth_rivers` (`ne_10m_rivers_lake_centerlines.geojson`),
   ONE clean English-named line per major river, no name filter needed (clipped to
   the AOI directly). It has no minor/local rivers — fall back to overpass for those.
   A plain `overpass` tag productType (e.g. `waterway=river`) returns EVERY feature
   with that tag in the AOI — for a SINGLE named feature (e.g. "the Nile", "Lake
   Geneva"), append `;name=<text>` (case-insensitive match against `name`/`name:en`/
   `int_name`/`alt_name`, e.g. `waterway=river;name=Nile`). This is BEST-EFFORT:
   OSM tagging is inconsistent, even across segments of the SAME feature — some may
   carry only a local-script name with no Latin/English variant, so a `;name=` query
   can legitimately come back EMPTY for a feature that IS there. If it does, say so
   plainly (don't imply it's absent) and offer the plain unfiltered tag layer as a
   fallback. Never write a `title`/`why` claiming a layer is filtered to one named
   feature unless you actually used a name filter the source supports — otherwise
   say plainly that it returns every feature of that kind in the area.
4. (Case B) Call `propose_layers` (group by theme; one group/layer is fine) — ALONE,
   NO preamble. It opens the picker and blocks; do not announce it or list controls.
5. (Case B) AFTER it returns, give a DIRECT, COMPACT summary of what the user KEPT
   (read `selected`, ignore `skipped`): group by theme, ONE short line per layer —
   `source · what it is` (+ feature count if fetched). End with ONE line inviting a
   request for more detail on a specific layer. If cancelled, say so in one line.
6. If the user THEN asks about a specific layer, give the deeper detail then (what it
   contains, key fields, resolution, relevance; call `list_layers`/`preview` if it
   helps). Do not dump that detail for every layer up front.

Config-only shortcut: if the user EXPLICITLY asks just for the catalog.yml or the
add/fetch commands (no exploration), skip the pickers and call `make_catalog_entry`
with the real layer name(s); commands are shown automatically — name the
provider/layer briefly (2-4 sentences).

If no registry source fits, use a META-CATALOGUE (type=csw): `discover` to find a
service, `list_layers` on its `endpoint`+`service_type` for the REAL layer name,
then feed it to `propose_aoi`/`propose_layers` (candidates with endpoint+
service_type) or to `make_catalog_entry`. (`discover` on `gee` returns Earth
Engine dataset ids for the earthengine-api instead — never catalog.yml material.)

Bounding boxes are ALWAYS EPSG:4326 (longitude, latitude in decimal degrees) —
never projected/Lambert metres. Get them from `propose_aoi`/`geocode_place`, not from memory.

Be concise. Cite source ids in backticks.

Source registry (id · name · type · coverage · themes · auth · status):
"""


def _registry_digest(sources: list[Source]) -> str:
    lines = []
    for s in sources:
        st = (s.status or {}).get("state", "?")
        tag = " · free-productType" if (s.catalog.get("queryParam") or s.catalog.get("free_product")) else ""
        lines.append(f"- {s.id} · {s.name} · {s.type} · {s.coverage} · "
                     f"{','.join(s.themes)} · auth={s.auth} · {st}{tag}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tools (provider-neutral) + dispatch
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "name": "geocode_place",
        "description": "Resolve a named place (city, river, department, region, country…) to a "
                       "real area of interest in EPSG:4326 by querying OpenStreetMap Nominatim. "
                       "ALWAYS call this FIRST when the user names a geographic area — never invent "
                       "coordinates. Returns display_name, bbox (lon_min,lat_min,lon_max,lat_max), "
                       "has_polygon and the recommended coverage labels for that country. The "
                       "resolved AOI (a polygon when available) is remembered and reused for fetches.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "place name, e.g. 'Lys river, France'"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "propose_aoi",
        "description": "Resolve AND confirm the area of interest INTERACTIVELY — the crucial first "
                       "step whenever the user names an area. Provide several candidates and the user "
                       "previews/picks ONE. Three candidate kinds: (1) kind='place' → a Nominatim "
                       "lookup (towns, departments, regions, countries; rivers as lines). Note: "
                       "Nominatim returns only a POINT for seas/oceans/large diffuse areas — do NOT "
                       "rely on it for those. (2) kind='feature' → the real geometry of a NAMED "
                       "feature from a data layer (e.g. a watershed/'bassin versant' polygon, a river "
                       "line, or a marine area from SANDRE/IGN/OSM/Marine Regions); pass the layer "
                       "(`source_id` OR `endpoint`+`service_type`), `name` (feature name to match, "
                       "e.g. 'Authie'), and `place_hint` (a place to scope the region) or `search_bbox`. "
                       "The `layer` MUST come from a prior list_layers/discover call on a VECTOR "
                       "(WFS/STAC) service — NEVER invent it. A small spelling/namespace drift in a WFS "
                       "typename is tolerated (auto-matched), but an unknown layer or a raster "
                       "(WMS/WMTS/WCS) source is REJECTED and you must list_layers and retry. "
                       "(3) kind='bbox' → an explicit `bbox`='lon_min,lat_min,lon_max,lat_max' from "
                       "your geographic knowledge; LAST RESORT only, when no polygon source exists. "
                       "For a SEA/OCEAN use kind='feature' on a marine-areas layer to get the REAL "
                       "polygon — e.g. `source_id`='marine_regions', layer='MarineRegions:goas' (or "
                       "'MarineRegions:iho'), name='Mediterranean'. For an EEZ (zone économique "
                       "exclusive) use layer='MarineRegions:eez' with name=the country/territory "
                       "(e.g. 'France') — no place_hint needed; named features are searched worldwide "
                       "and, if several match (France = metropole + overseas), each is offered "
                       "separately plus a union. Prefer kind='feature' for rivers/watersheds/lakes/"
                       "seas/EEZ; kind='bbox' only if truly nothing else works. "
                       "Lines (rivers) are buffered into a polygon (user sets the size). The chosen "
                       "AOI is remembered; thematic layers are clipped to it by default (user can "
                       "disable); an AOI that IS the requested feature is not cropped. Always offer "
                       "2-3 candidates so the user can choose the right source. Call ALONE, NO preamble.",
        "input_schema": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "description": "AOI candidates to resolve and offer to the user",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "description": "'place' or 'feature'"},
                            "query": {"type": "string", "description": "place name (kind=place)"},
                            "source_id": {"type": "string", "description": "registry source id (kind=feature)"},
                            "endpoint": {"type": "string", "description": "service URL (kind=feature, with service_type)"},
                            "service_type": {"type": "string", "description": "wfs|wms|wcs|wmts|stac (kind=feature)"},
                            "layer": {"type": "string", "description": "exact layer id (kind=feature)"},
                            "name": {"type": "string", "description": "feature name to match (kind=feature)"},
                            "place_hint": {"type": "string", "description": "place to scope the feature "
                                           "search to the right region (kind=feature)"},
                            "search_bbox": {"type": "string", "description": "OPTIONAL "
                                            "'lon_min,lat_min,lon_max,lat_max' search extent (kind=feature)"},
                            "label": {"type": "string", "description": "optional human label"},
                            "buffer_m": {"type": "number", "description": "default buffer in metres for "
                                         "line/point geometries (user can change it)"},
                        },
                    },
                },
            },
            "required": ["candidates"],
        },
    },
    {
        "name": "propose_layers",
        "description": "Present a grouped, candidate layer list to the user for INTERACTIVE selection "
                       "in the terminal. Call this AFTER geocode_place has resolved the area and AFTER "
                       "you've gathered DIVERSE candidates across several themes (via list_layers / "
                       "discover), keeping only sources whose coverage includes the AOI. The user "
                       "toggles which layers to keep, can view details, and can fetch a layer on the "
                       "AOI (feature counts), visualise it, export it or get its catalog.yml. Returns "
                       "the user's final selection plus per-layer fetch results. Afterwards, write a "
                       "short thematic briefing of the chosen layers. Call this tool ALONE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "groups": {
                    "type": "array",
                    "description": "candidate layers grouped by theme",
                    "items": {
                        "type": "object",
                        "properties": {
                            "theme": {"type": "string", "description": "theme label, e.g. 'hydrology'"},
                            "layers": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "source_id": {"type": "string", "description": "registry source id"},
                                        "endpoint": {"type": "string", "description": "service URL "
                                                     "(ad-hoc service, use with service_type)"},
                                        "service_type": {"type": "string", "description": "wfs|wms|wcs|"
                                                         "wmts|stac (with endpoint)"},
                                        "layer": {"type": "string", "description": "exact layer id "
                                                  "from list_layers"},
                                        "title": {"type": "string", "description": "human label"},
                                        "why": {"type": "string", "description": "one-line relevance note"},
                                    },
                                    "required": ["layer"],
                                },
                            },
                        },
                        "required": ["theme", "layers"],
                    },
                },
                "bbox": {"type": "string", "description": "OPTIONAL 'lon_min,lat_min,lon_max,lat_max' "
                         "in EPSG:4326; omit to use the AOI resolved by geocode_place"},
                "datetime": {"type": "string", "description": "OPTIONAL 'YYYY-MM-DD/YYYY-MM-DD'"},
            },
            "required": ["groups"],
        },
    },
    {
        "name": "list_layers",
        "description": "List the real layers/collections/typenames of a service by querying "
                       "its server. EITHER pass `source_id` (a registry source) OR, to inspect "
                       "a service URL found via `discover`, pass `endpoint` + `service_type`. "
                       "Use it to get the EXACT layer name — never invent one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "a registry source id"},
                "endpoint": {"type": "string", "description": "service URL (use with service_type, "
                             "for a discovered service not in the registry)"},
                "service_type": {"type": "string", "description": "wfs|wms|wcs|wmts|stac when "
                                 "using endpoint"},
                "grep": {"type": "string", "description": "optional case-insensitive filter; "
                         "several terms separated by '|' or spaces match ANY of them "
                         "(e.g. 'piste|route|dfci')"},
            },
        },
    },
    {
        "name": "discover",
        "description": "Keyword-search a meta-catalogue: a CSW service (sources tagged "
                       "type=csw, e.g. `eea_sdi`, `sextant`) for WMS/WFS/STAC services to "
                       "add, or the Google Earth Engine data catalog (`gee`) for Earth "
                       "Engine dataset ids (used with the earthengine-api, NOT addable to "
                       "catalog.yml). Use it when no direct source fits. Returns records "
                       "with their service URLs (CSW) or dataset ids + doc pages (gee).",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "a csw-type source id, or `gee`"},
                "query": {"type": "string", "description": "keywords, e.g. 'land cover'"},
            },
            "required": ["source_id", "query"],
        },
    },
    {
        "name": "make_catalog_entry",
        "description": "Generate the exact, ready-to-use catalog.yml block and CLI commands. "
                       "Always call this instead of writing YAML by hand. EITHER pass "
                       "`source_id` (registry source) OR, for a service found via `discover`, "
                       "pass `provider_name` + `service_type` + `endpoint`. `layers` must be the "
                       "REAL layer names from list_layers — never invented.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "a registry source id"},
                "provider_name": {"type": "string", "description": "a short name for a new "
                                  "provider (ad-hoc service), e.g. 'herault_dfci'"},
                "service_type": {"type": "string", "description": "WFS|WMS|WMTS|WCS|STAC|REST "
                                 "(with provider_name + endpoint)"},
                "endpoint": {"type": "string", "description": "service URL (with provider_name)"},
                "layers": {"type": "array", "items": {"type": "string"},
                           "description": "exact layer/typename ids from list_layers"},
                "bbox": {"type": "string", "description": "OPTIONAL area of interest as "
                         "'lon_min,lat_min,lon_max,lat_max' in EPSG:4326 (decimal degrees). "
                         "Fill it when the user names a place so the fetch command is "
                         "copy-paste-ready (e.g. Hérault → '2.5,43.2,4.2,44.0')."},
                "datetime": {"type": "string", "description": "OPTIONAL time window "
                             "'YYYY-MM-DD/YYYY-MM-DD' (skip for static/vector layers)"},
            },
            "required": ["layers"],
        },
    },
]


def _format_geocode(results: list[dict]) -> tuple[dict | None, dict]:
    """Split a geocode_place result into (aoi_for_state, slim_for_model).

    The slim dict goes into the model's context (no bulky geometry — just the
    bbox + a ``has_polygon`` flag); the aoi dict (full geometry) is kept by the
    assistant to drive fetches.
    """
    from .geocode import recommended_coverages

    if not results:
        return None, {"found": False}
    top = results[0]
    geom = top.get("geometry")
    aoi = {"display_name": top.get("display_name"), "geometry": geom,
           "bbox": top.get("bbox"), "country_code": top.get("country_code")}
    slim = {
        "found": True,
        "display_name": top.get("display_name"),
        "bbox": list(top["bbox"]) if top.get("bbox") else None,
        "has_polygon": bool(geom) and geom.get("type") in ("Polygon", "MultiPolygon"),
        "country": top.get("country_code"),
        "recommended_coverage": sorted(recommended_coverages(top.get("country_code"), top.get("bbox"))),
    }
    return aoi, slim


def _dispatch(name: str, args: dict, sources: list[Source]) -> str:
    """Execute a tool call, returning a string result for the model."""
    try:
        if name == "geocode_place":
            from .geocode import geocode_place
            results = geocode_place(args["query"], limit=args.get("limit", 1))
            _, slim = _format_geocode(results)
            return json.dumps(slim, ensure_ascii=False)
        if name == "list_layers":
            from .inspect import list_layers, list_layers_from

            def slim(layers: list[dict], cap: int = 50) -> list[dict]:
                # Compact view for the MODEL only: full listings (OGC abstracts,
                # keywords, bbox, legend URLs; STAC descriptions) balloon the
                # conversation — ~70k prompt tokens over one 4-turn session. It
                # only needs the exact id, enough title to judge relevance, and
                # the time-dimension flag.
                out = []
                for l in layers[:cap]:
                    e = {"id": l["id"], "title": (l.get("title") or "")[:100]}
                    if l.get("sends_datetime"):
                        e["time_series"] = True
                    out.append(e)
                return out

            grep = args.get("grep")
            src = None
            if args.get("endpoint") and args.get("service_type"):
                _list = lambda g=grep: list_layers_from(args["service_type"], args["endpoint"], g)
                where = args["endpoint"]
            else:
                src = get_source(sources, args["source_id"])
                _list = lambda g=grep: list_layers(src, grep=g)
                where = src.id
            layers = _list()
            result = {
                "source": where,
                "count": len(layers),
                "layers": slim(layers),
                "truncated": len(layers) > 50,
            }
            # When a filter matches nothing, show real names so the model can
            # recalibrate — many services use terse codes (EFFIS: fwi, hs, ba...).
            if grep and not layers:
                unfiltered = _list(None)
                if unfiltered:
                    result["note"] = (
                        f"No layer name/title contains {grep!r} — this service uses terse "
                        f"codes, not plain words. Pick from sample_layers and retry "
                        f"list_layers with a code you see (or omit grep)."
                    )
                    result["sample_layers"] = slim(unfiltered, 40)
                    result["total_available"] = len(unfiltered)
            if src and src.catalog.get("queryParam"):
                result["free_productType"] = True
                result["note"] = (
                    f"This source takes a free-form productType passed as the "
                    f"'{src.catalog['queryParam']}' query parameter (e.g. a scientific "
                    f"name). Do NOT search the species in the layer list — pass the value "
                    f"directly to make_catalog_entry. The routes below are just endpoints."
                )
            elif src and src.catalog.get("free_product"):
                result["free_productType"] = True
                hint = src.catalog["free_product"]
                detail = hint if isinstance(hint, str) else "any supported value"
                result["note"] = (
                    f"This source takes a free-form productType: {detail}. The listed "
                    f"layers are just EXAMPLES — pass the exact value the user asked for "
                    f"(e.g. 'MSI') directly to propose_layers/make_catalog_entry, even if "
                    f"it is not listed."
                )
            return json.dumps(result, ensure_ascii=False)
        if name == "discover":
            from .discover import discover
            src = get_source(sources, args["source_id"])
            res = discover(src, args["query"], limit=args.get("limit", 25))
            # actionable records only: a usable service URL (CSW), or an identifier
            # + doc page (dataset lists like `gee`, where the id IS the handle)
            slim = []
            for r in res["records"]:
                services = [u for u in r["urls"] if u["service"]][:3]
                if services:
                    slim.append({"title": r["title"], "type": r["type"], "services": services})
                elif r.get("page"):
                    slim.append({"id": r["identifier"], "title": r["title"],
                                 "type": r["type"], "page": r["page"]})
            return json.dumps({"matches": res["matches"], "records": slim}, ensure_ascii=False)
        if name == "make_catalog_entry":
            import yaml

            from .inspect import _slug, to_catalog_provider, to_catalog_provider_raw
            layers = args["layers"]
            quoted = " ".join(f'"{l}"' for l in layers)

            if args.get("source_id"):  # registry source
                src = get_source(sources, args["source_id"])
                provider = src.id
                block = to_catalog_provider(src, layers, resolve_descriptions=True)
                add_command = f"geodatacatalog add {provider} {quoted} -o catalog.yml"
            else:                       # ad-hoc service found via discover
                provider = args["provider_name"]
                stype = args["service_type"]
                block = to_catalog_provider_raw(provider, stype, args["endpoint"], layers)
                add_command = (f"geodatacatalog add {provider} {quoted} "
                               f"--type {stype.upper()} --endpoint {args['endpoint']} -o catalog.yml")

            layer_ids = [f"{provider}__{_slug(l)}" for l in layers]
            l_flags = " ".join(f"-l {lid}" for lid in layer_ids)

            # If the model knows the AOI, fill a real EPSG:4326 bbox so the commands
            # run as-is; otherwise leave a clearly-named lon/lat placeholder.
            bbox = (args.get("bbox") or "").strip()
            dt = (args.get("datetime") or "").strip()
            bbox_cli = bbox or "<lon_min,lat_min,lon_max,lat_max>"
            # `--bbox=...` (not `--bbox ...`): a bbox starting with '-' (e.g. France
            # -5.14,...) is otherwise parsed by argparse as an option, not a value.
            fetch_command = f"geodatacatalog fetch catalog.yml {l_flags} --bbox={bbox_cli}"
            if dt:
                fetch_command += f" --datetime {dt}"
            fetch_command += " -o out --explore map.html --open"

            bbox_py = (f"({', '.join(p.strip() for p in bbox.split(','))})"
                       if bbox else "(lon_min, lat_min, lon_max, lat_max)")
            dt_py = f',\n               datetime="{dt}"' if dt else ""
            return json.dumps({
                "catalog_yaml": yaml.safe_dump(block, sort_keys=False, allow_unicode=True),
                "add_command": add_command,
                "fetch_command": fetch_command,
                "python_snippet": (
                    "from geodatacatalog import load_catalog, GeoRequest\n"
                    "catalog = load_catalog('catalog.yml')\n"
                    "# bbox is ALWAYS EPSG:4326 (longitude, latitude)\n"
                    f"results = catalog.fetch(\n"
                    f"    GeoRequest(bbox={bbox_py}{dt_py}),\n"
                    f"    layers={layer_ids})\n"
                    "results.explore()"
                ),
            }, ensure_ascii=False)
        return f"error: unknown tool {name!r}"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


def _result_summary(name: str, result: str) -> str:
    """One-line outcome of a tool call, for the live activity log.

    These lines are progress notes, not warnings — a source returning no layer
    match just means it isn't browsed that way (e.g. queried by species name).
    """
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        return result  # _dispatch errors are plain strings ("error: ...")
    if name == "geocode_place":
        if not data.get("found"):
            return "no place matched — ask the user to refine the name"
        kind = "polygon AOI" if data.get("has_polygon") else "bbox"
        return f"✓ resolved {data.get('display_name')} ({kind})"
    if name == "propose_aoi":
        if data.get("cancelled"):
            return "AOI selection cancelled by the user"
        if data.get("error"):
            return data["error"]
        if "resolved" in data:  # headless: no picker, just resolved candidates
            return f"✓ {data['resolved']} AOI candidate(s) resolved"
        clip = "clip on" if data.get("clip_layers") else "clip off"
        return f"✓ AOI = {data.get('chosen')} ({clip})"
    if name == "propose_layers":
        if data.get("cancelled"):
            return "selection cancelled by the user"
        if data.get("error"):
            return data["error"]
        if "proposed" in data:  # headless: no picker, just proposed layers
            fetched = " (fetched)" if data.get("fetched") else ""
            return f"✓ {data['proposed']} layer(s) proposed{fetched}"
        selected = data.get("selected", [])
        fetched = sum(1 for s in selected if s.get("fetched"))
        return f"✓ {len(selected)} layer(s) selected, {fetched} fetched"
    if name == "list_layers":
        if data.get("free_productType"):  # e.g. GBIF
            return "ok — free-form productType: pass the value directly (e.g. a species name or spectral index)"
        n = data.get("count", 0)
        if not n:
            total = data.get("total_available")
            if total:
                return f"no match — but {total} layers exist (terse codes); recalibrating"
            return "no layer matched the filter (this source may be queried differently)"
        return f"✓ {n} layer(s)" + (" (showing first 50)" if data.get("truncated") else "")
    if name == "discover":
        n = data.get("matches")
        kept = len(data.get("records", []))
        return f"✓ {n} match(es), {kept} with a service URL" if kept else "no service-bearing record"
    if name == "make_catalog_entry":
        return "✓ catalog.yml block generated"
    return "✓ done"


def _trace_args(name: str, args: dict) -> dict:
    """Slim a tool call's arguments for the client-facing action trace.

    The two terminal tools (propose_aoi/propose_layers) carry the whole proposal
    in their arguments, already returned in full as the result — echoing them
    again here would double the payload for no benefit, so only counts are kept.
    """
    if name == "propose_layers":
        groups = args.get("groups") or []
        return {"themes": [g.get("theme") for g in groups],
                "layers": sum(len(g.get("layers", [])) for g in groups)}
    if name == "propose_aoi":
        return {"candidates": len(args.get("candidates") or [])}
    return args


def _searched_not_retained(actions: list[dict], kept: set[str]) -> list[str]:
    """Sources the model queried (list_layers/discover) but did not propose from."""
    out: list[str] = []
    for act in actions:
        if act.get("tool") not in ("list_layers", "discover"):
            continue
        args = act.get("args") or {}
        sid = args.get("source_id") or args.get("endpoint")
        if sid and sid not in kept and sid not in out:
            out.append(sid)
    return out


def _summarize_headless(terminal: str, result: dict) -> str:
    """A natural-language recap of a headless propose_* result.

    Synthesized from the structured result rather than a further model call —
    the backend system prompts forbid prose so the tool loop stays short and
    reliable. Multi-line: one bullet per source carrying the model's rationale
    (``why``), coverage and caveats, plus the sources searched but not retained
    (from the ``actions`` trace), so the recap explains the proposal instead of
    just counting it.
    """
    if terminal == "propose_layers":
        layers = result.get("layers", [])
        if not layers:
            return "No layer matched this request — try rephrasing or widening the area."
        aoi = (result.get("aoi") or {}).get("display_name") or "the selected area"
        by_source: dict[str, list[dict]] = {}
        for l in layers:
            by_source.setdefault(l.get("source_id") or l.get("endpoint") or "?", []).append(l)
        head = f"{len(layers)} layer(s) proposed over {aoi}, from {len(by_source)} source(s)"
        fetched = sum(1 for l in layers if l.get("fetched"))
        if fetched:
            head += f"; {fetched} fetched on the AOI"
        if result.get("partial"):
            head += " (stopped early — tool-call budget reached; refine to continue)"
        lines = [head + "."]
        for sid, group in by_source.items():
            names = list(dict.fromkeys(
                (l.get("title") or l.get("layer") or "?") for l in group))
            shown = ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
            line = f"• {sid} — {shown}"
            why = next((l.get("why") for l in group if l.get("why")), "")
            if why:
                line += f": {why}"
            if not line.endswith((".", "!", "?")):
                line += "."
            caveats = []
            cov = next((l.get("coverage") for l in group if l.get("coverage")), None)
            if cov:
                caveats.append(f"coverage: {cov}")
            if any(l.get("in_aoi") is False for l in group):
                caveats.append("outside the AOI")
            if any(l.get("needs_auth") for l in group):
                caveats.append("credentials required")
            counts = [l.get("feature_count") for l in group if l.get("fetched")]
            got = [c for c in counts if c]
            if got:
                caveats.append(f"{sum(got)} feature(s) over the AOI")
            if any(c == 0 for c in counts):
                caveats.append("empty over this AOI")
            err = next((l.get("error") for l in group if l.get("error")), None)
            if err:
                caveats.append(f"fetch failed: {err}")
            if caveats:
                line += " (" + "; ".join(caveats) + ")"
            lines.append(line)
        searched = _searched_not_retained(result.get("actions") or [], set(by_source))
        if searched:
            lines.append("Also searched, nothing retained: " + ", ".join(searched) + ".")
        return "\n".join(lines)
    if terminal == "propose_aoi":
        feats = result.get("features", [])
        if not feats:
            return "No AOI candidate could be resolved — try rephrasing the area."
        names = ", ".join((f.get("properties") or {}).get("display_name") or "?" for f in feats[:5])
        return f"Found {len(feats)} candidate area(s): {names}."
    return ""


# ---------------------------------------------------------------------------
# Provider availability
# ---------------------------------------------------------------------------

def pick_provider() -> tuple[str | None, str | None]:
    """Return (provider, reason_if_unavailable). Mistral wins when both keys are set."""
    if os.environ.get("MISTRAL_API_KEY"):
        return "mistral", None
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        try:
            import anthropic  # noqa: F401
            return "anthropic", None
        except ImportError:
            return None, ("ANTHROPIC_API_KEY is set but the `anthropic` package is missing — "
                          "pip install 'geodatacatalog[ai]'")
    return None, ("no API key found — set MISTRAL_API_KEY (free tier: https://console.mistral.ai) "
                  "or ANTHROPIC_API_KEY, or use the offline `geodatacatalog search`")


def ensure_available() -> str | None:
    return pick_provider()[1]


#: Soft theme — inline code and code blocks use colour only, NO background box
#: (rich's defaults paint a dark box that looks harsh on a dark terminal).
_MD_THEME = {
    "markdown.code": "cyan",                 # `inline code` → colour, no bgcolor
    "markdown.link": "blue",
    "markdown.link_url": "dim blue underline",
    "markdown.h1": "bold magenta",
    "markdown.h2": "bold magenta",
    "markdown.h3": "bold cyan",
    "markdown.h4": "bold",
    "markdown.item.bullet": "yellow",
    "markdown.block_quote": "dim italic",
}


def _render(text: str) -> None:
    """Pretty-print the assistant's markdown answer in the terminal.

    Uses ``rich`` with a soft theme when available (no background boxes on code);
    falls back to plain text otherwise (``pip install rich`` or the [ai] extra).
    """
    if not text or not text.strip():
        return
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.theme import Theme

        console = Console(theme=Theme(_MD_THEME, inherit=True))
        # code_theme="ansi_dark" → fenced blocks use terminal ANSI colours on the
        # default (transparent) background instead of monokai's grey box.
        console.print(Markdown(text, code_theme="ansi_dark"))
    except ImportError:
        print(text)


def _activity(line: str) -> None:
    """Print a tool-activity line as dim secondary output (rich if available)."""
    try:
        from rich.console import Console
        Console().print(line, style="dim", highlight=False)
    except ImportError:
        print(line)


def _print_question(text: str) -> None:
    """Echo the question being sent to the model, so it stays visible above the
    tool-call activity/log lines that follow (and in one-shot/`-q` invocations,
    where it otherwise never appears on screen at all)."""
    try:
        from rich.console import Console
        from rich.rule import Rule
        Console().print(Rule(f"[bold]› {text}[/]", style="cyan", align="left"))
    except ImportError:
        print(f"\n› {text}")


def _thinking():
    """Context manager showing that the model call is in flight (blocking).

    Uses a ``rich`` spinner when available; falls back to a static line that
    is cleared once the call returns.
    """
    try:
        from rich.console import Console
        return Console().status("[dim]\U0001f9e0 réflexion…[/]", spinner="dots")
    except ImportError:
        from contextlib import contextmanager

        @contextmanager
        def _plain():
            print("\U0001f9e0 réflexion…", end="", flush=True)
            try:
                yield
            finally:
                print("\r" + " " * 20 + "\r", end="", flush=True)
        return _plain()


def _print_answer_header() -> None:
    """Rule separating the model's final answer from the tool-activity trail
    above it, so a long turn (several tool calls) doesn't bury the response."""
    try:
        from rich.console import Console
        from rich.rule import Rule
        Console().print(Rule("\U0001f916", style="dim", align="left"))
    except ImportError:
        print("—")


def _candidate_label(c: dict) -> str:
    """Short human label for one propose_aoi candidate dict."""
    return (c.get("query") or c.get("name") or c.get("label")
            or c.get("source_id") or c.get("layer") or "?")


def _fmt_call(name: str, args: dict) -> str:
    """One-line, human-readable description of the tool the model is about to
    run — shown to the user as live progress. Raw calls like
    ``propose_aoi(candidates=[{'kind': 'place', 'query': 'Toulouse'}])`` mean
    nothing to a non-technical user, so each tool gets a plain-English sentence
    instead; unrecognised tools fall back to the raw ``name(args)`` form.
    """
    if name == "geocode_place":
        return f"  · looking up place: {args.get('query', '?')}"
    if name == "propose_aoi":
        cands = args.get("candidates") or []
        labels = [_candidate_label(c) for c in cands]
        joined = ", ".join(labels[:3]) + ("…" if len(labels) > 3 else "")
        return f"  · proposing area of interest: {joined or '?'}"
    if name == "propose_layers":
        groups = args.get("groups") or []
        n = sum(len(g.get("layers", [])) for g in groups)
        themes = ", ".join(g["theme"] for g in groups if g.get("theme"))
        return f"  · proposing {n} layer(s)" + (f" ({themes})" if themes else "")
    if name == "list_layers":
        target = args.get("source_id") or args.get("endpoint") or "?"
        grep = f" matching '{args['grep']}'" if args.get("grep") else ""
        return f"  · browsing layers on {target}{grep}"
    if name == "discover":
        return f"  · searching '{args.get('query', '?')}' in the {args.get('source_id', '?')} catalogue"
    if name == "make_catalog_entry":
        target = args.get("source_id") or args.get("provider_name") or "?"
        return f"  · generating a catalog.yml entry for {target}"

    def short(v):
        s = str(v)
        return repr(s if len(s) <= 60 else s[:57] + "…")
    return f"  · {name}(" + ", ".join(f"{k}={short(v)}" for k, v in args.items()) + ")"


def _print_ready(entry: dict) -> None:
    """Print the copy-paste commands from a make_catalog_entry result.

    Printed by us (not via the model's markdown) with soft wrapping so a long
    command stays a SINGLE line — copy-paste keeps it intact even when the
    terminal visually wraps it.
    """
    items = [("add it to your catalog.yml", entry.get("add_command")),
             ("then fetch it (set your bbox / dates)", entry.get("fetch_command"))]
    items = [(label, cmd) for label, cmd in items if cmd]
    if not items:
        return
    try:
        from rich.console import Console
        from rich.rule import Rule
        c = Console()
        c.print(Rule("📋 ready to use", style="dim", align="left"))
        for label, cmd in items:
            c.print(f"# {label}", style="dim italic")
            c.print(cmd, style="bold cyan", soft_wrap=True)   # soft_wrap → one copyable line
        c.print()
    except ImportError:
        print("\n── ready to use ──")
        for label, cmd in items:
            print(f"# {label}\n{cmd}")
        print()


# ---------------------------------------------------------------------------
# Interactive layer selection (agenticearth-style) + fetch on the AOI
# ---------------------------------------------------------------------------

def _parse_bbox(s) -> tuple[float, float, float, float] | None:
    if not s:
        return None
    try:
        parts = [float(x) for x in str(s).split(",")]
    except ValueError:
        return None
    return (parts[0], parts[1], parts[2], parts[3]) if len(parts) == 4 else None


def _bbox_str(bbox) -> str:
    return ",".join(str(x) for x in bbox) if bbox else "<lon_min,lat_min,lon_max,lat_max>"


def _bbox_area(bbox) -> float:
    return abs((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) if bbox else 0.0


def _aoi_request(aoi_geom: dict | None, bbox, datetime, clip: bool = True):
    """Build a GeoRequest from the AOI.

    With ``clip`` and a polygon AOI, the fetch is clipped to it (catalog.fetch
    clips on ``aoi_gdf``). With ``clip=False`` (or no polygon) it fetches by the
    bounding box only and returns whole features — used when the AOI is the
    requested feature itself, or the user disabled clipping.
    """
    from ..request import GeoRequest
    if clip and aoi_geom and aoi_geom.get("type") in ("Polygon", "MultiPolygon"):
        try:
            import geopandas as gpd
            from shapely.geometry import shape
            gdf = gpd.GeoDataFrame(geometry=[shape(aoi_geom)], crs="EPSG:4326")
            return GeoRequest(aoi_gdf=gdf, datetime=datetime)
        except Exception:
            pass
    return GeoRequest(bbox=tuple(bbox), datetime=datetime)


def _build_raw_layer(service_type: str, endpoint: str, layer: str, lid: str = "raw__sel"):
    """Build a layer object for an ad-hoc service (no registry entry)."""
    from ..layers import STACLayer, WCSLayer, WFSLayer, WMSLayer, WMTSLayer
    st = (service_type or "").lower()
    if st == "stac":
        return STACLayer(layer_id=lid, url=endpoint, collection=layer)
    if st == "wfs":
        return WFSLayer(layer_id=lid, url=endpoint, typename=layer)
    if st == "wms":
        return WMSLayer(layer_id=lid, url=endpoint, layer_name=layer)
    if st == "wcs":
        return WCSLayer(layer_id=lid, url=endpoint, coverage_id=layer)
    if st == "wmts":
        return WMTSLayer(layer_id=lid, url=endpoint, layer_name=layer)
    raise ValueError(f"unsupported service type {service_type!r} (stac|wfs|wms|wcs|wmts)")


def _layer_object(candidate: dict, sources: list[Source], lid: str):
    """Build the geodatacatalog layer object for a picker candidate."""
    if candidate.get("source_id"):
        from .inspect import _build_layer
        obj = _build_layer(get_source(sources, candidate["source_id"]), candidate["layer"])
    elif candidate.get("endpoint") and candidate.get("service_type"):
        obj = _build_raw_layer(candidate["service_type"], candidate["endpoint"], candidate["layer"])
    else:
        raise ValueError("candidate has neither source_id nor endpoint+service_type")
    obj.layer_id = lid
    return obj


def _fetch_on_aoi(candidates: list[dict], sources: list[Source],
                  aoi_geom: dict | None, bbox, datetime, clip: bool = True,
                  export_dir: str | None = None) -> dict:
    """Fetch a batch of candidate layers on the AOI in one throwaway catalog.

    Returns ``{id(candidate): {"count": int|None, "error": str|None}}``. Layers
    that fail to build are recorded individually; failures during the fetch are
    isolated by ``catalog.fetch`` (the layer comes back as ``None``). When
    ``export_dir`` is set, the fetched results are written there.
    """
    import tempfile

    from ..catalog import GeoDataCatalog

    out: dict = {}
    layers, mapping = [], {}
    for i, c in enumerate(candidates):
        try:
            obj = _layer_object(c, sources, lid=f"sel{i}")
        except Exception as e:
            out[id(c)] = {"count": None, "error": f"{type(e).__name__}: {e}"}
            continue
        layers.append(obj)
        mapping[obj.layer_id] = c
    if not layers:
        return out

    catalog = GeoDataCatalog(layers=layers, cache_dir=tempfile.mkdtemp(prefix="gdc_chat_"))
    try:
        results = catalog.fetch(_aoi_request(aoi_geom, bbox, datetime, clip), no_cache=True)
    except Exception as e:
        for c in mapping.values():
            out[id(c)] = {"count": None, "error": f"{type(e).__name__}: {e}"}
        return out

    for lid, c in mapping.items():
        data = results.get(lid)
        try:
            count = len(data) if data is not None else 0
        except TypeError:
            count = None
        out[id(c)] = {"count": count, "error": None}
        label = c.get("title") or c.get("layer")
        if count:
            _activity(f"    ✓ {label}: {count} feature(s)")
        elif count == 0:
            _activity(f"    · {label}: 0 feature")
        else:
            _activity(f"    ? {label}: fetched")

    if export_dir:
        try:
            results.export(Path(export_dir))
            _activity(f"    ✓ exported to {export_dir}/")
        except Exception as e:
            _activity(f"    ✗ export failed: {type(e).__name__}: {e}")
    return out


def _visualise_item(c: dict, sources: list[Source], aoi_geom, bbox, datetime, clip: bool = True) -> None:
    """Fetch a single layer on the AOI and save/open a Folium map."""
    import tempfile

    from ..catalog import GeoDataCatalog
    try:
        obj = _layer_object(c, sources, lid="viz")
        catalog = GeoDataCatalog(layers=[obj], cache_dir=tempfile.mkdtemp(prefix="gdc_chat_"))
        results = catalog.fetch(_aoi_request(aoi_geom, bbox, datetime, clip), no_cache=True)
        if results.get(obj.layer_id) is None:
            _activity("  ✗ fetch failed for this layer — nothing to preview (see the error above)")
            return
        m = results.explore()
        if m is None:
            _activity("  · nothing to show on a map for this layer")
            return
        out = Path(f"{_safe_name(c['layer'])}_map.html")
        m.save(str(out))
        _activity(f"  ✓ map saved: {out}")
        _open_in_browser(out)
    except Exception as e:
        _activity(f"  ✗ map failed: {type(e).__name__}: {e}")


def _export_item(c: dict, sources: list[Source], aoi_geom, bbox, datetime, clip: bool = True) -> None:
    """Fetch a single layer on the AOI and export it to ./out."""
    import tempfile

    from ..catalog import GeoDataCatalog
    try:
        obj = _layer_object(c, sources, lid="exp")
        catalog = GeoDataCatalog(layers=[obj], cache_dir=tempfile.mkdtemp(prefix="gdc_chat_"))
        results = catalog.fetch(_aoi_request(aoi_geom, bbox, datetime, clip), no_cache=True)
        if results.get(obj.layer_id) is None:
            _activity("  ✗ fetch failed for this layer — nothing to export (see the error above)")
            return
        results.export(Path("out"))
        _activity("  ✓ exported to out/")
    except Exception as e:
        _activity(f"  ✗ export failed: {type(e).__name__}: {e}")


def _catalog_entry_for(c: dict, sources: list[Source], bbox, datetime) -> dict | None:
    """Build a make_catalog_entry result (YAML + commands) for one candidate."""
    args: dict = {"layers": [c["layer"]]}
    if c.get("source_id"):
        args["source_id"] = c["source_id"]
    elif c.get("endpoint") and c.get("service_type"):
        args["provider_name"] = _safe_name(c.get("title") or c["layer"])
        args["service_type"] = c["service_type"]
        args["endpoint"] = c["endpoint"]
    else:
        return None
    if bbox:
        args["bbox"] = _bbox_str(bbox)
    if datetime:
        args["datetime"] = datetime
    out = _dispatch("make_catalog_entry", args, sources)
    try:
        return json.loads(out)
    except ValueError:
        _activity(f"  ✗ {out}")
        return None


def _info_line(c: dict, sources: list[Source]) -> str:
    bits = [f"  {c.get('title') or c['layer']}", f"    layer: {c['layer']}"]
    if c.get("why"):
        bits.append(f"    why: {c['why']}")
    if c.get("source_id"):
        try:
            src = get_source(sources, c["source_id"])
            bits.append(f"    source: {src.name} · {src.type} · coverage={src.coverage} "
                        f"· themes={','.join(src.themes)}")
            bits.append(f"    endpoint: {src.endpoint}")
        except KeyError:
            pass
    elif c.get("endpoint"):
        bits.append(f"    service: {c.get('service_type')} · {c['endpoint']}")
    if c.get("fetched"):
        fc = c.get("feature_count")
        bits.append(f"    fetched: {fc} feature(s)" if fc is not None
                    else f"    fetched: {c.get('error') or 'no data'}")
    if not c.get("in_aoi", True):
        bits.append(f"    ⚠ coverage {c.get('coverage')!r} is OUTSIDE the requested AOI")
    return "\n".join(bits)


def _safe_name(name: str) -> str:
    from .inspect import _slug
    return _slug(name)


def _open_in_browser(path) -> None:
    try:
        import webbrowser
        webbrowser.open(Path(path).resolve().as_uri())
    except Exception:
        pass


def _select_indices(candidates: list[dict], rest: str) -> list[dict]:
    items = []
    for tok in re.split(r"[\s,]+", rest.strip()):
        if tok.isdigit():
            idx = int(tok) - 1
            if 0 <= idx < len(candidates):
                items.append(candidates[idx])
    return items


def _is_indices(s: str) -> bool:
    toks = [t for t in re.split(r"[\s,]+", s.strip()) if t]
    return bool(toks) and all(t.isdigit() for t in toks)


def _print_proposal(candidates: list[dict], aoi_label: str) -> None:
    """Summary of the proposed layers, shown once before selection starts."""
    print()
    _activity(f"Proposed {len(candidates)} layer(s) for {aoi_label} — review then select:")
    last_theme = object()
    for i, c in enumerate(candidates, 1):
        if c.get("theme") != last_theme:
            last_theme = c.get("theme")
            _activity(f" {last_theme or 'other'}")
        src = c.get("source_id") or c.get("endpoint") or "?"
        label = c.get("title") or c["layer"]
        tag = "  ⚠ out-of-AOI" if not c.get("in_aoi", True) else ""
        print(f"  {i:>2}. {src} · {label}{tag}")
        if c.get("why"):
            print(f"      ↳ {c['why']}")
    print()


def _print_candidates(candidates: list[dict], aoi_label: str) -> None:
    print()
    _activity(f"Layers for {aoi_label}:")
    last_theme = object()
    for i, c in enumerate(candidates, 1):
        if c.get("theme") != last_theme:
            last_theme = c.get("theme")
            _activity(f" {last_theme or 'other'}")
        box = "[x]" if c.get("keep") else "[ ]"
        src = c.get("source_id") or c.get("endpoint") or "?"
        label = c.get("title") or c["layer"]
        flags = ""
        if not c.get("in_aoi", True):
            flags += "  ⚠ out-of-AOI"
        if c.get("fetched"):
            fc = c.get("feature_count")
            flags += f"  · {fc} feat" if fc is not None else f"  · {c.get('error') or 'no data'}"
        print(f"  {box} {i:>2}  {src} · {label}{flags}")


def _picker_outcome(candidates: list[dict], ctx) -> dict:
    selected, skipped = [], []
    for c in candidates:
        entry = {k: c[k] for k in ("theme", "source_id", "endpoint", "service_type",
                                   "layer", "title", "feature_count", "error")
                 if c.get(k) is not None}
        entry["fetched"] = bool(c.get("fetched"))
        if not c.get("in_aoi", True):
            entry["out_of_aoi"] = True
        (selected if c.get("keep") else skipped).append(entry)
    return {"selected": selected, "skipped": skipped, "aoi": ctx.aoi_summary}


# ---------------------------------------------------------------------------
# AOI resolution & interactive confirmation
# ---------------------------------------------------------------------------

_DEFAULT_BUFFER_M = 1000.0
_LINE_TYPES = ("LineString", "MultiLineString", "Point", "MultiPoint")


def _buffer_to_polygon(geom4326, meters: float):
    """Buffer a (line/point) shapely geometry by ``meters`` → polygon in EPSG:4326."""
    import geopandas as gpd
    g = gpd.GeoSeries([geom4326], crs="EPSG:4326")
    metric = g.estimate_utm_crs()
    return g.to_crs(metric).buffer(meters).to_crs("EPSG:4326").iloc[0]


def _place_options(query: str, limit: int = 6) -> list[dict]:
    """Nominatim candidates for a place name → AOI options (kind=place)."""
    from .geocode import geocode_place
    opts = []
    for r in geocode_place(query, limit=limit):
        if not r.get("bbox"):
            continue
        opts.append({
            "kind": "place", "label": r.get("display_name") or query,
            "display_name": r.get("display_name") or query,
            "geometry": r.get("geometry"), "bbox": r.get("bbox"),
            "country_code": r.get("country_code"),
            "feature_type": r.get("category") or r.get("osm_type"),
            "is_target": False, "n_features": None, "_gdf": None,
            "buffer_m": None, "source": "OpenStreetMap (Nominatim)",
        })
    return opts


_NAME_COLS = ("geoname", "nom", "name", "lib", "libelle", "toponyme",
              "territory1", "sovereign1")
_AOI_SPLIT_CAP = 15


def _feat_opt(g, display_name: str, cand: dict, country, layer_real: str) -> dict:
    """Build one feature AOI option from a (sub)GeoDataFrame (its union)."""
    from shapely.geometry import mapping
    union = g.union_all() if hasattr(g, "union_all") else g.unary_union
    minx, miny, maxx, maxy = g.total_bounds
    origin = cand.get("source_id") or cand.get("endpoint")
    opt = {
        "kind": "feature", "label": display_name, "display_name": display_name,
        "geometry": mapping(union), "bbox": (float(minx), float(miny), float(maxx), float(maxy)),
        "country_code": country, "feature_type": cand.get("layer"),
        "is_target": True, "n_features": int(len(g)), "_gdf": g,
        "buffer_m": cand.get("buffer_m"), "source": f"{origin} · {layer_real}",
    }
    for k in ("source_id", "endpoint", "service_type", "layer"):  # for export / catalog.yml
        if cand.get(k):
            opt[k] = cand[k]
    return opt


def _feature_option(cand: dict, sources: list[Source]) -> list[dict]:
    """Resolve a named feature from a data layer → AOI option(s) (kind=feature).

    Returns a LIST: when a name matches several distinct features (e.g. France's
    EEZ = metropole + each overseas territory), each becomes its own selectable
    AOI plus a combined "all (union)" option, so the user picks the right one.
    """
    import tempfile

    import geopandas as gpd

    from ..catalog import GeoDataCatalog
    from ..request import GeoRequest

    if not cand.get("layer"):
        raise ValueError("feature candidate needs a 'layer' (call list_layers first)")
    if not (cand.get("source_id") or (cand.get("endpoint") and cand.get("service_type"))):
        raise ValueError("feature candidate needs source_id or endpoint+service_type")
    name = (cand.get("name") or "").strip()
    bbox = _parse_bbox(cand.get("search_bbox"))
    country = None
    if bbox is None and cand.get("place_hint"):
        from .geocode import geocode_place
        hits = geocode_place(cand["place_hint"], limit=1)
        if hits:
            bbox, country = hits[0].get("bbox"), hits[0].get("country_code")
    # A sea/ocean/EEZ/region geocodes to a POINT (zero-area bbox): search worldwide
    # and isolate the feature by name instead (needs a `name`).
    worldwide = bbox is None or _bbox_area(bbox) < 1e-6
    if worldwide:
        if not name:
            raise ValueError("feature candidate needs search_bbox/place_hint, or a "
                             "`name` to search worldwide")
        bbox = (-180.0, -85.0, 180.0, 85.0)
        country = None

    obj = _layer_object(cand, sources, lid="aoi_feature")
    _resolve_wfs_typename(obj)  # tolerate an approximate/invented WFS layer name
    catalog = GeoDataCatalog(layers=[obj], cache_dir=tempfile.mkdtemp(prefix="gdc_aoi_"))
    req = GeoRequest(bbox=tuple(bbox), max_features=20000 if worldwide else None)
    results = catalog.fetch(req, no_cache=True)
    gdf = results.get(obj.layer_id)
    if not isinstance(gdf, gpd.GeoDataFrame) or gdf.empty:
        raise ValueError(f"no features found for {cand.get('layer')}")
    if name:
        matched = _filter_by_name(gdf, name)
        if not matched.empty:
            gdf = matched
        elif worldwide:
            raise ValueError(f"no feature matching {name!r} in {cand.get('layer')}")
        else:
            _activity(f"  · no attribute matched {name!r}; keeping all features in the area")

    g = gdf.to_crs("EPSG:4326")
    layer_real = getattr(getattr(obj, "product", None), "typename", None) or cand.get("layer")
    namecol = next((c for c in _NAME_COLS if c in g.columns), None)
    base = name or cand.get("label") or cand.get("layer")

    # Several distinct matches → one option each + a combined union.
    if name and 1 < len(g) <= _AOI_SPLIT_CAP:
        opts = [_feat_opt(g, f"{base} — all {len(g)} parts (union)", cand, country, layer_real)]
        for i in range(len(g)):
            row = g.iloc[[i]]
            lbl = str(g.iloc[i][namecol]) if namecol and g.iloc[i].get(namecol) else f"{base} #{i + 1}"
            opts.append(_feat_opt(row, lbl, cand, country, layer_real))
        return opts
    return [_feat_opt(g, base, cand, country, layer_real)]


def _match_typename(requested: str, available: list[str]) -> str | None:
    """Best WFS typename matching an approximate ``requested`` name.

    Tolerant of an invented namespace/version and of spelling drift: the model
    may pass 'BassinVersant' → 'sa:BassinVersantTopographique_FXX_Topage2025', or
    even 'SANDRE:PlanDEau_v1' → 'sa:PlanEau_FXX_Topage2025'. The request is reduced
    to its core concept (namespace + ``_v1`` dropped) and matched against each
    layer's base name by substring or fuzzy ratio. Prefers metropole (FXX / no DOM
    suffix) and the most recent year.
    """
    if requested in available:
        return requested
    import difflib
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    req = norm(re.sub(r"_v\d+$", "", requested.split(":")[-1]))  # drop 'SANDRE:' / '_v1'
    if not req:
        return None
    dom = ("_glp", "_mtq", "_guf", "_reu", "_myt")
    base = lambda a: norm(a.split(":")[-1].split("_")[0])        # 'sa:PlanEau_FXX_Topage2025' → 'planeau'

    def relevant(a: str) -> bool:
        b = base(a)
        return (req in norm(a) or req in b or b in req
                or difflib.SequenceMatcher(None, req, b).ratio() >= 0.7)

    matches = [a for a in available if relevant(a)]
    if not matches:
        return None

    def score(a: str):
        al = a.lower()
        year = max((int(y) for y in re.findall(r"(?:19|20)\d{2}", a)), default=0)
        ratio = round(difflib.SequenceMatcher(None, req, base(a)).ratio(), 2)
        return (not any(d in al for d in dom), ratio, year, "_fxx" in al, -len(a))

    return max(matches, key=score)


def _resolve_wfs_typename(obj) -> None:
    """If ``obj`` is a WFS layer with an unknown typename, fix it from capabilities."""
    product = getattr(obj, "product", None)
    typename = getattr(product, "typename", None)
    if product is None or not typename:
        return
    try:
        available = list(obj._get_wfs().contents)
    except Exception:
        return
    if typename in available:
        return
    best = _match_typename(typename, available)
    if best:
        _activity(f"  · resolved layer {typename!r} → {best}")
        product.typename = best


#: Raster service classes — they return images, not vector geometry, so they can
#: never define a kind='feature' AOI (which needs polygons/lines to union).
_RASTER_LAYER_KINDS = {"WMSLayer": "WMS", "WMTSLayer": "WMTS", "WCSLayer": "WCS"}


def _verify_feature_layer(cand: dict, sources: list[Source]) -> str | None:
    """Check a kind='feature' candidate's layer against the service's REAL layers.

    Returns ``None`` when the layer is usable (auto-correcting an approximate WFS
    typename in place). Returns an actionable error string the model must act on
    when the layer cannot be a feature AOI:

    * a raster service (WMS/WMTS/WCS) — it yields images, not vector features;
    * a guessed/unknown WFS layer with no close match in the capabilities.

    The point is to stop the model guessing ("tenter au hasard"): a bad layer is
    rejected with a hint to call ``list_layers`` and re-propose with the exact id,
    instead of silently falling back to geocoding the place. Network failures while
    reading capabilities are NOT treated as errors (we don't block on a flaky
    service) — the later fetch/fallback still handles those.
    """
    if not cand.get("layer"):
        return None  # _feature_option raises its own clear message
    label = cand.get("source_id") or cand.get("endpoint") or "the service"
    try:
        obj = _layer_object(cand, sources, lid="aoi_verify")
    except Exception:
        return None  # let _feature_option surface the build error verbatim
    cls = type(obj).__name__
    if cls in _RASTER_LAYER_KINDS:
        return (f"`{label}` is a {_RASTER_LAYER_KINDS[cls]} (raster) service — it returns "
                f"images, not vector features, so layer {cand['layer']!r} cannot define a "
                f"feature AOI. Call list_layers to find a WFS/vector layer for the real "
                f"geometry, or treat this as DATA over a place AOI via propose_layers.")
    if cls == "WFSLayer":
        product = getattr(obj, "product", None)
        typename = getattr(product, "typename", None)
        if not typename:
            return None
        try:
            available = list(obj._get_wfs().contents)
        except Exception:
            return None  # capabilities unreachable → don't block on a flaky service
        if typename in available or _match_typename(typename, available):
            return None  # exact or close enough; _feature_option corrects it
        sample = ", ".join(available[:12])
        return (f"layer {typename!r} does not exist on `{label}`. Call "
                f"list_layers for that source to get the exact layer ids "
                f"(e.g. {sample} …) and re-call propose_aoi with a real one — do not guess.")
    return None


def _verify_feature_candidates(candidates: list[dict], sources: list[Source]) -> list[str]:
    """Collect verification errors for every kind='feature' candidate (see
    :func:`_verify_feature_layer`). Empty list ⇒ all feature layers are usable."""
    errs = []
    for c in candidates or []:
        if c.get("kind") == "feature":
            e = _verify_feature_layer(c, sources)
            if e:
                errs.append(e)
    return errs


def _filter_by_name(gdf, name: str):
    """Rows whose any non-geometry column contains ``name`` (case-insensitive).

    Substring (not exact) so 'France' on the EEZ layer keeps EVERY French zone
    (metropole + all overseas) — their union becomes a MultiPolygon AOI.
    """
    name_l = name.strip().lower()
    geom_col = gdf.geometry.name
    mask = None
    for col in gdf.columns:
        if col == geom_col:
            continue
        try:
            col_mask = gdf[col].astype(str).str.lower().str.contains(name_l, na=False, regex=False)
        except Exception:
            continue
        mask = col_mask if mask is None else (mask | col_mask)
    return gdf.iloc[0:0] if mask is None else gdf[mask]


def _resolve_aoi_candidates(candidates: list[dict], sources: list[Source]) -> list[dict]:
    """Resolve raw AOI candidates (place/feature/bbox) to concrete AOI options.

    Shared by the interactive chat picker and the backend ``resolve_aoi``. Each
    candidate that fails falls back to geocoding its name, so the caller still
    gets options when a data service is down. Returns de-duplicated options.
    """
    options: list[dict] = []
    for cand in candidates or []:
        try:
            if cand.get("kind") == "feature":
                options.extend(_feature_option(cand, sources))
            elif cand.get("kind") == "bbox":
                options.append(_bbox_option(cand))
            else:
                options.extend(_place_options(cand.get("query", ""),
                                              limit=int(cand.get("limit", 6))))
        except Exception as e:
            _activity(f"  · candidate failed: {type(e).__name__}: {e}")
            fb = cand.get("name") or cand.get("place_hint") or cand.get("query")
            if fb:
                try:
                    extra = _place_options(fb, limit=3)
                    if extra:
                        _activity(f"  · falling back to geocoding {fb!r}")
                        options.extend(extra)
                except Exception:
                    pass
    return _dedupe_aoi(options)


def _coverage_status(c: dict, sources: list[Source], accepted: set[str]) -> tuple[str | None, bool]:
    """(coverage_label, is_in_aoi) for a candidate (registry sources only)."""
    if c.get("source_id"):
        try:
            src = get_source(sources, c["source_id"])
        except KeyError:
            return None, True
        return src.coverage, (not accepted or src.coverage in accepted)
    return None, True  # ad-hoc service: unknown coverage, keep


def _needs_auth(c: dict, sources: list[Source]) -> bool:
    """True when a candidate's registry source requires credentials (auth != none),
    so the picker/plugin can warn before a fetch fails with a 'needs credentials' error."""
    if c.get("source_id"):
        try:
            src = get_source(sources, c["source_id"])
        except KeyError:
            return False
        return bool(src.auth) and src.auth != "none"
    return False  # ad-hoc service: unknown


def _sends_datetime(c: dict, sources: list[Source]) -> bool | None:
    """Whether a candidate's layer forwards the request datetime (temporal), so the
    plugin can flag it with a symbol. Registry source → from its type/config; ad-hoc
    service → from its service type; unknown → None (see search.temporal_for)."""
    from .search import temporal_for
    if c.get("source_id"):
        try:
            src = get_source(sources, c["source_id"])
        except KeyError:
            return None
        return temporal_for(src.type, src.catalog)
    if c.get("service_type"):
        return temporal_for(c["service_type"])
    return None


def _flatten_candidates(groups: list[dict], sources: list[Source],
                        accepted: set[str]) -> list[dict]:
    """Flatten proposed theme groups into flat layer candidates, flagging coverage."""
    out = []
    for g in groups or []:
        theme = g.get("theme", "")
        for l in g.get("layers", []):
            if not l.get("layer"):
                continue
            c = {"theme": theme, "layer": l["layer"],
                 "title": l.get("title", ""), "why": l.get("why", "")}
            for k in ("source_id", "endpoint", "service_type"):
                if l.get(k):
                    c[k] = l[k]
            c["coverage"], c["in_aoi"] = _coverage_status(c, sources, accepted)
            c["needs_auth"] = _needs_auth(c, sources)
            c["sends_datetime"] = _sends_datetime(c, sources)
            out.append(c)
    return out


def _aoi_feature_collection(options: list[dict]) -> dict:
    """Serialise resolved AOI options to a GeoJSON FeatureCollection (no _gdf)."""
    from shapely.geometry import box, mapping
    feats = []
    for i, o in enumerate(options):
        geom = o.get("geometry") or (mapping(box(*o["bbox"])) if o.get("bbox") else None)
        feats.append({
            "type": "Feature", "id": i, "geometry": geom,
            "properties": {
                "display_name": o.get("display_name"), "kind": o.get("kind"),
                "source": o.get("source"), "geometry_kind": _geom_kind(o),
                "n_features": o.get("n_features"), "is_target": o.get("is_target", False),
                "bbox": list(o["bbox"]) if o.get("bbox") else None,
                "buffer_m": o.get("buffer_m"),
                # keep the layer origin so the client can re-fetch/configure it
                **{k: o[k] for k in ("source_id", "endpoint", "service_type", "layer")
                   if o.get(k)},
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def _aoi_from_geojson(geojson) -> tuple[dict | None, tuple, str | None]:
    """Parse a user AOI GeoJSON → (geometry_mapping, bbox_4326, display_name).

    Accepts a FeatureCollection, a Feature, or a bare geometry (dict, JSON string,
    or path to a .geojson). Multiple features are unioned into one AOI geometry.
    """
    import json as _json
    from pathlib import Path as _Path

    import geopandas as gpd
    from shapely.geometry import mapping, shape

    if isinstance(geojson, (str, _Path)):
        s = str(geojson)
        text = _Path(s).read_text(encoding="utf-8") if _Path(s).exists() else s
        geojson = _json.loads(text)
    if not isinstance(geojson, dict):
        raise ValueError("aoi must be a GeoJSON dict, JSON string or path")

    gtype = geojson.get("type")
    name = None
    if gtype == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in geojson.get("features", []) if f.get("geometry")]
        for f in geojson.get("features", []):
            name = name or (f.get("properties") or {}).get("display_name")
    elif gtype == "Feature":
        geoms = [shape(geojson["geometry"])] if geojson.get("geometry") else []
        name = (geojson.get("properties") or {}).get("display_name")
    else:  # bare geometry
        geoms = [shape(geojson)]
    if not geoms:
        raise ValueError("no geometry found in the AOI GeoJSON")

    gser = gpd.GeoSeries(geoms, crs="EPSG:4326")
    union = gser.union_all() if hasattr(gser, "union_all") else gser.unary_union
    minx, miny, maxx, maxy = gser.total_bounds
    return mapping(union), (float(minx), float(miny), float(maxx), float(maxy)), name


def _dedupe_aoi(options: list[dict]) -> list[dict]:
    """Drop duplicate AOI options (same display_name + rounded bbox)."""
    seen, out = set(), []
    for o in options:
        bb = tuple(round(v, 4) for v in o["bbox"]) if o.get("bbox") else None
        key = (o.get("display_name"), o.get("kind"), bb)
        if key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


def _geom_kind(o: dict) -> str:
    g = o.get("geometry")
    if not g:
        return "bbox"
    t = g.get("type") or "?"
    return {"Point": "point", "MultiPoint": "multipoint", "LineString": "line",
            "MultiLineString": "multiline", "Polygon": "polygon",
            "MultiPolygon": "multipolygon"}.get(t, t)


def _aoi_label(o: dict) -> str:
    bits = [o.get("display_name") or "?", _geom_kind(o)]
    if o.get("n_features"):
        bits.append(f"{o['n_features']} feat")
    bits.append(f"via {o.get('source', '?')}")
    return " · ".join(bits)


def _bbox_option(cand: dict) -> dict:
    """An explicit bbox candidate (model estimate) → a pure bounding-box AOI."""
    bbox = _parse_bbox(cand.get("bbox") or cand.get("search_bbox"))
    if bbox is None:
        raise ValueError("bbox candidate needs 'bbox' = 'lon_min,lat_min,lon_max,lat_max'")
    return {
        "kind": "bbox", "label": cand.get("label") or "bbox (estimate)",
        "display_name": cand.get("label") or "bbox", "geometry": None, "bbox": bbox,
        "country_code": None, "feature_type": "bbox", "is_target": False,
        "n_features": None, "_gdf": None, "buffer_m": None,
        "source": "manual bbox (model estimate)",
    }


def _aoi_geodataframe(o: dict):
    """A GeoDataFrame for previewing/exporting an AOI option."""
    import geopandas as gpd
    from shapely.geometry import box, shape
    if o.get("_gdf") is not None:
        return o["_gdf"]
    if o.get("geometry"):
        return gpd.GeoDataFrame(geometry=[shape(o["geometry"])], crs="EPSG:4326")
    return gpd.GeoDataFrame(geometry=[box(*o["bbox"])], crs="EPSG:4326")


def _preview_aoi(o: dict) -> None:
    try:
        m = _aoi_geodataframe(o).explore()
        out = Path(f"{_safe_name(o.get('display_name') or 'aoi')}_aoi.html")
        m.save(str(out))
        _activity(f"  ✓ AOI map saved: {out}")
        _open_in_browser(out)
    except Exception as e:
        _activity(f"  ✗ AOI preview failed: {type(e).__name__}: {e}")


def _apply_buffer(o: dict, meters: float) -> None:
    """Replace a line/point AOI geometry with its buffered polygon, in place."""
    from shapely.geometry import mapping, shape
    if not o.get("geometry"):
        return
    try:
        poly = _buffer_to_polygon(shape(o["geometry"]), meters)
        o["geometry"] = mapping(poly)
        o["bbox"] = tuple(float(v) for v in poly.bounds)
        o["buffered_m"] = meters
        o["_gdf"] = None  # the cached feature gdf is the unbuffered line; drop it
        _activity(f"  ✓ buffered by {meters:g} m")
    except Exception as e:
        _activity(f"  ✗ buffer failed: {type(e).__name__}: {e}")


def _needs_buffer(o: dict) -> bool:
    g = o.get("geometry")
    return bool(g) and g.get("type") in _LINE_TYPES


def _ask_float(prompt: str, default: float, q=None) -> float | None:
    raw = (q.text(prompt, default=str(default)).ask() if q is not None
           else input(f"  {prompt} [{default:g}] ").strip())
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        _activity(f"  ? not a number: {raw!r}")
        return default


def _ask_yes(prompt: str, default: bool, q=None) -> bool:
    if q is not None:
        ans = q.confirm(prompt, default=default).ask()
        return default if ans is None else bool(ans)
    raw = input(f"  {prompt} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "o", "oui")


def _print_aoi_options(options: list[dict]) -> None:
    print()
    _activity(f"Area-of-interest candidates ({len(options)}) — confirm one:")
    for i, o in enumerate(options, 1):
        print(f"  {i:>2}. {_aoi_label(o)}")


def _finalize_buffer(o: dict, q=None) -> None:
    """If the AOI is a line/point, buffer it into a polygon (user-adjustable)."""
    if not _needs_buffer(o):
        return
    default = o.get("buffer_m") or _DEFAULT_BUFFER_M
    while True:
        meters = _ask_float("Buffer around the line/point in metres:", default, q)
        if meters is None or meters <= 0:
            _activity("  · buffer skipped (AOI stays a line — clipping will be ineffective)")
            return
        _apply_buffer(o, meters)
        if not _ask_yes("Adjust the buffer size again?", False, q):
            return


def _export_aoi(o: dict) -> None:
    """Save the AOI geometry (the resolved feature, or the polygon/bbox) to ./out."""
    try:
        gdf = _aoi_geodataframe(o)
        out_dir = Path("out")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{_safe_name(o.get('display_name') or 'aoi')}_aoi.geojson"
        gdf.to_file(path, driver="GeoJSON")
        _activity(f"  ✓ AOI exported: {path}")
    except Exception as e:
        _activity(f"  ✗ AOI export failed: {type(e).__name__}: {e}")


def _aoi_commands(o: dict, sources: list[Source]) -> None:
    """Print catalog.yml + add/fetch commands for the AOI's underlying data layer."""
    if o.get("kind") != "feature" or not o.get("layer"):
        _activity("  · a geocoded place AOI has no data layer — nothing to configure "
                  "(export it as GeoJSON instead)")
        return
    entry = _catalog_entry_for(o, sources, o.get("bbox"), None)
    if entry:
        _activity("  (note: the fetch returns all features of this layer in the bbox, "
                  "not only this one)")
        _print_ready(entry)


def _pick_aoi(options: list[dict], sources: list[Source]) -> tuple[dict | None, bool]:
    """Interactive AOI confirmation. Returns (chosen_option | None, clip_layers).

    The user picks a candidate, then per AOI can preview it, export it, or get its
    catalog.yml + fetch commands, before confirming it as the area of interest.
    """
    q = _questionary()
    while True:
        chosen = _aoi_choose(options, q)
        if chosen is None:
            return None, True
        while True:
            action = _aoi_action(chosen, q)
            if action == "use":
                _finalize_buffer(chosen, q)
                clip = _ask_yes("Clip study layers to this AOI?", True, q)
                return chosen, clip
            if action == "another":
                break
            if action == "cancel":
                return None, True
            if action == "preview":
                _preview_aoi(chosen)
            elif action == "export":
                _export_aoi(chosen)
            elif action == "commands":
                _aoi_commands(chosen, sources)


def _aoi_choose(options: list[dict], q) -> dict | None:
    """Select one AOI candidate (None = cancel)."""
    if q is None:
        return _aoi_choose_plain(options)
    choices = [q.Choice(_aoi_label(o), i) for i, o in enumerate(options)]
    choices.append(q.Choice("✗ Cancel", "cancel"))
    ans = q.select("Pick the area of interest:", choices=choices).ask()
    return None if ans in (None, "cancel") else options[ans]


def _aoi_action(chosen: dict, q) -> str:
    """Action on the chosen AOI: use | preview | export | commands | another | cancel."""
    if q is None:
        return _aoi_action_plain(chosen)
    ans = q.select(
        f"AOI: {chosen.get('display_name')} — what next?",
        choices=[
            q.Choice("✓ Use this area of interest", "use"),
            q.Choice("🗺  Preview it on a map", "preview"),
            q.Choice("💾 Export it to a file (./out)", "export"),
            q.Choice("📋 Get its catalog.yml + fetch commands", "commands"),
            q.Choice("↩  Pick another candidate", "another"),
            q.Choice("✗ Cancel", "cancel"),
        ]).ask()
    return ans or "cancel"


def _aoi_choose_plain(options: list[dict]) -> dict | None:
    while True:
        _print_aoi_options(options)
        print("Commands:  <n> choose · p <n> preview · q cancel")
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            continue
        cmd, _, rest = raw.partition(" ")
        cmd, rest = cmd.lower(), rest.strip()
        if cmd in ("q", "cancel"):
            return None
        if cmd in ("p", "preview"):
            for o in _select_indices(options, rest or input("  preview which number? ").strip()):
                _preview_aoi(o)
        elif cmd.isdigit():
            picked = _select_indices(options, raw)
            if picked:
                return picked[0]
        else:
            _activity("  ? type a number to choose, p <n> to preview, or q")


def _aoi_action_plain(chosen: dict) -> str:
    print(f"\nAOI: {chosen.get('display_name')}")
    print("  u use · p preview · e export · c catalog.yml/commands · b back · q cancel")
    try:
        raw = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "cancel"
    return {"u": "use", "": "use", "p": "preview", "e": "export",
            "c": "commands", "b": "another", "q": "cancel"}.get(raw, "?")


def _questionary():
    """Return the questionary module when usable (installed + interactive TTY)."""
    import sys
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        import questionary
        return questionary
    except ImportError:
        return None


def _choice_label(c: dict) -> str:
    src = c.get("source_id") or c.get("endpoint") or "?"
    label = c.get("title") or c["layer"]
    extra = "  ⚠ out-of-AOI" if not c.get("in_aoi", True) else ""
    if c.get("fetched"):
        fc = c.get("feature_count")
        extra += f"  · {fc} feat" if fc is not None else f"  · {c.get('error') or 'no data'}"
    return f"[{c.get('theme')}] {src} · {label}{extra}"


def _pick_layers(candidates: list[dict], ctx) -> dict:
    """Interactive terminal picker. Returns the selection outcome (or cancelled).

    Uses ``questionary`` (arrow-key checkboxes + action menu) when available,
    otherwise a plain numbered command loop. ``ctx`` exposes ``aoi_label``,
    ``aoi_summary`` and the per-layer callbacks ``fetch(kept)``, ``info(c)``,
    ``visualise(c)``, ``export(c)`` and ``catalog_entry(c)``.
    """
    for c in candidates:
        c.setdefault("keep", c.get("in_aoi", True))
        c.setdefault("fetched", False)
        c.setdefault("feature_count", None)
        c.setdefault("error", None)

    _print_proposal(candidates, ctx.aoi_label)
    q = _questionary()
    return _pick_q(candidates, ctx, q) if q is not None else _pick_plain(candidates, ctx)


# --- questionary path (menu-driven) ----------------------------------------

def _q_checkbox(candidates: list[dict], q) -> bool:
    """Let the user (re)choose which layers to keep. Returns False if cancelled."""
    choices = [q.Choice(title=_choice_label(c), value=i, checked=c.get("keep", True))
               for i, c in enumerate(candidates)]
    picked = q.checkbox("Select the layers to keep (space to toggle, enter to confirm):",
                        choices=choices).ask()
    if picked is None:
        return False
    picked = set(picked)
    for i, c in enumerate(candidates):
        c["keep"] = i in picked
    return True


def _q_pick_one(candidates: list[dict], q, verb: str):
    """Ask which single kept layer to act on; None if cancelled/back."""
    kept = [(i, c) for i, c in enumerate(candidates) if c.get("keep")]
    pool = kept or list(enumerate(candidates))
    choices = [q.Choice(title=_choice_label(c), value=i) for i, c in pool]
    choices.append(q.Choice(title="← back", value=None))
    idx = q.select(f"Which layer to {verb}?", choices=choices).ask()
    return candidates[idx] if isinstance(idx, int) else None


def _pick_q(candidates: list[dict], ctx, q) -> dict:
    if not _q_checkbox(candidates, q):
        return {"cancelled": True}
    while True:
        kept = [c for c in candidates if c.get("keep")]
        action = q.select(
            f"{len(kept)} layer(s) kept for {ctx.aoi_label}. What next?",
            choices=[
                q.Choice("✓ Done — finish and summarise", "done"),
                q.Choice("⬇  Fetch the kept layers on the AOI (feature counts)", "fetch"),
                q.Choice("🗺  Preview a layer on a map", "map"),
                q.Choice("💾 Export a layer to a file (./out)", "export"),
                q.Choice("📋 Get a layer's catalog.yml + commands", "yaml"),
                q.Choice("ℹ  Show a layer's details", "info"),
                q.Choice("☑  Change the selection", "select"),
                q.Choice("📍 Change the area of interest (new place)", "aoi"),
                q.Choice("✗ Cancel", "cancel"),
            ]).ask()
        if action in (None, "cancel"):
            return {"cancelled": True}
        if action == "done":
            return _picker_outcome(candidates, ctx)
        if action == "fetch":
            ctx.fetch(kept)
        elif action == "aoi":
            query = q.text("New area of interest (place name):").ask()
            ctx.set_aoi(query)
        elif action == "select":
            if not _q_checkbox(candidates, q):
                return {"cancelled": True}
        elif action == "info":
            c = _q_pick_one(candidates, q, "inspect")
            if c is not None:
                print(ctx.info(c))
        elif action == "map":
            c = _q_pick_one(candidates, q, "preview")
            if c is not None:
                ctx.visualise(c)
        elif action == "export":
            c = _q_pick_one(candidates, q, "export")
            if c is not None:
                ctx.export(c)
        elif action == "yaml":
            c = _q_pick_one(candidates, q, "configure")
            if c is not None:
                ctx.catalog_entry(c)


# --- plain path (numbered command loop, no extra deps) ----------------------

_PLAIN_HELP = (
    "  numbers = toggle layer(s) on/off   (e.g. 3   or   1 4 7)\n"
    "  a / z   = keep all / none\n"
    "  f       = fetch the kept layers on the AOI (shows feature counts)\n"
    "  i <n>   = details   ·   v <n> = preview map   ·   e <n> = export   ·   y <n> = catalog.yml\n"
    "  aoi <place> = change the area of interest (re-geocode)\n"
    "  d       = done (finish & summarise)   ·   q = cancel"
)


def _resolve_targets(candidates: list[dict], rest: str) -> list[dict]:
    """Indices from ``rest``; if empty, prompt for them. [] if none/aborted."""
    if not rest:
        try:
            rest = input("  which layer number(s)? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return []
    return _select_indices(candidates, rest)


def _pick_plain(candidates: list[dict], ctx) -> dict:
    while True:
        _print_candidates(candidates, ctx.aoi_label)
        print("Commands:")
        print(_PLAIN_HELP)
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return {"cancelled": True}
        if not raw:
            continue
        cmd, _, rest = raw.partition(" ")
        cmd, rest = cmd.lower(), rest.strip()
        if cmd in ("q", "cancel"):
            return {"cancelled": True}
        if cmd in ("d", "done"):
            return _picker_outcome(candidates, ctx)
        if cmd in ("a", "all"):
            for c in candidates:
                c["keep"] = True
        elif cmd in ("z", "none"):
            for c in candidates:
                c["keep"] = False
        elif cmd in ("f", "fetch"):
            ctx.fetch([c for c in candidates if c.get("keep")])
        elif cmd in ("aoi", "area", "zone"):
            ctx.set_aoi(rest or input("  new area (place name): ").strip())
        elif cmd in ("i", "info", "v", "map", "folium", "e", "export", "y", "yaml", "catalog"):
            targets = _resolve_targets(candidates, rest)
            if not targets:
                _activity(f"  · '{cmd}' needs a layer number, e.g. '{cmd} 1'")
                continue
            for c in targets:
                if cmd in ("i", "info"):
                    print(ctx.info(c))
                elif cmd in ("v", "map", "folium"):
                    ctx.visualise(c)
                elif cmd in ("e", "export"):
                    ctx.export(c)
                else:
                    ctx.catalog_entry(c)
        elif _is_indices(raw):
            for c in _select_indices(candidates, raw):
                c["keep"] = not c.get("keep")
        else:
            _activity(f"  ? unknown command {raw!r} — type a number to toggle, or d/q")


# ---------------------------------------------------------------------------
# Tool-using assistant (one session, provider-shaped history)
# ---------------------------------------------------------------------------

class _Assistant:
    def __init__(self, provider: str, sources: list[Source], model: str,
                 interactive: bool = True, system: str | None = None):
        self.provider = provider
        self.sources = sources
        self.model = model
        self.system = (system or _SYSTEM) + _registry_digest(sources)
        self.messages: list[dict] = []
        self._entries: list[dict] = []   # make_catalog_entry results to print at the end
        self.aoi: dict | None = None     # resolved AOI (display_name, geometry, bbox, is_target)
        self.accepted_coverage: set[str] = set()
        self.clip_aoi: bool = True       # clip thematic layers to the AOI polygon
        # --- headless (backend) mode: propose_* return data, no picker ---
        self.interactive = interactive
        self._headless_result: dict | None = None
        self._headless_capture = False   # capture chosen result + stop (backend, both modes)
        # layers the model has seen via list_layers — used to salvage a proposal
        # if the tool loop ends without the model ever calling propose_layers.
        self._seen_layers: list[dict] = []
        self._seen_keys: set = set()
        self._headless_fetch = False
        self._headless_export: str | None = None
        # Tool-call activity for the current headless turn (backend sessions):
        # {"tool", "args", "result"} — surfaced to the client alongside the result
        # so a UI can show what the assistant did, not just what it concluded.
        self._trace: list[dict] = []
        if provider == "anthropic":
            import anthropic
            self.client = anthropic.Anthropic()

    # --- stateful tool execution -------------------------------------------
    def _exec(self, name: str, args: dict) -> str:
        """Run a tool, capturing/injecting AOI state across turns.

        Stateless tools go through the module-level :func:`_dispatch`; the two
        stateful ones (geocode memory, interactive picker) are handled here.
        """
        if name == "geocode_place":
            return self._geocode(args)
        if name == "propose_aoi":
            return self._propose_aoi_headless(args) if not self.interactive else self._propose_aoi(args)
        if name == "propose_layers":
            return self._propose_layers_headless(args) if not self.interactive else self._propose_layers(args)
        if (name == "make_catalog_entry" and not (args.get("bbox") or "").strip()
                and self.aoi and self.aoi.get("bbox")):
            args = {**args, "bbox": _bbox_str(self.aoi["bbox"])}
        result = _dispatch(name, args, self.sources)
        if name == "list_layers":
            self._remember_layers(args, result)
        return result

    def _remember_layers(self, args: dict, result: str) -> None:
        """Stash layers matched by list_layers, so an exhausted tool loop can still
        propose them (see :meth:`run_headless`). Only positive matches are kept."""
        try:
            layers = (json.loads(result) or {}).get("layers") or []
        except (ValueError, TypeError):
            return
        origin = {k: args[k] for k in ("source_id", "endpoint", "service_type")
                  if args.get(k)}
        grep = args.get("grep")
        for l in layers[:8]:
            lid = l.get("id")
            key = (origin.get("source_id"), origin.get("endpoint"), lid)
            if not lid or key in self._seen_keys:
                continue
            self._seen_keys.add(key)
            self._seen_layers.append({
                **origin, "layer": lid, "title": l.get("title", ""),
                "why": f"matched {grep!r}" if grep else "listed for this source",
            })

    def _geocode(self, args: dict) -> str:
        from .geocode import geocode_place, recommended_coverages
        try:
            results = geocode_place(args["query"], limit=args.get("limit", 1))
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"
        aoi, slim = _format_geocode(results)
        if aoi:
            self.aoi = aoi
            self.accepted_coverage = recommended_coverages(aoi.get("country_code"), aoi.get("bbox"))
        return json.dumps(slim, ensure_ascii=False)

    def _propose_aoi(self, args: dict) -> str:
        from .geocode import recommended_coverages
        candidates = args.get("candidates") or []
        bad = _verify_feature_candidates(candidates, self.sources)
        if bad:
            return json.dumps({"error": "unverified feature layer(s) — never guess a layer name",
                               "problems": bad}, ensure_ascii=False)
        options = _resolve_aoi_candidates(candidates, self.sources)
        if not options:
            return json.dumps({"error": "no AOI candidate could be resolved — "
                                        "refine the place name or feature"})
        chosen, clip = _pick_aoi(options, self.sources)
        if chosen is None:
            return json.dumps({"cancelled": True})
        cc = chosen.get("country_code")
        self.aoi = {"display_name": chosen.get("display_name"), "geometry": chosen.get("geometry"),
                    "bbox": chosen.get("bbox"), "country_code": cc,
                    "is_target": chosen.get("is_target", False)}
        # No country code (e.g. a sea) → fall back to bbox-extent filtering, so a
        # country-specific source (e.g. finland) isn't offered for a disjoint AOI.
        self.accepted_coverage = recommended_coverages(cc, chosen.get("bbox"))
        self.clip_aoi = clip
        if self._headless_capture:  # backend --interactive: return the chosen AOI as GeoJSON
            self._headless_result = _aoi_feature_collection([chosen])
        return json.dumps({
            "chosen": chosen.get("display_name"), "kind": chosen.get("kind"),
            "source": chosen.get("source"),
            "bbox": list(chosen["bbox"]) if chosen.get("bbox") else None,
            "n_features": chosen.get("n_features"),
            "is_target": chosen.get("is_target", False),
            "buffered_m": chosen.get("buffered_m"),
            "has_polygon": bool(chosen.get("geometry")),
            "clip_layers": clip,
        }, ensure_ascii=False)

    def _candidates(self, groups: list[dict]) -> list[dict]:
        """Flatten proposed groups into candidates, flagging AOI coverage."""
        return _flatten_candidates(groups, self.sources, self.accepted_coverage)

    def _propose_layers(self, args: dict) -> str:
        bbox = _parse_bbox(args.get("bbox")) or (self.aoi or {}).get("bbox")
        aoi_geom = (self.aoi or {}).get("geometry")
        if bbox is None and aoi_geom is None:
            return json.dumps({"error": "no AOI resolved — call geocode_place first "
                                        "or pass an explicit bbox"})
        dt = (args.get("datetime") or "").strip() or None
        candidates = self._candidates(args.get("groups", []))
        if not candidates:
            return json.dumps({"error": "no candidate layers in groups"})

        # Mutable AOI state so the user can re-target the area mid-session.
        aoi = {"geom": aoi_geom, "bbox": bbox,
               "label": (self.aoi or {}).get("display_name") or _bbox_str(bbox),
               "summary": {"display_name": (self.aoi or {}).get("display_name"),
                           "bbox": list(bbox) if bbox else None}}

        def _fetch(kept):
            if not kept:
                _activity("  · nothing selected to fetch")
                return
            results = _fetch_on_aoi(kept, self.sources, aoi["geom"], aoi["bbox"], dt, self.clip_aoi)
            for c in kept:
                r = results.get(id(c)) or {}
                c["fetched"] = True
                c["feature_count"] = r.get("count")
                c["error"] = r.get("error")

        def _yaml(c):
            entry = _catalog_entry_for(c, self.sources, aoi["bbox"], dt)
            if entry:
                _print_ready(entry)

        def _set_aoi(query):
            """Re-geocode the AOI to a new place name; refresh state and flags."""
            from .geocode import geocode_place, recommended_coverages
            query = (query or "").strip()
            if not query:
                return None
            try:
                results = geocode_place(query, limit=1)
            except Exception as e:
                _activity(f"  ✗ geocode failed: {type(e).__name__}: {e}")
                return None
            new_aoi, _ = _format_geocode(results)
            if not new_aoi:
                _activity(f"  · no place matched {query!r}")
                return None
            self.aoi = new_aoi
            self.accepted_coverage = recommended_coverages(new_aoi.get("country_code"), new_aoi.get("bbox"))
            nb = new_aoi.get("bbox")
            aoi["geom"] = new_aoi.get("geometry")
            aoi["bbox"] = nb
            aoi["label"] = new_aoi.get("display_name") or _bbox_str(nb)
            aoi["summary"] = {"display_name": new_aoi.get("display_name"),
                              "bbox": list(nb) if nb else None}
            # coverage flags change with the country; previous fetch counts are stale
            for c in candidates:
                c["coverage"], c["in_aoi"] = _coverage_status(c, self.sources, self.accepted_coverage)
                c["fetched"], c["feature_count"], c["error"] = False, None, None
            ctx.aoi_label = aoi["label"]
            ctx.aoi_summary = aoi["summary"]
            _activity(f"  ✓ area set to {aoi['label']}")
            return aoi["label"]

        ctx = SimpleNamespace(
            aoi_label=aoi["label"], aoi_summary=aoi["summary"], fetch=_fetch,
            info=lambda c: _info_line(c, self.sources),
            visualise=lambda c: _visualise_item(c, self.sources, aoi["geom"], aoi["bbox"], dt, self.clip_aoi),
            export=lambda c: _export_item(c, self.sources, aoi["geom"], aoi["bbox"], dt, self.clip_aoi),
            catalog_entry=_yaml, set_aoi=_set_aoi,
        )
        outcome = _pick_layers(candidates, ctx)
        if self._headless_capture:  # backend --interactive: return the picker outcome
            self._headless_result = outcome
        return json.dumps(outcome, ensure_ascii=False, default=str)

    # --- headless (backend) tool handlers ----------------------------------
    def _propose_aoi_headless(self, args: dict) -> str:
        """Resolve AOI candidates to a GeoJSON FeatureCollection (no picker)."""
        candidates = args.get("candidates") or []
        bad = _verify_feature_candidates(candidates, self.sources)
        if bad:
            return json.dumps({"error": "unverified feature layer(s) — never guess a layer name",
                               "problems": bad}, ensure_ascii=False)
        options = _resolve_aoi_candidates(candidates, self.sources)
        if not options:
            return json.dumps({"error": "no AOI candidate could be resolved"})
        self._headless_result = _aoi_feature_collection(options)
        return json.dumps({"resolved": len(options),
                           "candidates": [_aoi_label(o) for o in options]}, ensure_ascii=False)

    def _propose_layers_headless(self, args: dict) -> str:
        """Flatten proposed layers (and optionally fetch on the AOI) → data."""
        # The AOI's own bbox wins when one is already fixed (the normal case for a
        # LayersSession/GeoLibre panel — the system prompt already forbids
        # propose_aoi/geocode_place there). Trusting the model's `bbox` arg instead
        # let it silently balloon the query turn over turn (each refine "helpfully"
        # widening the box to try to catch a whole named river, snowballing into an
        # oversized/nonsensical area and Overpass 504s) — `bbox` is only meant for
        # the AOI-less "broad zone" case (no propose_aoi ever called).
        bbox = (self.aoi or {}).get("bbox") or _parse_bbox(args.get("bbox"))
        aoi_geom = (self.aoi or {}).get("geometry")
        if bbox is None and aoi_geom is None:
            return json.dumps({"error": "no AOI — pass an AOI GeoJSON to fetch_for_aoi"})
        dt = (args.get("datetime") or "").strip() or None
        candidates = self._candidates(args.get("groups", []))
        if not candidates:
            return json.dumps({"error": "no candidate layers in groups"})

        if self._headless_fetch or self._headless_export:
            kept = [c for c in candidates if c.get("in_aoi", True)]
            counts = _fetch_on_aoi(kept, self.sources, aoi_geom, bbox, dt, self.clip_aoi,
                                   export_dir=self._headless_export)
            for c in kept:
                r = counts.get(id(c)) or {}
                c["fetched"] = True
                c["feature_count"] = r.get("count")
                c["error"] = r.get("error")
        self._headless_result = {
            "aoi": {"display_name": (self.aoi or {}).get("display_name"),
                    "bbox": list(bbox) if bbox else None},
            "datetime": dt, "clip": self.clip_aoi,
            "exported_to": self._headless_export if self._headless_fetch or self._headless_export else None,
            "layers": [{k: c.get(k) for k in
                        ("theme", "source_id", "endpoint", "service_type", "layer", "title",
                         "why", "coverage", "in_aoi", "needs_auth", "sends_datetime",
                         "fetched", "feature_count", "error")
                        if c.get(k) is not None} for c in candidates],
        }
        return json.dumps({"proposed": len(candidates),
                           "fetched": bool(self._headless_fetch or self._headless_export)})

    def run_headless(self, query: str, terminal: str) -> dict | None:
        """Run the tool loop until the ``terminal`` tool yields a structured result.

        The model may first call list_layers/discover/geocode_place, then the
        terminal tool (propose_aoi or propose_layers) which captures the result.
        """
        self._headless_result = None
        self._seen_layers = []
        self._seen_keys = set()
        self._trace = []
        self.messages.append({"role": "user", "content": query})
        for _ in range(MAX_TOOL_TURNS):
            done = self._step_mistral() if self.provider == "mistral" else self._step_anthropic()
            if self._headless_result is not None:
                break
            if done:
                break
        # The loop ended without the model calling propose_layers (e.g. it spent
        # every turn on list_layers). Salvage a proposal from the layers it did
        # find rather than returning an empty result.
        if (self._headless_result is None and terminal == "propose_layers"
                and self._seen_layers):
            _activity(f"  · tool budget reached — proposing {len(self._seen_layers)} "
                      "layer(s) found so far")
            self._propose_layers_headless(
                {"groups": [{"theme": "", "layers": self._seen_layers}]})
            if self._headless_result is not None:
                self._headless_result["partial"] = True
                # The salvage bypassed the normal tool-result, so the history has no
                # record of what was proposed. Add a short assistant note (only when
                # it keeps roles alternating) so a refinement turn stays coherent.
                if self.messages and self.messages[-1].get("role") != "assistant":
                    proposed = ", ".join(
                        f"{l.get('source_id', '?')}/{l.get('layer', '')}"
                        for l in self._headless_result.get("layers", []))
                    self.messages.append({"role": "assistant", "content": (
                        "I hit the tool-call budget and auto-proposed these layers: "
                        f"{proposed}. We can refine from here.")})
        if self._headless_result is not None:
            # Actions taken this turn + a synthesized recap, so a UI can show what
            # the assistant did and not just the raw proposal.
            self._headless_result["actions"] = self._trace
            self._headless_result["summary"] = _summarize_headless(terminal, self._headless_result)
        return self._headless_result

    def _record(self, tool_name: str, result: str) -> None:
        """Stash make_catalog_entry results so we can print clean commands later."""
        if tool_name == "make_catalog_entry" and not result.startswith("error"):
            try:
                self._entries.append(json.loads(result))
            except ValueError:
                pass

    def _flush_ready(self) -> None:
        for entry in self._entries:
            _print_ready(entry)
        self._entries = []

    def ask(self, text: str) -> None:
        _print_question(text)
        self.messages.append({"role": "user", "content": text})
        self._entries = []
        for _ in range(MAX_TOOL_TURNS):
            done = self._step_mistral() if self.provider == "mistral" else self._step_anthropic()
            if done:
                self._flush_ready()
                return
        self._flush_ready()
        print("(stopped after several tool calls — try rephrasing)")

    # --- Mistral (OpenAI-compatible function calling) ----------------------
    def _step_mistral(self) -> bool:
        import requests

        tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": t["input_schema"],
        }} for t in _TOOLS]
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": self.system}, *self.messages],
            "tools": tools, "tool_choice": "auto", "max_tokens": MAX_TOKENS,
        }
        with _thinking():
            resp = requests.post(
                MISTRAL_URL, json=payload,
                headers={"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"},
                timeout=120,
            )
        if resp.status_code == 429:
            raise RuntimeError("Mistral rate limit reached (free tier) — retry in a moment")
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        msg = choice["message"]
        calls = msg.get("tool_calls") or []
        self.messages.append({"role": "assistant", "content": msg.get("content") or "",
                              "tool_calls": calls})
        if not calls:
            _print_answer_header()
            _render(msg.get("content") or "")
            return True
        # The model can hit the token budget mid tool-call, leaving truncated
        # (unparseable) JSON arguments. Every tool_call_id MUST get a tool reply or
        # the next request 400s — so on a parse failure we answer with an error
        # asking the model to retry more concisely, instead of crashing the request.
        truncated = choice.get("finish_reason") == "length"
        for call in calls:
            fn = call["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                _activity(f"  · {fn['name']}: truncated/invalid arguments — asking the model to retry")
                self._trace.append({"tool": fn["name"], "args": {},
                                    "result": "truncated/invalid arguments — asked the model to retry"})
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "name": fn["name"],
                    "content": json.dumps({"error": "your tool arguments were truncated or were not "
                        "valid JSON (the output was too long). Retry this call with FEWER layers and "
                        "SHORTER text fields (keep each 'why'/'title' to one short phrase)."})})
                continue
            _activity(_fmt_call(fn["name"], args))
            result = self._exec(fn["name"], args)
            summary = _result_summary(fn["name"], result)
            _activity(f"    → {summary}")
            self._trace.append({"tool": fn["name"], "args": _trace_args(fn["name"], args), "result": summary})
            self._record(fn["name"], result)
            self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                  "name": fn["name"], "content": result})
        if truncated:
            _activity("  · response hit the token budget — continuing")
        return False

    # --- Anthropic (tool use) ----------------------------------------------
    def _step_anthropic(self) -> bool:
        tools = [{"name": t["name"], "description": t["description"],
                  "input_schema": t["input_schema"]} for t in _TOOLS]
        with _thinking():
            resp = self.client.messages.create(
                model=self.model, max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": self.system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=self.messages, tools=tools,
            )
        self.messages.append({"role": "assistant", "content": resp.content})
        if any(b.type == "text" and b.text.strip() for b in resp.content):
            _print_answer_header()
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                _render(block.text)
        if resp.stop_reason != "tool_use":
            return True
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                args = dict(block.input)
                _activity(_fmt_call(block.name, args))
                result = self._exec(block.name, args)
                summary = _result_summary(block.name, result)
                _activity(f"    → {summary}")
                self._trace.append({"tool": block.name, "args": _trace_args(block.name, args), "result": summary})
                self._record(block.name, result)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": result})
        self.messages.append({"role": "user", "content": results})
        return False


def chat(question: str | None, sources: list[Source]) -> None:
    """One-shot answer, or an interactive REPL when ``question`` is None."""
    provider, reason = pick_provider()
    if reason:
        raise RuntimeError(reason)
    default = DEFAULT_MISTRAL_MODEL if provider == "mistral" else DEFAULT_ANTHROPIC_MODEL
    model = os.environ.get("GEODATACATALOG_AI_MODEL", default)
    assistant = _Assistant(provider, sources, model)

    if question:
        print()
        assistant.ask(question)
        return

    print(f"geodatacatalog chat — {provider} / {model} (Ctrl-D or 'exit' to quit)")
    while True:
        try:
            line = input("\n? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line or line.lower() in ("exit", "quit"):
            break
        assistant.ask(line)
