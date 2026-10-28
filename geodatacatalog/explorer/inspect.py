"""Inspect a registered source: list its layers, preview one, generate catalog.yml.

Reuses the geodatacatalog layer classes for previews, so what you see is
exactly what ``catalog.fetch()`` will return.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from .probe import USER_AGENT
from .registry import Source

log = logging.getLogger(__name__)

#: registry source type → catalog.yml search.type
_CATALOG_TYPE = {"stac": "StacSearch", "wfs": "WFS", "wms": "WMS",
                 "wcs": "WCS", "wmts": "WMTS", "rest": "REST", "xyz": "XYZ"}


class NotEnumerable(ValueError):
    """A source's layers can't be listed — an expected limitation, not a failure.

    Raised for REST sources that take a free-form query and expose no curated
    ``layers`` list, plugin product list, nor OpenAPI document. Subclasses
    ``ValueError`` so callers can catch it to report a clean limitation rather
    than a hard failure.
    """


def _is_meta_catalogue(source: Source) -> bool:
    """A discovery portal that indexes OTHER services (keyword-searchable).

    CSW services and open-data platforms / dataset lists (``discover:`` config).
    A bare ``type: registry`` with none of these is a *download* dataset, not a
    meta-catalogue — see :func:`access_hint`.
    """
    return (source.type == "csw" or bool(source.discover.get("api"))
            or bool(source.discover.get("dataset_list")))


def access_hint(source: Source) -> str:
    """How to actually get data from a source that has no drillable layers.

    Registry sources split in two: meta-catalogues (search with ``discover``) and
    download/portal datasets like Köppen-Geiger (grab the file, load it locally).
    Reused by both the ``layers`` error and the ``show`` command so the guidance
    stays consistent.
    """
    if _is_meta_catalogue(source):
        follow = (
            "use the returned id with the provider's own client library"
            if source.discover.get("dataset_list") else
            "add the service it returns: `geodatacatalog add <name> <layer> "
            "--type <WFS|WMS|STAC|...> --endpoint <url>`"
        )
        return (
            f"[{source.id}] is a meta-catalogue (a discovery portal that indexes "
            f"OTHER services). Keyword-search it with "
            f'`geodatacatalog discover {source.id} "<keywords>"`, then {follow}.'
        )
    if source.type == "registry":
        return (
            f"[{source.id}] is a download dataset — no live tile/query API, so it "
            f"has no layers to list, fetch or add directly. Download the file(s) "
            f"from {source.endpoint} (see the notes below), then load them locally, "
            f"clipped to your AOI — either a `search.type: local` entry in a "
            f"catalog.yml (LocalLayer), or directly with rioxarray/geopandas."
        )
    if source.type == "xyz":
        return (
            f"[{source.id}] is a tile template — the endpoint itself is the layer. "
            f"Add it to a catalog with `geodatacatalog add {source.id} tiles -o "
            f"catalog.yml` (search.type: XYZ — fetches mosaic the tiles into a "
            f"GeoTIFF), or drape {source.endpoint} directly as an XYZ tile layer "
            f"in your map/GIS."
        )
    return (
        f"[{source.id}] can't be enumerated automatically (type={source.type!r}) "
        f"— open {source.endpoint} in a browser."
    )


# ---------------------------------------------------------------------------
# Layer listing
# ---------------------------------------------------------------------------

def _curated_layers(source: Source) -> list[dict]:
    """Normalise the registry ``layers`` field to [{id, title}, ...]."""
    out = []
    for entry in source.layers:
        if isinstance(entry, str):
            out.append({"id": entry, "title": ""})
        else:
            out.append({"id": entry["id"], "title": entry.get("title", "")})
    return out


def _plugin_products(source: Source) -> list[dict] | None:
    """If the source's plugin module exposes ``list_products()``, its product list.

    Lets a free-form plugin (e.g. sentinelhub) enumerate every productType it
    accepts. Returns ``None`` when there is no plugin or no ``list_products``.
    """
    plugin = source.catalog.get("plugin")
    if not plugin:
        return None
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "layers" / "plugins" / plugin
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, "list_products", None)
        return fn() if callable(fn) else None
    except Exception as e:
        log.debug("[%s] list_products failed: %s", source.id, e)
        return None


def _openapi_layers(source: Source) -> list[dict]:
    """List GET paths from OpenAPI/Swagger documents.

    Tries explicit spec URL(s) from ``test.openapi`` first (specs are often
    hosted away from the API, e.g. GBIF's live on techdocs.gbif.org), then
    falls back to conventional locations under the endpoint.
    """
    import requests

    explicit = source.test.get("openapi") or []
    if isinstance(explicit, str):
        explicit = [explicit]
    ep = source.endpoint.rstrip("/")
    candidates = list(explicit) + [ep + p for p in ("/openapi.json", "/swagger.json", "/api-docs")]

    layers: list[dict] = []
    seen: set[str] = set()
    for url in candidates:
        try:
            resp = requests.get(url, timeout=15,
                                 headers={"Accept": "application/json", "User-Agent": USER_AGENT})
            if resp.status_code != 200:
                continue
            spec = resp.json()
        except Exception:
            continue
        if not isinstance(spec, dict):
            # Some endpoints (e.g. psfdf's Cloudflare Worker) answer HTTP 200 on
            # any path with their own payload instead of 404ing — not an OpenAPI
            # document, so skip it rather than crash on spec.get(...).
            continue
        found = 0
        for path, ops in (spec.get("paths") or {}).items():
            if not (isinstance(ops, dict) and "get" in ops):
                continue
            pid = path.lstrip("/")
            if pid in seen:
                continue
            seen.add(pid)
            layers.append({"id": pid, "title": (ops.get("get") or {}).get("summary", "")})
            found += 1
        if found:
            log.info(f"[{source.id}] {found} routes discovered via {url}")
        if found and not explicit:
            break  # one conventional spec is enough; explicit lists are all read
    return layers


def _grep_layers(layers: list[dict], grep: str | None) -> list[dict]:
    if not grep:
        return layers
    import re
    terms = [t for t in re.split(r"[|,\s]+", grep.lower()) if t]
    return [l for l in layers
            if any(t in l["id"].lower() or t in l["title"].lower() for t in terms)]


def _stac_sends_datetime(temporal: list | None) -> bool | None:
    """Whether picking a date meaningfully filters a STAC collection (for the symbol).

    A STAC *search* always sends a datetime window, but for a single-epoch product
    (a DEM, an annual land-cover mosaic) the chosen date doesn't change the result —
    only a true time series does. The discriminator is the collection's temporal
    extent ``[start, end]``: an **open-ended** extent (``end`` is ``None``) means
    ongoing acquisitions → a real time series (``True``, 🕒); a **closed** extent is
    typically a finite/single-epoch product (``False``, 📌); an absent extent is
    unknown (``None``, 🕒?). The curated ``static: true`` flag in catalog.yml is the
    authoritative override (honoured by the catalog view via ``temporal_for``)."""
    if not temporal or len(temporal) < 2:
        return None                       # no temporal extent advertised → unknown
    return temporal[1] is None            # open-ended end → ongoing time series


def _stac_collection_meta(c) -> dict:
    """Frontend-friendly metadata for a STAC collection (for the info panel).

    Many catalogues (e.g. DEA Africa) leave ``title`` equal to the id but populate
    ``description`` — so surface description/license/temporal extent, not just the id.
    """
    temporal = None
    try:
        iv = c.extent.temporal.intervals[0]
        temporal = [d.date().isoformat() if d else None for d in iv]
    except Exception:
        pass
    return {
        "id": c.id,
        "title": c.title or "",
        "description": c.description or "",
        "license": getattr(c, "license", None) or None,
        "temporal": temporal,
        # 🕒 ongoing series / 📌 single-epoch / 🕒? unknown — from the temporal extent
        "sends_datetime": _stac_sends_datetime(temporal),
    }


def _legend_url(content) -> str | None:
    """The server-supplied legend graphic URL for a WMS/WMTS layer, or ``None``.

    OWSLib exposes a layer's styles as ``{name: {...}}``; WMS styles carry the
    LegendURL under ``legend`` and WMTS under ``legendURLs`` (or ``legend``). Returns
    the first usable one so the frontend can show the layer's legend image.
    """
    styles = getattr(content, "styles", None)
    if not isinstance(styles, dict):
        return None
    for style in styles.values():
        if not isinstance(style, dict):
            continue
        url = style.get("legend") or style.get("legendURLs")
        if url:
            return url if isinstance(url, str) else url[0]
    return None


def _ogc_is_temporal(content) -> bool:
    """True if an OWSLib OGC layer/coverage advertises a ``TIME`` dimension.

    WMS exposes it as ``.timepositions``/``.defaulttimeposition`` (older) or via
    ``.dimensions['time']`` (1.3.0 Dimension elements); WCS coverages may carry a
    time axis the same way. Lets the frontend flag a layer whose fetch forwards the
    chosen date (``WFS``/``WMTS`` contents have no time dimension → ``False``)."""
    if getattr(content, "timepositions", None) or getattr(content, "defaulttimeposition", None):
        return True
    dims = getattr(content, "dimensions", None) or {}
    return bool(dims.get("time"))


def _ogc_layer_meta(lid: str, content) -> dict:
    """Frontend-friendly metadata for an OGC layer/coverage (for the info panel).

    OWSLib content objects (WFS/WMS/WCS/WMTS) expose ``abstract``, ``keywords`` and a
    WGS84 bounding box beyond the bare title — surface them so the info panel shows a
    real description (e.g. EMODnet's Barcelona Convention abstract), not just the id.
    WMS/WMTS layers also carry a server legend graphic URL (``legend``). ``sends_datetime``
    flags a layer with a ``TIME`` dimension (the fetch forwards the chosen date).
    """
    abstract = (getattr(content, "abstract", None) or "").strip()
    keywords = [k for k in (getattr(content, "keywords", None) or []) if k]
    bbox = getattr(content, "boundingBoxWGS84", None)
    return {
        "id": lid,
        "title": (getattr(content, "title", None) or "").strip(),
        "description": abstract,
        "keywords": keywords,
        "bbox": [round(float(v), 4) for v in bbox] if bbox else None,
        "legend": _legend_url(content),
        "sends_datetime": _ogc_is_temporal(content),
    }


def _raw_temporal(c: dict) -> list | None:
    """Temporal extent ``[start, end]`` (``YYYY-MM-DD`` or ``None``) from raw JSON.

    Reads ``extent.temporal.interval[0]`` straight from the ``/collections``
    document so the fast raw path keeps the temporal info the typed path derives
    from ``c.extent.temporal``.
    """
    try:
        iv = c["extent"]["temporal"]["interval"][0]
    except (KeyError, TypeError, IndexError):
        return None
    if not isinstance(iv, (list, tuple)):
        return None
    return [d[:10] if isinstance(d, str) and d else None for d in iv]


def _raw_stac_collections(endpoint: str) -> list[dict]:
    """List STAC collections from the raw ``/collections`` document.

    The primary STAC listing path: a single (paginated via ``rel=next``) request
    that tolerates entries which don't parse, returning the same shape as
    ``_stac_collection_meta``. Used in preference to pystac_client's typed
    traversal, which on some catalogues (e.g. DEA Africa) is slow or 504s before
    yielding anything.
    """
    import requests

    url = endpoint.rstrip("/") + "/collections"
    out: list[dict] = []
    seen: set[str] = set()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for _ in range(50):  # bound pagination
        try:
            resp = requests.get(url, timeout=20, headers=headers)
            resp.raise_for_status()
            doc = resp.json()
        except Exception:
            break
        for c in doc.get("collections") or []:
            try:
                cid = c["id"]
            except (KeyError, TypeError):
                continue
            if cid in seen:
                continue
            seen.add(cid)
            out.append({
                "id": cid,
                "title": c.get("title") or "",
                "description": c.get("description") or "",
                "license": c.get("license") or None,
                "temporal": (tmp := _raw_temporal(c)),
                # 🕒 ongoing series / 📌 single-epoch / 🕒? unknown — from the temporal extent
                "sends_datetime": _stac_sends_datetime(tmp),
            })
        nxt = next((l.get("href") for l in (doc.get("links") or [])
                    if l.get("rel") == "next" and l.get("href")), None)
        if not nxt or nxt == url:
            break
        url = nxt
    return out


# Live listings are expensive (SANDRE's WFS GetCapabilities takes ~12 s per
# request, and its 2.0.0 endpoint 500s before 1.1.0 answers, doubling the cost)
# and one conversation re-lists the same service several times (grep retries,
# the no-match recalibration below). Cache the UNFILTERED listing per service and
# apply ``grep`` locally on top; entries expire so a long-lived process still
# sees capabilities changes. Shallow-copy on the way out so callers can't
# corrupt the cache.
_LISTING_TTL_S = 3600.0
_listing_cache: dict[tuple, tuple[float, list[dict]]] = {}


def _cached_listing(key: tuple, build) -> list[dict]:
    hit = _listing_cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < _LISTING_TTL_S:
        layers = hit[1]
    else:
        layers = build()
        if layers:  # don't pin a flaky server's empty answer
            _listing_cache[key] = (time.monotonic(), layers)
    return [dict(l) for l in layers]


def list_layers_from(service_type: str, endpoint: str,
                     grep: str | None = None) -> list[dict]:
    """List the layers of an OGC/STAC service by its type and endpoint.

    Works without a registry entry — used to inspect a service URL found via
    ``discover``. Returns ``[{"id": ..., "title": ...}, ...]``.
    """
    st = service_type.lower()
    layers = _cached_listing(("service", st, endpoint),
                             lambda: _live_service_layers(st, endpoint))
    return _grep_layers(layers, grep)


def _live_service_layers(st: str, endpoint: str) -> list[dict]:
    if st == "stac":
        # The raw /collections document is a single fast request that carries
        # everything we surface (id/title/description/license/temporal) and
        # tolerates entries that don't parse — so it's the primary path. The
        # typed pystac_client traversal is slow or 504s on some catalogues
        # (e.g. DEA Africa) and a single malformed collection aborts it
        # mid-stream (e.g. CNES GEODES raises STACTypeError); use it only as a
        # fallback for catalogues that don't serve /collections.
        layers = _raw_stac_collections(endpoint)
        if not layers:
            import pystac_client
            log.warning("[%s] raw /collections empty; trying typed STAC listing", endpoint)
            cat = pystac_client.Client.open(endpoint, headers={"User-Agent": USER_AGENT})
            layers = [_stac_collection_meta(c) for c in cat.get_collections()]
    elif st == "wfs":
        from owslib.wfs import WebFeatureService
        # Negotiate the version: some servers (e.g. SANDRE) 500 on 2.0.0
        # GetCapabilities but answer an older protocol version.
        wfs = None
        last_err = None
        for version in ("2.0.0", "1.1.0", "1.0.0"):
            try:
                wfs = WebFeatureService(endpoint, version=version, timeout=60)
                break
            except Exception as e:
                last_err = e
        if wfs is None:
            raise last_err
        layers = [_ogc_layer_meta(k, v) for k, v in wfs.contents.items()]
    elif st == "wms":
        from owslib.wms import WebMapService
        # Negotiate the version: many servers (e.g. IGN) reject owslib's 1.1.1
        # default with "VERSION ... have to be 1.3.0 or empty".
        wms = None
        last_err = None
        for version in ("1.3.0", "1.1.1"):
            try:
                wms = WebMapService(endpoint, version=version, timeout=60)
                break
            except Exception as e:
                last_err = e
        if wms is None:
            raise last_err
        layers = [_ogc_layer_meta(k, v) for k, v in wms.contents.items()]
    elif st == "wcs":
        from owslib.wcs import WebCoverageService
        wcs = WebCoverageService(endpoint, version="2.0.1", timeout=60)
        layers = [_ogc_layer_meta(k, v) for k, v in wcs.contents.items()]
    elif st == "wmts":
        from owslib.wmts import WebMapTileService
        wmts = WebMapTileService(endpoint)
        layers = [_ogc_layer_meta(k, v) for k, v in wmts.contents.items()]
    else:
        raise ValueError(f"layer listing is not supported for service type {st!r} "
                         f"(expected stac|wfs|wms|wcs|wmts) — open {endpoint} in a browser")
    return layers


def list_layers(source: Source, grep: str | None = None) -> list[dict]:
    """List the layers/collections of a registry source.

    OGC/STAC sources are discovered live; REST sources use the curated ``layers``
    list (falling back to OpenAPI discovery). Returns ``[{"id", "title"}, ...]``,
    optionally filtered by a case-insensitive ``grep`` (terms split on ``| , space``).
    """
    if source.layers or source.type == "rest":
        layers = _cached_listing(("source", source.id),
                                 lambda: _rest_source_layers(source))
        if not layers:
            raise NotEnumerable(
                f"[{source.id}] this REST API has no curated layer list and no "
                f"OpenAPI document, so its routes can't be enumerated. If it takes "
                f"a free-form query value (e.g. a species or place name), pass that "
                f"straight to make_catalog_entry; otherwise pick another source or "
                f"use `discover` to find one. (Maintainers: browse {source.endpoint} "
                f"and add a `layers:` section to sources.yml.)"
            )
        return _grep_layers(layers, grep)
    if source.type in ("stac", "wfs", "wms", "wcs", "wmts"):
        return list_layers_from(source.type, source.endpoint, grep)
    if source.type == "xyz":
        # A tile template has exactly one "layer": the tiles themselves. Surface
        # it so the browser offers display/add/fetch like for any other source.
        return _grep_layers([{"id": "tiles", "title": source.name}], grep)
    # registry / csw / xyz / anything non-drillable: no layers to list — explain
    # how to actually reach the data (meta-catalogue search, XYZ tile template,
    # or download-and-load-locally). NotEnumerable so HTTP callers answer 422
    # with this hint rather than a 502 server error.
    raise NotEnumerable(access_hint(source))


def _rest_source_layers(source: Source) -> list[dict]:
    # A plugin may enumerate its free-form productTypes (e.g. sentinelhub → all
    # spyndex Sentinel-2 indices + bands), beyond the curated list in sources.yml.
    products = _plugin_products(source)
    if products is not None:
        return products
    layers = _curated_layers(source)
    # OpenAPI route discovery only makes sense for a real REST API — not for a
    # plugin-backed source (e.g. sentinelhub) whose endpoint has no spec.
    if source.type == "rest" and not source.catalog.get("plugin"):
        known = {l["id"] for l in layers}
        layers += [l for l in _openapi_layers(source) if l["id"] not in known]
    return layers


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def _build_layer(source: Source, layer_name: str):
    """Build the layer object exactly as ``load_catalog`` would.

    Goes through :func:`to_catalog_provider` + :func:`load_catalog` so the source's
    ``catalog`` config (REST ``bboxParams``/``dataKey``/``geometryField``..., or a
    ``plugin``) is honoured — preview/picker then behave like the real fetch.
    """
    import tempfile
    from pathlib import Path

    import yaml

    from ..config import load_catalog

    block = to_catalog_provider(source, [layer_name])
    with tempfile.TemporaryDirectory(prefix="gdc_build_") as tmp:
        yml = Path(tmp) / "catalog.yml"
        yml.write_text(yaml.safe_dump(block, sort_keys=False, allow_unicode=True), encoding="utf-8")
        catalog = load_catalog(yml, cache_dir=f"{tmp}/cache")
        if not catalog.layers:
            if source.auth and source.auth != "none":   # need_auth, no creds in env
                raise ValueError(
                    f"[{source.id}] needs credentials (auth={source.auth}): set the "
                    f"{source.id.upper()}_API_KEY environment variable, then retry. "
                    f"(See the source notes for the key URL.)"
                )
            raise ValueError(
                f"[{source.id}] could not build layer {layer_name!r} "
                f"(unsupported type={source.type!r} for direct fetch)"
            )
        return next(iter(catalog.layers.values()))


def preview(source: Source, layer_name: str,
            bbox: tuple[float, float, float, float] = (1.40, 43.55, 1.50, 43.65),
            datetime: str | None = None,
            output_html: str | Path | None = None):
    """Fetch one layer on a small bbox and return a Folium map.

    If ``output_html`` is given the map is also saved there. Uses a throwaway
    catalog with ``no_cache=True`` so nothing is written to disk.
    """
    from ..catalog import GeoDataCatalog
    from ..request import GeoRequest
    import tempfile

    layer = _build_layer(source, layer_name)
    catalog = GeoDataCatalog(layers=[layer], cache_dir=tempfile.mkdtemp(prefix="gdc_preview_"))
    request = GeoRequest(bbox=bbox, datetime=datetime)
    results = catalog.fetch(request, no_cache=True)

    data = results[layer.layer_id]
    if isinstance(data, list) and data:   # STAC items → show footprints/thumbnails
        log.info(f"[{source.id}] {len(data)} STAC items found")
    m = results.explore()
    if m is not None and output_html:
        Path(output_html).parent.mkdir(parents=True, exist_ok=True)
        m.save(str(output_html))
        log.info(f"preview map saved: {output_html}")
    return m


# ---------------------------------------------------------------------------
# catalog.yml generation
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return s or "layer"


def _has_env_placeholder(obj) -> bool:
    """True if any string in a (possibly nested) structure holds a ``${VAR}`` placeholder."""
    if isinstance(obj, str):
        return "${" in obj
    if isinstance(obj, dict):
        return any(_has_env_placeholder(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_has_env_placeholder(v) for v in obj)
    return False


def to_catalog_provider_raw(name: str, service_type: str, endpoint: str,
                            layers: list[str], auth: str = "none",
                            catalog_extra: dict | None = None,
                            descriptions: dict[str, str] | None = None,
                            static: bool | None = None,
                            static_layers: set[str] | None = None) -> dict:
    """Build a catalog.yml provider block from raw parameters (no registry source).

    Used to turn a service discovered via :func:`discover` into a config.
    ``name`` is the provider key, ``service_type`` one of WFS/WMS/WMTS/WCS/STAC/REST.
    """
    st = service_type.lower()
    if st not in _CATALOG_TYPE:
        raise ValueError(f"service type {service_type!r} cannot be expressed in catalog.yml "
                         f"(supported: {sorted(_CATALOG_TYPE)})")
    descriptions = descriptions or {}

    catalog_extra = dict(catalog_extra or {})
    # An `auth` sub-block in the registry `catalog` config (e.g. an unsigned S3
    # region for DEA Africa) is a provider-level auth concern, not a search param.
    auth_extra = catalog_extra.pop("auth", None)
    # A source can be anonymous at the API level (`auth: none`) yet still need
    # credentials to read its data assets — cop_dataspace browses the STAC catalogue
    # freely but its eodata S3 store wants ${CDSE_S3_*} keys, carried in `catalog.auth`.
    # Flag need_auth whenever that auth block references ${ENV_VAR} placeholders (a
    # public unsigned bucket like DEA Africa's, which only pins a region, stays false),
    # so load_catalog skips-with-warning on missing creds instead of building empty keys.
    need_auth = auth != "none" or _has_env_placeholder(auth_extra)
    search: dict = {"type": _CATALOG_TYPE[st], "api_endpoint": endpoint,
                    "need_auth": need_auth}
    search.update(catalog_extra)
    provider: dict = {"search": search}

    if auth == "api_key":
        provider["auth"] = {"credentials": {"api_key": f"${{{name.upper()}_API_KEY}}"}}
    elif auth == "oauth":
        provider["auth"] = {"credentials": {"token": f"${{{name.upper()}_TOKEN}}"}}
    if auth_extra:
        provider.setdefault("auth", {}).update(auth_extra)

    is_static = static if static is not None else st in ("wfs", "wcs")
    products = {}
    for layer in layers:
        product: dict = {"productType": layer}
        if descriptions.get(layer):
            product["description"] = descriptions[layer]
        # `static_layers` (when given) decides per layer — e.g. single-epoch STAC
        # collections — overriding the type-level default.
        layer_static = (layer in static_layers) if static_layers is not None else is_static
        if layer_static:
            product["static"] = True
        products[_slug(layer)] = product
    provider["products"] = products

    return {name: provider}


def stac_static_layers(source: Source, layers: list[str]) -> set[str]:
    """STAC layer names that are single-epoch and so should be added with ``static: true``.

    A single-epoch collection (a DEM, an annual land-cover mosaic) has a *closed*
    temporal extent and isn't filtered by date, so ``static`` both keeps the temporal
    symbol consistent between the explorer and the catalog view, and lets the fetch
    skip the datetime window (and its empty-window retry). Best effort: a failed
    extent lookup returns an empty set, so the layer is simply added as temporal
    (the previous behaviour). Reuses :func:`_stac_sends_datetime` via the listing."""
    try:
        metas = list_layers_from("stac", source.endpoint)
    except Exception as e:
        log.debug("[%s] STAC extent lookup for static flag failed: %s", source.id, e)
        return set()
    by_id = {m["id"]: m for m in metas}
    return {l for l in layers if (by_id.get(l) or {}).get("sends_datetime") is False}


def _layer_descriptions(source: Source, layers: list[str]) -> dict[str, str]:
    """Resolve each layer's real description from the live service metadata.

    Maps ``layer_id -> description`` (falling back to the layer ``title``) by listing
    the source's layers/collections once. Best effort: any failure (offline, service
    down) returns ``{}`` so the caller falls back to ``source.notes``.
    """
    wanted = set(layers)
    try:
        listed = list_layers(source)
    except Exception as e:
        log.debug("[%s] layer description lookup skipped: %s", source.id, e)
        return {}
    out: dict[str, str] = {}
    for entry in listed:
        lid = entry.get("id")
        if lid in wanted:
            desc = (entry.get("description") or entry.get("title") or "").strip()
            if desc:
                out[lid] = desc
    return out


def to_catalog_provider(source: Source, layers: list[str],
                        titles: dict[str, str] | None = None,
                        *, infer_static: bool = False,
                        resolve_descriptions: bool = False) -> dict:
    """Build a catalog.yml provider block for a registry source and its layers.

    With ``infer_static`` (the 'add' path), single-epoch STAC collections are tagged
    ``static: true`` from their temporal extent — one extra ``/collections`` request,
    so it's off by default to keep the hot fetch/preview path (``_build_layer``) cheap.

    With ``resolve_descriptions`` (the persisting 'add'/'init' paths), each product's
    ``description`` is the layer's real metadata abstract/title from the live service
    rather than the generic provider-level ``notes`` — one extra listing request, so
    it's also off by default. ``titles`` (an explicit ``layer_id -> description`` map)
    always wins over both and over ``source.notes``.
    """
    if titles is None and resolve_descriptions:
        titles = _layer_descriptions(source, layers)
    titles = titles or {}
    descriptions = {l: titles.get(l) or source.notes for l in layers}
    plugin = source.catalog.get("plugin")
    if plugin:  # source handled by a shipped/local Python plugin (e.g. opensky.py)
        return _to_plugin_provider(source, plugin, layers, descriptions)
    if source.type not in _CATALOG_TYPE:
        raise ValueError(
            f"[{source.id}] type={source.type!r} cannot be expressed in catalog.yml "
            f"(supported: {sorted(_CATALOG_TYPE)})"
        )
    static_layers = (stac_static_layers(source, layers)
                     if infer_static and source.type == "stac" else None)
    return to_catalog_provider_raw(
        source.id, source.type, source.endpoint, layers,
        auth=source.auth, catalog_extra=source.catalog, descriptions=descriptions,
        static_layers=static_layers,
    )


def _to_plugin_provider(source: Source, plugin: str, layers: list[str],
                        descriptions: dict[str, str]) -> dict:
    """Provider block whose ``search.type`` is a Python plugin module name."""
    extra = {k: v for k, v in source.catalog.items() if k not in ("plugin", "credentials")}
    search = {"type": plugin, "api_endpoint": source.endpoint, "need_auth": source.auth != "none"}
    search.update(extra)
    provider: dict = {"search": search}
    # explicit `catalog.credentials` wins (e.g. Sentinel Hub OAuth client_id/secret);
    # otherwise fall back to the default api_key/token env var for the source.
    creds = source.catalog.get("credentials")
    if creds:
        provider["auth"] = {"credentials": dict(creds)}
    elif source.auth == "api_key":
        provider["auth"] = {"credentials": {"api_key": f"${{{source.id.upper()}_API_KEY}}"}}
    elif source.auth == "oauth":
        provider["auth"] = {"credentials": {"token": f"${{{source.id.upper()}_TOKEN}}"}}
    provider["products"] = {
        _slug(l): ({"productType": l, "description": descriptions[l]} if descriptions.get(l)
                   else {"productType": l})
        for l in layers
    }
    return {source.id: provider}


def merge_into_catalog(provider_block: dict, path: str | Path,
                       validate: bool = True) -> Path:
    """Create ``path`` or merge the provider block into an existing catalog.yml.

    Existing providers/products are preserved; only missing products are added.
    The resulting file is validated with :func:`geodatacatalog.load_catalog`
    before being written (validation happens on a temp copy).
    """
    import tempfile

    import yaml

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {}
    if path.exists():
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    for name, block in provider_block.items():
        if name in cfg:
            existing = cfg[name]
            existing.setdefault("products", {})
            for pid, product in block.get("products", {}).items():
                existing["products"].setdefault(pid, product)
            # Backfill a provider-level auth block the existing entry lacks (e.g. a
            # cop_dataspace added before its S3 auth was carried over), and only ever
            # promote need_auth toward true — so re-adding a layer heals a provider
            # whose data reads would otherwise fall back to ambient/empty credentials,
            # without clobbering any auth the user has customised.
            if "auth" in block and "auth" not in existing:
                existing["auth"] = block["auth"]
            if block.get("search", {}).get("need_auth") and isinstance(existing.get("search"), dict):
                existing["search"]["need_auth"] = True
        else:
            cfg[name] = block

    text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=110)

    if validate:
        from ..config import load_catalog
        with tempfile.TemporaryDirectory(prefix="gdc_validate_") as tmp:
            tmp_yml = Path(tmp) / "catalog.yml"
            tmp_yml.write_text(text, encoding="utf-8")
            catalog = load_catalog(tmp_yml, cache_dir=f"{tmp}/cache")
            log.info(f"validation OK — {len(catalog.layers)} layers load from the merged catalog")

    path.write_text(text, encoding="utf-8")
    log.info(f"catalog written: {path}")
    return path


def init_catalog(sources: list[Source], path: str | Path,
                 only_ok: bool = True) -> Path:
    """Generate a complete catalog.yml from a list of sources.

    Each source contributes its ``test`` layer/collection as a starting
    product. Sources without a known layer or with an unsupported type are
    skipped with a warning.
    """
    merged: dict = {}
    for src in sources:
        if only_ok and (src.status or {}).get("state") not in ("ok", None):
            log.warning(f"[{src.id}] skipped (status={src.status.get('state')})")
            continue
        layer = src.test.get("layer") or src.test.get("collection")
        if src.type not in _CATALOG_TYPE or not layer:
            log.warning(f"[{src.id}] skipped (no test layer or unsupported type {src.type})")
            continue
        merged.update(to_catalog_provider(src, [layer], resolve_descriptions=True))

    if not merged:
        raise ValueError("no eligible source — need type in "
                         f"{sorted(_CATALOG_TYPE)} and a test.layer/test.collection")

    return merge_into_catalog(merged, path)


