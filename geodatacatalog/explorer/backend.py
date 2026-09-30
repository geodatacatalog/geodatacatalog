"""Backend API: two decoupled, AI-assisted steps for a (future) frontend.

The baseline usage is TWO calls:

1. :func:`resolve_aoi` — a natural-language area description → a GeoJSON
   ``FeatureCollection`` of candidate areas of interest (with provenance). The
   client (or user) picks one, or supplies its own GeoJSON instead.
2. :func:`fetch_for_aoi` — a user AOI GeoJSON + a natural-language data request →
   the layers the AI finds relevant on that AOI (optionally fetched/exported).

The two are independent (step 2 accepts any GeoJSON, not only step 1's output).
The combined interactive flow stays available via ``geodatacatalog chat``.

Both steps reuse the same tool-using assistant in *headless* mode: the model may
call ``list_layers``/``discover``/``geocode_place`` first, then the terminal tool
(``propose_aoi`` / ``propose_layers``) returns structured data instead of opening
the terminal picker.

To **iterate in natural language** over a result, keep the session alive instead of
calling the one-shot functions: :class:`AoiSession` and :class:`LayersSession` expose
``ask(query)`` and retain the conversation across turns (the one-shot functions are
thin wrappers that open a session and ask once). For an HTTP frontend, the
:func:`open_aoi_session` / :func:`open_layers_session` / :func:`session_ask` /
:func:`close_session` registry hands out an opaque ``session_id`` so the client holds
no state. In a terminal, ``geodatacatalog aoi/fetch-aoi --repl`` drives a session
interactively (see :func:`_repl`).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from .ai import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_MISTRAL_MODEL,
    _aoi_from_geojson,
    _Assistant,
    pick_provider,
)
from .geocode import recommended_coverages
from .registry import Source, load_registry

_AOI_SYSTEM = """You resolve an AREA OF INTEREST (AOI) for a geospatial backend.
Given the user's area description, end by calling `propose_aoi` with 2-6 candidates;
that is your goal. You MAY first call `list_layers`/`discover` to find the right data
layer for a named feature. Rules:
- Town / department / region / country → kind='place' (pass `query`).
- River / watershed / lake / sea / EEZ → kind='feature' on the right data layer, with
  `name` (the feature name) and, for a regional feature, `place_hint` (region to scope
  the search). For a sea/EEZ no place_hint is needed (searched worldwide). Examples:
  a sea → marine_regions 'MarineRegions:goas'/'MarineRegions:iho' name='Mediterranean';
  an EEZ → marine_regions 'MarineRegions:eez' name='France'; a watershed → SANDRE
  'BassinVersantTopographique' name=<river>. Approximate layer names are auto-resolved.
- Vague / large NATURAL area (mountain range, massif, coastline — "les Pyrénées",
  "the Alps"): Nominatim returns only a thin centre-line or an over-broad region, which
  is useless as a polygon. Propose the named area PLUS 3-5 concrete sub-areas as
  kind='place' (the départements/regions it spans, a national/natural park) so the user
  gets usable polygons — e.g. Pyrénées: Pyrénées-Atlantiques, Hautes-Pyrénées,
  Pyrénées-Orientales, "Parc national des Pyrénées".
- Large diffuse area with no polygon source → kind='bbox' with bbox from your knowledge.
Offer candidates across the plausible sources so the client can choose. Call
`propose_aoi` ALONE. Do NOT write prose — just make the tool calls.
"""

_LAYERS_SYSTEM = """You select DATA LAYERS for a geospatial backend, restricted to an
AOI that is ALREADY fixed (do not call propose_aoi or geocode_place). Find the exact
layer ids with `list_layers` (and `discover` if needed) — NEVER invent ids — then end by
calling `propose_layers` with the layers grouped by theme, relevant to the user's
request. Keep only sources whose coverage fits the AOI.
The AOI is FIXED for the whole session — `propose_layers`'s optional `bbox` argument
is IGNORED here (it only applies to a session with no AOI at all), so do NOT try to
"fix" a layer that only partially covers a named feature (e.g. a river longer than the
visible area) by passing a bigger bbox turn after turn — it has no effect and each
guess drifts further from the real extent. If a named feature clearly extends beyond
the fixed AOI, just say so in the layer's `why`; the user controls the AOI, not you.
A named spectral INDEX (NDVI, NDMI, NBR, NDWI, EVI, SAVI...) or a single Sentinel-2
BAND (B02..B12, B8A) → use the `sentinelhub` source (it computes them on the fly;
productType = the index/band name). STAC collections only have RAW bands, not named
indices — do not search them for an index.
OSM features (buildings, roads, POIs, admin) over a bbox → prefer `overpass` (live,
fast); `overture_maps` is slow over S3 for a live bbox — use it only for bulk/large
areas.
A MAJOR named river/fleuve (the kind with its own Wikipedia page — the Nile, the
Amazon, the Danube...) → prefer `natural_earth_rivers` (`ne_10m_rivers_lake_centerlines
.geojson`), ONE clean English-named line per major river, no productType/name filter
needed (its geometry is simply clipped to the AOI). It does NOT cover minor/local
rivers and streams — for those, or when it has no line in the AOI, fall back to
`overpass` `waterway=river;name=<text>` (see below).
A plain `overpass` tag productType (e.g. `waterway=river`) returns EVERY feature with
that tag in the AOI, not one named feature — for a single named feature append
`;name=<text>` (case-insensitive match against `name`/`name:en`/`int_name`/`alt_name`,
e.g. `waterway=river;name=Nile`). This is BEST-EFFORT, not reliable: OSM tagging is
inconsistent across regions and even across segments of the SAME feature — a river
may be tagged only in the local script with no Latin/English variant on some
segments (verified: some Nile segments near Cairo carry only `name=نهر النيل`, no
`name:en`), so a `;name=` query can legitimately come back EMPTY for a real, present
feature. If it does, say so plainly (do not imply the feature is absent) and suggest
the plain (unfiltered) tag layer as a fallback so the user can identify it visually.
Never write a `title`/`why` claiming a layer is filtered to one named feature unless
you actually used a name filter the source supports — otherwise say plainly that it
returns every feature of that kind in the area.
Call tools only; write no prose.
"""


def _assistant(sources, model, system, interactive=False):
    provider, reason = pick_provider()
    if reason:
        raise RuntimeError(reason)
    model = model or os.environ.get(
        "GEODATACATALOG_AI_MODEL",
        DEFAULT_MISTRAL_MODEL if provider == "mistral" else DEFAULT_ANTHROPIC_MODEL,
    )
    a = _Assistant(provider, sources, model, interactive=interactive, system=system)
    a._headless_capture = True  # capture the chosen result and stop the loop
    return a


# ---------------------------------------------------------------------------
# Stateful sessions — iterate over the result in natural language
#
# Each step keeps its assistant alive so a frontend can refine the result
# ("garde que le satellite", "élargis au bassin du Nil"): the assistant's
# message history persists, so every :meth:`ask` continues the same conversation.
# The one-shot :func:`resolve_aoi` / :func:`fetch_for_aoi` below are thin wrappers
# that open a session and ask once.
# ---------------------------------------------------------------------------

class _Session:
    """A persistent NL conversation over one fixed assistant + terminal tool."""

    def __init__(self, assistant, terminal: str, empty):
        self.assistant = assistant
        self._terminal = terminal
        self._empty = empty   # () -> result returned when a turn yields nothing

    def _frame(self, query: str, first: bool) -> str:
        """Wrap the user query with any fixed context (first turn only)."""
        return query

    def ask(self, query: str) -> dict:
        """Run one NL turn; the conversation history carries over to the next."""
        first = not self.assistant.messages
        result = self.assistant.run_headless(self._frame(query, first),
                                              terminal=self._terminal)
        return result if result else self._empty()


class AoiSession(_Session):
    """Iterate on the AREA OF INTEREST by natural language.

    ``ask(query)`` returns a GeoJSON ``FeatureCollection`` of candidate AOIs
    (each feature's ``properties`` carry ``display_name``, ``source``, ``kind``,
    ``geometry_kind``, ``n_features``, ``bbox``); refine by calling ``ask`` again.
    """

    def __init__(self, *, sources: list[Source] | None = None,
                 model: str | None = None, interactive: bool = False):
        sources = sources if sources is not None else load_registry()
        a = _assistant(sources, model, _AOI_SYSTEM, interactive=interactive)
        super().__init__(a, "propose_aoi",
                         lambda: {"type": "FeatureCollection", "features": []})


class LayersSession(_Session):
    """Iterate on the DATA LAYERS over an AOI fixed at construction.

    ``aoi_geojson`` is a GeoJSON FeatureCollection / Feature / geometry (dict, JSON
    string, or path); several features are unioned. ``ask(query)`` returns
    ``{aoi, ..., layers:[...]}``; pass ``fetch=True`` / ``export_dir`` (here or
    per-turn on ``ask``) to download the in-AOI layers and fill feature counts.
    """

    def __init__(self, aoi_geojson, *, sources: list[Source] | None = None,
                 model: str | None = None, fetch: bool = False,
                 export_dir: str | None = None, interactive: bool = False):
        sources = sources if sources is not None else load_registry()
        geom, bbox, name = _aoi_from_geojson(aoi_geojson)
        a = _assistant(sources, model, _LAYERS_SYSTEM, interactive=interactive)
        a.aoi = {"display_name": name, "geometry": geom, "bbox": bbox,
                 "country_code": None, "is_target": False}
        # Derive the coverage labels the AOI falls in from its bbox, so out-of-zone
        # sources (e.g. a france-only source over an African AOI) are both excluded
        # from the result and not even probed by the model.
        a.accepted_coverage = recommended_coverages(None, bbox)
        a._headless_fetch = fetch
        a._headless_export = export_dir
        self._name = name
        self._bbox = bbox
        super().__init__(a, "propose_layers",
                         lambda: {"aoi": {"display_name": name, "bbox": list(bbox)},
                                  "layers": []})

    def _frame(self, query: str, first: bool) -> str:
        if not first:
            return query   # the fixed AOI + scope is already in the history
        scope = ""
        if self.assistant.accepted_coverage:
            scope = (f" It lies in these coverage zones: "
                     f"{sorted(self.assistant.accepted_coverage)}. Only call list_layers "
                     f"on sources whose coverage is one of these (global/multi-region "
                     f"sources always qualify); do NOT probe sources restricted to other "
                     f"regions.")
        return (f"{query}\n\n(The AOI is fixed: {self._name or 'user GeoJSON'}, "
                f"bbox {list(self._bbox)}.{scope})")

    def ask(self, query: str, *, fetch: bool | None = None,
            export_dir: str | None = None) -> dict:
        if fetch is not None:
            self.assistant._headless_fetch = fetch
        if export_dir is not None:
            self.assistant._headless_export = export_dir
        return super().ask(query)


def resolve_aoi(query: str, *, sources: list[Source] | None = None,
                model: str | None = None, interactive: bool = False) -> dict:
    """One-shot step 1: NL area description → GeoJSON ``FeatureCollection``.

    A thin wrapper over :class:`AoiSession`; use the session directly to refine the
    AOI across turns. With ``interactive=True`` the terminal picker runs
    (preview/export/confirm) and the FeatureCollection holds the single chosen AOI.
    """
    return AoiSession(sources=sources, model=model, interactive=interactive).ask(query)


def fetch_for_aoi(aoi_geojson, query: str, *, sources: list[Source] | None = None,
                  fetch: bool = False, export_dir: str | None = None,
                  model: str | None = None, interactive: bool = False) -> dict:
    """One-shot step 2: AOI GeoJSON + NL request → ``{aoi, ..., layers:[...]}``.

    A thin wrapper over :class:`LayersSession`; use the session directly to refine
    the layer selection across turns. With ``fetch=True`` (or an ``export_dir``)
    the in-AOI layers are downloaded and feature counts filled in; ``export_dir``
    also writes them to disk. With ``interactive=True`` the terminal picker runs
    and the result is the user's selection (``{selected, skipped, aoi}``).
    """
    return LayersSession(aoi_geojson, sources=sources, model=model, fetch=fetch,
                         export_dir=export_dir, interactive=interactive).ask(query)


# ---------------------------------------------------------------------------
# In-memory session registry — lets an HTTP sidecar hold only an opaque id
#
# A long-lived Python process (e.g. GeoLibre's FastAPI sidecar on 127.0.0.1:8765)
# keeps the sessions; the frontend juggles only a ``session_id``. Intended shape:
#   POST /aoi            {query}              -> {session_id, featurecollection}
#   POST /aoi/refine     {session_id, query}  -> {featurecollection}
#   POST /layers         {aoi, query}         -> {session_id, layers}
#   POST /layers/refine  {session_id, query}  -> {layers}
# No web framework is pulled in here — only stdlib.
# ---------------------------------------------------------------------------

class _Registry:
    """Thread-safe ``{session_id: session}`` store with TTL + cap eviction."""

    def __init__(self, ttl: float = 3600.0, cap: int = 128):
        self._sessions: dict[str, tuple[_Session, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._cap = cap

    def _evict(self) -> None:
        now = time.time()
        for sid in [s for s, (_, t) in self._sessions.items() if now - t > self._ttl]:
            del self._sessions[sid]
        while len(self._sessions) > self._cap:
            oldest = min(self._sessions, key=lambda s: self._sessions[s][1])
            del self._sessions[oldest]

    def add(self, session: _Session) -> str:
        sid = uuid.uuid4().hex
        with self._lock:
            self._evict()
            self._sessions[sid] = (session, time.time())
        return sid

    def get(self, session_id: str) -> _Session:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                raise KeyError(f"unknown session_id {session_id!r} (expired or closed)")
            self._sessions[session_id] = (entry[0], time.time())
            return entry[0]

    def close(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


_REGISTRY = _Registry()


def open_aoi_session(*, sources: list[Source] | None = None,
                     model: str | None = None) -> str:
    """Open an AOI session and return its opaque ``session_id``."""
    return _REGISTRY.add(AoiSession(sources=sources, model=model))


def open_layers_session(aoi_geojson, *, sources: list[Source] | None = None,
                        model: str | None = None, fetch: bool = False,
                        export_dir: str | None = None) -> str:
    """Open a layers session over ``aoi_geojson`` and return its ``session_id``."""
    return _REGISTRY.add(LayersSession(aoi_geojson, sources=sources, model=model,
                                       fetch=fetch, export_dir=export_dir))


def session_ask(session_id: str, query: str) -> dict:
    """Run one NL turn on the session identified by ``session_id``."""
    return _REGISTRY.get(session_id).ask(query)


def close_session(session_id: str) -> None:
    """Drop a session (no error if already gone)."""
    _REGISTRY.close(session_id)


# ---------------------------------------------------------------------------
# Terminal NL-REPL — drive a session interactively from the CLI
# ---------------------------------------------------------------------------

def _emit(obj: dict, path: str | None) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
        print(f"written: {path}")
    else:
        print(text)


def _print_repl(result: dict, is_layers: bool) -> None:
    if is_layers:
        layers = result.get("layers", [])
        if not layers:
            print("  (no layer)")
        for i, l in enumerate(layers, 1):
            mark = "" if l.get("in_aoi", True) else "  ⚠ out-of-AOI"
            fc = l.get("feature_count")
            cnt = f"  [{fc} feat]" if fc is not None else ""
            title = l.get("title") or l.get("layer", "")
            print(f"  {i:>2}. {l.get('source_id', '?')} · {l.get('layer', '')} — {title}{cnt}{mark}")
    else:
        feats = result.get("features", [])
        if not feats:
            print("  (no candidate)")
        for i, f in enumerate(feats, 1):
            p = f.get("properties") or {}
            bbox = p.get("bbox")
            print(f"  {i:>2}. {p.get('display_name', '?')} ({p.get('geometry_kind', '?')})"
                  + (f"  bbox={[round(c, 3) for c in bbox]}" if bbox else ""))


def _repl(session: _Session, query: str, *, output: str | None = None) -> dict:
    """Drive a session as a terminal NL loop.

    Type free text to refine; ``save [path]`` writes the current result (to
    ``[path]`` or ``output`` or stdout) and exits; ``q`` quits. A
    :class:`LayersSession` also accepts ``fetch`` and ``export <dir>``, which
    re-run the last request with downloading on.
    """
    is_layers = isinstance(session, LayersSession)
    result = session.ask(query)
    last_query = query
    _print_repl(result, is_layers)
    hint = ("  (refine in NL · 'fetch' · 'export <dir>' · 'save [path]' · 'q')"
            if is_layers else "  (refine in NL · 'save [path]' · 'q')")
    print(hint)
    while True:
        try:
            line = input("» ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        verb, _, rest = line.partition(" ")
        v = verb.lower()
        if v in ("q", "quit", "exit"):
            break
        if v in ("save", "-o", "write"):
            _emit(result, rest.strip() or output)
            return result
        if is_layers and v == "fetch":
            result = session.ask(last_query, fetch=True)
        elif is_layers and v == "export":
            result = session.ask(last_query, export_dir=rest.strip() or "out")
        else:
            last_query = line
            result = session.ask(line)
        _print_repl(result, is_layers)
    return result
