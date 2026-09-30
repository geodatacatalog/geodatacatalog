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
    ``ValueError`` for backward compatibility; callers (e.g. the HTTP sidecar)
    can catch it to return a clean client-side status instead of a 5xx.
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
# and one AI conversation re-lists the same service several times (grep retries,
# the no-match recalibration below). Cache the UNFILTERED listing per service and
# apply ``grep`` locally on top; entries expire so a long-lived sidecar still
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
                    f"{source.id.upper()}_API_KEY environment variable before launching "
                    f"the sidecar, then retry. (See the source notes for the key URL.)"
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


def remove_from_catalog(path: str | Path, provider: str,
                        products: list[str] | None = None, validate: bool = True) -> Path:
    """Remove products (or a whole provider) from an existing catalog.yml.

    The inverse of :func:`merge_into_catalog`. ``products`` are product ids (the
    ``product`` field of :func:`catalog_summary`, NOT the slugged layer id); pass
    ``None`` to drop the whole provider. A provider left with no products is
    removed entirely. The remaining file is validated with ``load_catalog`` (on a
    temp copy) before being written. Raises ``KeyError`` for an unknown
    provider/product and ``FileNotFoundError`` for a missing file.
    """
    import tempfile

    import yaml

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"catalog not found: {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if provider not in cfg:
        raise KeyError(f"provider {provider!r} not in catalog {path}")

    if products:
        prods = cfg[provider].get("products") or {}
        missing = [p for p in products if p not in prods]
        if missing:
            raise KeyError(f"product(s) {missing} not in provider {provider!r}")
        for p in products:
            prods.pop(p, None)
        if prods:
            cfg[provider]["products"] = prods
        else:
            cfg.pop(provider)        # last product removed → drop the empty provider
    else:
        cfg.pop(provider)

    text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=110)

    if validate and cfg:             # an empty catalog needs no validation
        from ..config import load_catalog
        with tempfile.TemporaryDirectory(prefix="gdc_validate_") as tmp:
            tmp_yml = Path(tmp) / "catalog.yml"
            tmp_yml.write_text(text, encoding="utf-8")
            catalog = load_catalog(tmp_yml, cache_dir=f"{tmp}/cache")
            log.info(f"validation OK — {len(catalog.layers)} layers remain after removal")

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


def new_catalog(path: str | Path, sources: list[Source] | None = None,
                overwrite: bool = False) -> Path:
    """Create a fresh, project-scoped catalog.yml — empty, or seeded from ``sources``.

    Each source in ``sources`` contributes its ``test`` layer/collection as a
    starting product (like :func:`init_catalog`); pass ``None``/``[]`` for a blank
    catalog ready to receive :func:`merge_into_catalog`. Refuses to clobber an
    existing file unless ``overwrite=True`` (raises ``FileExistsError``). Sources
    with no test layer or an unsupported type are silently skipped.
    """
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"catalog already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)

    merged: dict = {}
    for src in sources or []:
        layer = src.test.get("layer") or src.test.get("collection")
        if src.type in _CATALOG_TYPE and layer:
            merged.update(to_catalog_provider(src, [layer], resolve_descriptions=True))

    if merged:
        if path.exists():
            path.unlink()            # fresh seed — don't merge into stale content
        return merge_into_catalog(merged, path)

    path.write_text("", encoding="utf-8")    # blank catalog
    log.info(f"empty catalog written: {path}")
    return path


def list_catalogs(directory: str | Path) -> dict:
    """List candidate catalog.yml files in ``directory`` with provider/layer counts.

    Globs ``*.yml``/``*.yaml`` (non-recursive) and summarises each via
    :func:`catalog_summary`. Returns ``{dir, catalogs:[{path, name, n_providers,
    n_layers}]}``; unreadable files are skipped. Lets a frontend offer a per-project
    catalog switcher.
    """
    directory = Path(directory)
    out = []
    if directory.is_dir():
        for f in sorted(directory.glob("*.y*ml")):
            try:
                s = catalog_summary(f)
            except Exception:        # not a readable catalog — skip it
                continue
            out.append({"path": str(f), "name": f.name,
                        "n_providers": s["n_providers"], "n_layers": s["n_layers"]})
    return {"dir": str(directory), "catalogs": out}


def raster_tile_source(layer, datetime: str | None = None) -> dict | None:
    """A MapLibre raster source for a WMTS/WMS layer, or ``None`` if not tiled raster.

    Lets the frontend add the layer as live tiles (no server-side download). WMTS
    builds a KVP GetTile template assuming a Web-Mercator TileMatrixSet with z/y/x
    matrix ids (true for IGN Géoplateforme and XYZ-compatible WMTS); WMS builds an
    EPSG:3857 GetMap template using MapLibre's ``{bbox-epsg-3857}`` placeholder.
    """
    from urllib.parse import quote

    cls = type(layer).__name__
    if cls not in ("WMTSLayer", "WMSLayer", "XYZLayer"):
        return None
    if cls == "XYZLayer":
        # The layer resolves its own template (manifest frame for `datetime`,
        # e.g. RainViewer) — MapLibre-ready once the leaflet-isms are gone:
        # {s} pinned to one subdomain, {r} dropped, {-y} = TMS scheme.
        tiles = (layer.tile_url_template(datetime)
                 .replace("{s}", layer.product.subdomains[0]).replace("{r}", ""))
        if tiles.startswith("http://"):
            tiles = "https://" + tiles[len("http://"):]
        out = {"type": "raster", "tiles": [tiles],
               "tileSize": layer.product.tile_size}
        if "{-y}" in tiles:
            out["tiles"] = [tiles.replace("{-y}", "{y}")]
            out["scheme"] = "tms"
        return out
    p = layer.product
    # Force https: a secure webview (Tauri/https GeoLibre) blocks mixed http content,
    # so an http:// tile request just fails ("Load failed"). Most WMS/WMTS also serve
    # https; if a server is http-only it can't be displayed in a secure context anyway.
    url = "https://" + layer.url[len("http://"):] if layer.url.startswith("http://") else layer.url
    sep = "&" if "?" in url else "?"
    if cls == "WMTSLayer":
        # Resolve the layer's REAL advertised style + TileMatrixSet from capabilities —
        # the configured 'normal'/'PM' may be invalid (e.g. IGN LANDCOVER.CLC18's only
        # style is 'CORINE Land Cover'), which the server answers with HTTP 400.
        meta = _wmts_meta(layer)
        style = meta.pop("style", None) or p.style
        tms = meta.pop("tms", None) or p.tile_matrix_set
        q = ("SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile"
             f"&LAYER={quote(p.layer_name)}&STYLE={quote(style or '')}&TILEMATRIXSET={quote(tms)}"
             "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
             f"&FORMAT={_web_tile_format(p.fmt)}")
        tiles = f"{url}{sep}{q}"
        # _wmts_meta is best-effort: when the capabilities lookup fails (huge
        # documents time out — NASA GIBS serves ~10 MB) the URL silently falls
        # back to the configured style/TMS, which may be plain wrong for this
        # server (PM/normal are IGN defaults) and die as opaque per-tile HTTP
        # 400s on the map. One probe turns that into a readable error.
        _probe_wmts_tile(tiles, meta)
        return {"type": "raster", "tiles": [tiles], "tileSize": 256, **meta}
    # WMSLayer
    q = ("SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
         f"&LAYERS={p.layer_name}&STYLES={p.style or ''}"
         "&CRS=EPSG:3857&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256"
         f"&FORMAT={_web_tile_format(p.img_format)}&TRANSPARENT=true"
         f"{_wms_time_param(layer, datetime)}")
    tiles = f"{url}{sep}{q}"
    _probe_wms_tile(tiles)   # catch an invalid layer/style answered with an XML
    return {"type": "raster", "tiles": [tiles], "tileSize": 256}   # ServiceException


def _wmts_meta(layer) -> dict:
    """``{style, tms, minzoom, maxzoom, bounds}`` for a WMTS layer from GetCapabilities.

    The tile URL must use the layer's REAL advertised style and a valid TileMatrixSet
    or the server returns HTTP 400; ``minzoom/maxzoom/bounds`` keep MapLibre from
    requesting tiles outside the layer's zoom range / coverage (also a 400). Best
    effort: any lookup failure returns ``{}`` and the URL falls back to the config.
    """
    import re

    out: dict = {}
    try:
        from owslib.wmts import WebMapTileService
        wmts = WebMapTileService(layer.url)
        tms = layer._select_tile_matrix_set(wmts)
        out["tms"] = tms
        lyr = wmts.contents[layer.product.layer_name]
        styles = getattr(lyr, "styles", None) or {}
        if styles:                              # prefer the advertised default style
            out["style"] = next((k for k, v in styles.items()
                                 if isinstance(v, dict) and v.get("isDefault")), next(iter(styles)))
        link = (getattr(lyr, "tilematrixsetlinks", {}) or {}).get(tms)
        limits = getattr(link, "tilematrixlimits", None) if link else None
        ids = []
        for k in (limits or {}):
            m = re.search(r"\d+$", str(k).split(":")[-1])
            if m:
                ids.append(int(m.group()))
        if not ids:
            ids = layer._matrix_levels(wmts, tms)
        if ids:
            out["minzoom"], out["maxzoom"] = min(ids), max(ids)
        bb = getattr(lyr, "boundingBoxWGS84", None)
        if bb and len(bb) == 4:
            out["bounds"] = [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])]
    except Exception as e:
        log.debug("[%s] WMTS meta lookup failed: %s", getattr(layer, "layer_id", "?"), e)
    return out


def _probe_wmts_tile(tiles_url: str, meta: dict | None = None) -> None:
    """Raise if a WMTS KVP GetTile URL answers a non-image (e.g. HTTP 400 for a
    style/TileMatrixSet the server does not advertise — the config fallback
    taken when the capabilities lookup failed).

    One tile at the lowest advertised zoom, centred on the layer's bounds
    (default: France, the registry's centre of mass) so the probe stays inside
    the layer's extent. Same tolerance as :func:`_probe_wms_tile`: transport
    errors are not fatal, a readable server answer is.
    """
    import re

    import requests

    from ..layers.wmts import WMTSLayer

    meta = meta or {}
    z = max(0, int(meta.get("minzoom") or 0))
    if meta.get("maxzoom") is not None:
        z = min(z, int(meta["maxzoom"]))
    lon, lat = 2.5, 46.6
    b = meta.get("bounds")
    if b and len(b) == 4:
        lon, lat = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    x, y = WMTSLayer._deg_to_tile(lon, max(-85.0, min(85.0, lat)), z)
    n = 2 ** z
    probe = (tiles_url.replace("{z}", str(z))
             .replace("{y}", str(min(n - 1, max(0, y))))
             .replace("{x}", str(min(n - 1, max(0, x)))))
    try:
        r = requests.get(probe, timeout=20, headers={"User-Agent": USER_AGENT})
    except Exception:
        return  # transport hiccup → don't block; let the client try the live tiles
    if r.status_code == 200 and "image" in r.headers.get("content-type", "").lower():
        return
    m = re.search(r"<(?:\w+:)?ExceptionText[^>]*>(.*?)</", r.text or "", re.S)
    detail = (m.group(1).strip() if m else (r.text or "").strip()[:200]) or \
             f"HTTP {r.status_code} ({r.headers.get('content-type', '?')})"
    raise ValueError(f"WMTS layer not displayable as tiles — server returned a "
                     f"non-image response: {detail[:200]}. If the service was "
                     f"temporarily unreachable the style/TileMatrixSet could not "
                     f"be verified — retry once the connection is back.")


def _probe_wms_tile(tiles_url: str) -> None:
    """Raise if a WMS GetMap tile URL returns a non-image (e.g. a ServiceException).

    Some servers answer an unknown layer/style with HTTP 200 + ``text/xml`` instead
    of an error code; the frontend then fails opaquely ("Cannot decode … in
    createImageBitmap"). One world-extent probe turns that into a clear message.
    Network/transport hiccups and non-200 codes are NOT treated as fatal (the real
    tiles may still work / be transient).
    """
    import re

    import requests

    W = 20037508.342789244
    probe = tiles_url.replace("{bbox-epsg-3857}", f"-{W},-{W},{W},{W}")
    try:
        r = requests.get(probe, timeout=20, headers={"User-Agent": USER_AGENT})
    except Exception:
        return  # transport hiccup → don't block; let the client try the live tiles
    if r.status_code == 200 and "image" in r.headers.get("content-type", "").lower():
        return
    m = re.search(r"<ServiceException[^>]*>(.*?)</ServiceException>", r.text or "", re.S)
    detail = (m.group(1).strip() if m else (r.text or "").strip()[:200]) or \
             f"HTTP {r.status_code} ({r.headers.get('content-type', '?')})"
    raise ValueError(f"WMS layer not displayable as tiles — server returned a non-image "
                     f"response: {detail[:200]}")


def _web_tile_format(fmt: str | None) -> str:
    """A browser-renderable tile format. MapLibre raster tiles must be PNG/JPEG/WebP,
    so a layer configured with a data format (e.g. image/tiff for download) is
    coerced to PNG for display."""
    f = (fmt or "").lower()
    return fmt if any(x in f for x in ("png", "jpeg", "jpg", "webp")) else "image/png"


def _latest_time(positions) -> str | None:
    """Pick a usable TIME from a WMS layer's ``timepositions``.

    For a discrete list → the last (latest) value. For a ``start/end/period``
    range → **today clamped to [start, end]**: the range end is often a far-future
    placeholder (e.g. 2099) with no data, and the start an empty archive date, so
    the realistic slice with data is around the current date (e.g. EFFIS forecasts).
    """
    if not positions:
        return None
    last = str(positions[-1]).strip()
    if "/" in last:                 # "start/end/period" range
        import datetime
        parts = last.split("/")
        start = parts[0]
        end = parts[1] if len(parts) > 1 else parts[0]
        today = datetime.date.today().isoformat()
        if start and today < start:
            return start
        if end and today > end:
            return end
        return today
    return last or None


def _wms_time_param(layer, datetime: str | None = None) -> str:
    """``&TIME=…`` for a TIME-enabled WMS layer, else ``""``.

    A static tile URL carries no per-request datetime, so a temporal layer (e.g.
    EFFIS ``ecmwf.fwi`` forecast) renders empty without TIME. But a NON-temporal
    layer (e.g. BRGM RISQUES) must never get a TIME — a globally-picked date would
    otherwise add a bogus param the server rejects. So check the time dimension
    first; only then honour the requested/configured/latest value.
    """
    configured = getattr(layer.product, "time", None)
    try:
        contents = layer._get_wms().contents.get(layer.product.layer_name)
    except Exception as e:           # capabilities unreachable → only a configured time
        log.debug("[%s] WMS time lookup skipped: %s", layer.layer_id, e)
        return f"&TIME={configured}" if configured else ""
    if contents is None:
        return f"&TIME={configured}" if configured else ""
    # owslib exposes the time dimension either as .timepositions/.defaulttimeposition
    # (older) or via .dimensions['time'] (WMS 1.3.0 Dimension elements).
    default = getattr(contents, "defaulttimeposition", None)
    positions = getattr(contents, "timepositions", None)
    tdim = (getattr(contents, "dimensions", None) or {}).get("time") or {}
    default = default or tdim.get("default")
    positions = positions or tdim.get("values")
    if not (configured or default or positions):
        return ""                    # non-temporal layer → never add TIME
    # requested date (picker) wins; else configured; else the LATEST available
    # (forecast layers default to an empty archive-start, so prefer latest).
    explicit = (datetime.split("/")[0] if datetime else None) or configured
    if explicit:
        # Snap to a time the layer actually offers — a static tile URL otherwise
        # carries a picker date the server rejects (InvalidDimensionValue), e.g. a
        # default "last 7 days" landing outside a monthly archive (EMODnet density).
        from ..layers.wms import _snap_time
        return f"&TIME={_snap_time(positions, explicit) if positions else explicit}"
    value = _latest_time(positions) or default
    return f"&TIME={value}" if value else ""


def _vector_result(data, label: str, max_features: int) -> dict:
    """Convert a fetched layer payload to a GeoJSON result, or raise for non-vector.

    Returns ``{"kind": "vector", "n_features": int, "truncated": bool,
    "geojson": FeatureCollection}`` (EPSG:4326). Raster/STAC raise ``ValueError`` —
    they can't be added via ``addGeoJsonLayer`` — so the frontend shows a message.
    """
    import json

    import geopandas as gpd

    if not isinstance(data, gpd.GeoDataFrame):
        kind = "STAC items" if isinstance(data, list) else type(data).__name__
        raise ValueError(f"{label} is a raster ({kind}), not vector — use the GeoTIFF "
                         f"button to download it (direct map display of data rasters is "
                         f"not supported yet; WMS/WMTS display as tiles)")
    gdf = data
    if gdf.crs is not None and (gdf.crs.to_epsg() or 4326) != 4326:
        gdf = gdf.to_crs(4326)
    truncated = len(gdf) > max_features
    if truncated:
        gdf = gdf.iloc[:max_features]
    # default=str: serialise Timestamp/date and any other non-JSON cell values
    return {"kind": "vector", "n_features": int(len(gdf)), "truncated": truncated,
            "geojson": json.loads(gdf.to_json(default=str))}


def _render_png(data, vmin: float | None = None, vmax: float | None = None,
                colormap: str = "viridis", max_px: int = 1024) -> dict:
    """Render a raster (xarray) → PNG bytes + EPSG:4326 corners.

    3+ bands → a true-colour RGB composite (``colormap`` ignored, ``vmin/vmax``
    null); a single band → a ``colormap`` colourisation with a 2–98% stretch.

    Returns ``{"kind": "image", "png": <bytes>, "coordinates": [TL,TR,BR,BL],
    "bounds": [w,s,e,n], "vmin", "vmax", "colormap"}``. The serve layer stores the
    bytes and hands the frontend an HTTP URL (a MapLibre image source can't reliably
    load a multi-MB ``data:`` URI in the Tauri webview). ``vmin``/``vmax`` default to
    the 2–98th percentile stretch.
    """
    import io

    import numpy as np
    import xarray as xr
    from matplotlib import colormaps, colors
    from PIL import Image

    da = data.to_array() if isinstance(data, xr.Dataset) else data
    if "time" in getattr(da, "dims", ()):        # latest acquisition
        da = da.isel(time=-1)
    band_dim = next((d for d in ("variable", "band") if d in getattr(da, "dims", ())), None)
    nbands = int(da.sizes[band_dim]) if band_dim else 1

    # 3+ bands → true-colour RGB composite (per-band 2–98% stretch, no colormap),
    # so a red/green/blue request renders as a real image, not a false-colour band.
    if band_dim and nbands >= 3:
        rgb = da.isel({band_dim: slice(0, 3)}).rio.reproject("EPSG:4326")
        left, bottom, right, top = (float(v) for v in rgb.rio.bounds())
        cube = np.asarray(rgb.values, dtype="float32")        # (3, H, W) in band order
        nodata = rgb.rio.nodata
        h, w = cube.shape[-2], cube.shape[-1]
        out = np.zeros((h, w, 4), dtype="uint8")
        bad = np.zeros((h, w), dtype=bool)
        for i in range(3):
            band = cube[i]
            m = ~np.isfinite(band)
            if nodata is not None:
                m |= band == nodata
            bad |= m
            valid = band[~m]
            if valid.size:
                lo, hi = float(np.percentile(valid, 2)), float(np.percentile(valid, 98))
                if hi <= lo:
                    hi = lo + 1e-6
                out[..., i] = (np.clip((band - lo) / (hi - lo), 0, 1) * 255).astype("uint8")
        out[..., 3] = np.where(bad, 0, 255)       # transparent where any band is nodata
        img = Image.fromarray(out, "RGBA")
        if max(img.size) > max_px:
            img.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return {"kind": "image", "png": buf.getvalue(),
                "coordinates": [[left, top], [right, top], [right, bottom], [left, bottom]],
                "bounds": [left, bottom, right, top],
                "vmin": None, "vmax": None, "colormap": "rgb"}

    if band_dim:                                  # single-band colourised render
        da = da.isel({band_dim: 0})
    da = da.rio.reproject("EPSG:4326")           # image overlay coords are lon/lat
    arr = np.asarray(da.values, dtype="float32")
    arr = arr.reshape(arr.shape[-2], arr.shape[-1])
    left, bottom, right, top = (float(v) for v in da.rio.bounds())

    mask = ~np.isfinite(arr)
    nodata = da.rio.nodata
    if nodata is not None:
        mask |= arr == nodata
    valid = arr[~mask]
    if valid.size == 0:
        raise ValueError("raster is empty on this bbox/datetime (all nodata)")
    if vmin is None:
        vmin = float(np.percentile(valid, 2))
    if vmax is None:
        vmax = float(np.percentile(valid, 98))
    if vmax <= vmin:
        vmax = vmin + 1e-6

    norm = colors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = colormaps[colormap] if colormap in colormaps else colormaps["viridis"]
    rgba = cmap(norm(np.where(mask, vmin, arr)), bytes=True)
    rgba[mask] = (0, 0, 0, 0)                     # transparent nodata
    img = Image.fromarray(rgba, "RGBA")
    if max(img.size) > max_px:                    # keep the data URI small
        img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {"kind": "image", "png": buf.getvalue(),
            "coordinates": [[left, top], [right, top], [right, bottom], [left, bottom]],
            "bounds": [left, bottom, right, top],
            "vmin": round(vmin, 6), "vmax": round(vmax, 6), "colormap": colormap}


def _request_for(bbox, datetime: str | None, aoi: dict | None,
                 bands: list[str] | None = None,
                 sort_by: str | None = None, max_items: int | None = None):
    """A GeoRequest from a bbox, or from an AOI GeoJSON (dissolved → bbox + clip).

    ``aoi`` is a GeoJSON FeatureCollection / Feature / geometry; its features are
    unioned into one AOI polygon so fetches are clipped to it (not just its bbox).
    ``bands`` (STAC spectral bands) is forwarded for STAC layers. ``sort_by``
    (``date`` | ``cloud_cover``) and ``max_items`` (cap on STAC items fetched) are
    STAC knobs forwarded onto the request.
    """
    from ..request import GeoRequest

    extra: dict = {}
    if sort_by:
        extra["sort_by"] = sort_by
    if max_items:
        extra["max_features"] = max_items

    if aoi:
        import geopandas as gpd
        from shapely.geometry import shape
        from shapely.ops import unary_union

        if isinstance(aoi, dict) and aoi.get("type") == "FeatureCollection":
            geoms = [shape(f["geometry"]) for f in aoi.get("features", []) if f.get("geometry")]
        elif isinstance(aoi, dict) and aoi.get("type") == "Feature":
            geoms = [shape(aoi["geometry"])] if aoi.get("geometry") else []
        else:
            geoms = [shape(aoi)]                 # bare geometry
        geoms = [g for g in geoms if g and not g.is_empty]
        if geoms:
            gdf = gpd.GeoDataFrame(geometry=[unary_union(geoms)], crs="EPSG:4326")
            return GeoRequest(aoi_gdf=gdf, datetime=datetime, bands=bands, **extra)
    return GeoRequest(bbox=tuple(bbox), datetime=datetime, bands=bands, **extra)


def _display_result(data, label: str, max_features: int,
                    vmin: float | None, vmax: float | None, colormap: str) -> dict:
    """Vector → GeoJSON; raster (xarray) → a colourised image overlay."""
    import xarray as xr

    if isinstance(data, (xr.DataArray, xr.Dataset)):
        return _render_png(data, vmin, vmax, colormap)
    return _vector_result(data, label, max_features)


def fetch_layer_geojson(source: Source, layer_name: str,
                        bbox: tuple[float, float, float, float],
                        datetime: str | None = None, max_features: int = 2000,
                        vmin: float | None = None, vmax: float | None = None,
                        colormap: str = "viridis", aoi: dict | None = None,
                        bands: list[str] | None = None,
                        sort_by: str | None = None, max_items: int | None = None) -> dict:
    """Display a REGISTRY source layer on a bbox: WMS/WMTS → tiles, vector → GeoJSON,
    data raster (WCS/STAC/plugin) → a colourised image overlay (used by the explorer).
    """
    import tempfile

    from ..catalog import GeoDataCatalog
    from ..request import GeoRequest

    layer = _build_layer(source, layer_name)
    tiles = raster_tile_source(layer, datetime)
    if tiles is not None:                       # WMTS/WMS → live tiles, no download
        return {"kind": "raster", "source": tiles}
    catalog = GeoDataCatalog(layers=[layer], cache_dir=tempfile.mkdtemp(prefix="gdc_fetch_"))
    results = catalog.fetch(_request_for(bbox, datetime, aoi, bands, sort_by, max_items),
                            no_cache=True)
    label = f"[{source.id}] {layer_name!r}"
    zarr = _zarr_passthrough(results, layer.layer_id, bands, bbox)
    if zarr is not None:                         # Zarr cube → let the client render it
        return zarr
    return _display_result(_load_if_stac(results, layer.layer_id, label),
                           label, max_features, vmin, vmax, colormap)


def _missing_catalog_layer(catalog_path: str | Path, layer_id: str) -> Exception:
    """Explain why a catalog layer_id is absent from the loaded catalog.

    ``catalog_summary`` lists every product in the YAML, but ``load_catalog`` skips
    providers whose ``need_auth`` credentials are missing — so a listed layer can be
    unfetchable. Distinguish that (clear ValueError naming the env vars) from a truly
    unknown id (KeyError → 404).
    """
    import re

    import yaml

    provider = layer_id.split("__", 1)[0]
    try:
        cfg = yaml.safe_load(Path(catalog_path).read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    block = cfg.get(provider)
    if not isinstance(block, dict):
        return KeyError(layer_id)                # provider not in the file at all
    env_vars = sorted(set(re.findall(r"\$\{(\w+)", yaml.safe_dump(block))))
    hint = f" — set the env var(s): {', '.join(env_vars)}" if env_vars else ""
    return ValueError(
        f"layer {layer_id!r} is declared in catalog.yml but did not load — most likely "
        f"missing credentials for provider {provider!r}{hint}; set them and restart the "
        f"sidecar.")


def fetch_catalog_layer_geojson(catalog_path: str | Path, layer_id: str,
                                bbox: tuple[float, float, float, float],
                                datetime: str | None = None, max_features: int = 2000,
                                vmin: float | None = None, vmax: float | None = None,
                                colormap: str = "viridis", aoi: dict | None = None,
                                bands: list[str] | None = None,
                                sort_by: str | None = None, max_items: int | None = None) -> dict:
    """Display a catalog.yml layer by ``layer_id``: WMS/WMTS → tiles, vector → GeoJSON,
    data raster (WCS/STAC/plugin) → a colourised image overlay.

    Uses the catalog's own provider/product config (any provider name). Raises
    ``KeyError`` if the layer id is not in the catalog.
    """
    from ..config import load_catalog
    from ..request import GeoRequest

    catalog = load_catalog(catalog_path)
    if layer_id not in catalog.layers:
        raise _missing_catalog_layer(catalog_path, layer_id)
    tiles = raster_tile_source(catalog.layers[layer_id], datetime)
    if tiles is not None:                       # WMTS/WMS → live tiles, no download
        return {"kind": "raster", "source": tiles}
    results = catalog.fetch(_request_for(bbox, datetime, aoi, bands, sort_by, max_items),
                            layers=[layer_id], no_cache=True)
    zarr = _zarr_passthrough(results, layer_id, bands, bbox)
    if zarr is not None:                         # Zarr cube → let the client render it
        return zarr
    return _display_result(_load_if_stac(results, layer_id, f"layer {layer_id!r}"),
                           f"layer {layer_id!r}", max_features, vmin, vmax, colormap)


def _zarr_passthrough(results, layer_id: str, bands: list[str] | None,
                      bbox: tuple | None) -> dict | None:
    """Return a ``kind: "zarr"`` payload when the STAC layer serves Zarr cubes.

    Zarr collections (e.g. era5-pds, one asset per variable) aren't rendered
    server-side — the frontend's "Add layer → Zarr" loads the store directly. Only
    STAC item lists can carry Zarr assets; anything else falls through to the normal
    raster/vector display path.
    """
    from ..layers.stac import zarr_passthrough

    data = results[layer_id]
    if not isinstance(data, list) or not data:
        return None
    return zarr_passthrough(data, bands, bbox)


def _load_if_stac(results, layer_id: str, label: str):
    """Return the layer payload, loading STAC items (list) into a raster first."""
    data = results[layer_id]
    if data is None:                            # the catalog stores None for a layer
        err = getattr(results, "errors", {}).get(layer_id)  # whose fetch raised
        if err:
            raise ValueError(f"{label}: {err}")
        raise ValueError(
            f"{label}: the fetch returned no data — the source likely failed, timed out, "
            f"or rate-limited (see the sidecar logs). Retry; if it persists the service "
            f"may be down or the request too large (zoom in / smaller AOI).")
    if isinstance(data, list):                  # STAC items → mosaic into xarray
        if not data:
            raise ValueError(f"{label}: no STAC items for this area — the AOI is likely "
                             f"outside the dataset's coverage, or it has no data here. "
                             f"Try an AOI within the dataset's region.")
        data = results.load(layer_id)[layer_id]
    return data


def _geotiff_bytes(data, label: str) -> bytes:
    """Serialise a fetched RASTER payload (xarray) to GeoTIFF bytes, else raise."""
    import os
    import tempfile

    import xarray as xr

    if not isinstance(data, (xr.DataArray, xr.Dataset)):
        kind = "STAC items" if isinstance(data, list) else type(data).__name__
        raise ValueError(f"{label} is not a raster ({kind}); GeoTIFF download is for "
                         f"raster layers (WMS/WMTS/WCS/STAC/COG)")
    if "time" in getattr(data, "dims", ()):     # STAC stacks → latest acquisition
        data = data.isel(time=-1)
    fd, tmp = tempfile.mkstemp(suffix=".tif", prefix="gdc_tif_")
    os.close(fd)
    try:
        # Write a tiled GeoTIFF (+ overviews) — a COG. GeoLibre / geotiff.js read
        # rasters through a COG reader that requires internal tiling (a striped
        # GeoTIFF fails with a tileWidth/tileSize error and won't display).
        try:
            import rasterio
            from rasterio.enums import Resampling

            data.rio.to_raster(tmp, tiled=True, blockxsize=256, blockysize=256,
                               compress="deflate")
            with rasterio.open(tmp, "r+") as dst:
                factors = [f for f in (2, 4, 8, 16) if min(dst.width, dst.height) // f >= 2]
                if factors:
                    dst.build_overviews(factors, Resampling.average)
        except Exception as e:                  # tiny rasters / driver quirks → plain
            log.debug("[%s] COG write fell back to plain GeoTIFF: %s", label, e)
            data.rio.to_raster(tmp)
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(tmp)


def fetch_layer_geotiff(source: Source, layer_name: str,
                        bbox: tuple[float, float, float, float],
                        datetime: str | None = None, aoi: dict | None = None,
                        bands: list[str] | None = None,
                        sort_by: str | None = None, max_items: int | None = None) -> bytes:
    """Fetch a RASTER layer of a REGISTRY source on a bbox → raw GeoTIFF bytes."""
    import tempfile

    from ..catalog import GeoDataCatalog
    from ..request import GeoRequest

    layer = _build_layer(source, layer_name)
    catalog = GeoDataCatalog(layers=[layer], cache_dir=tempfile.mkdtemp(prefix="gdc_tif_"))
    results = catalog.fetch(_request_for(bbox, datetime, aoi, bands, sort_by, max_items),
                            no_cache=True)
    label = f"[{source.id}] {layer_name!r}"
    return _geotiff_bytes(_load_if_stac(results, layer.layer_id, label), label)


def fetch_catalog_layer_geotiff(catalog_path: str | Path, layer_id: str,
                                bbox: tuple[float, float, float, float],
                                datetime: str | None = None, aoi: dict | None = None,
                                bands: list[str] | None = None,
                                sort_by: str | None = None, max_items: int | None = None) -> bytes:
    """Fetch a RASTER layer FROM the user's catalog.yml by ``layer_id`` → GeoTIFF bytes."""
    from ..config import load_catalog
    from ..request import GeoRequest

    catalog = load_catalog(catalog_path)
    if layer_id not in catalog.layers:
        raise _missing_catalog_layer(catalog_path, layer_id)
    results = catalog.fetch(_request_for(bbox, datetime, aoi, bands, sort_by, max_items),
                            layers=[layer_id], no_cache=True)
    return _geotiff_bytes(_load_if_stac(results, layer_id, f"layer {layer_id!r}"),
                          f"layer {layer_id!r}")


def _download_assets(layer, results, request, asset_keys: list[str] | None,
                     label: str) -> tuple[str, str, bytes]:
    """Save the selected non-raster STAC asset(s) → ``(filename, media_type, bytes)``.

    The counterpart of the ``downloads`` list from :func:`_downloadable_assets`: it
    streams the original file (a zipped ``.hgt``, a NetCDF cube, a GeoParquet table)
    the raster path can't display. A single asset is returned as-is; several are
    bundled into a ``.zip``. Only STAC layers expose per-asset files. The layer's
    own auth (Earthdata bearer token, Azure SAS, S3 creds) is used, so credential-
    gated assets download server-side even when the browser couldn't fetch them.
    """
    import io
    import tempfile
    import zipfile

    from ..layers import STACLayer

    if not isinstance(layer, STACLayer):
        raise ValueError(f"{label}: asset download is only available for STAC layers")
    items = results[layer.layer_id]
    if not items:
        raise ValueError(f"{label}: no items for this area/time to download.")
    with tempfile.TemporaryDirectory(prefix="gdc_dl_") as tmp:
        paths = layer.download(items, request, tmp, bands=asset_keys)
        if len(paths) == 1:
            return paths[0].name, "application/octet-stream", paths[0].read_bytes()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                zf.write(p, arcname=p.name)
        return f"{_slug(layer.layer_id)}.zip", "application/zip", buf.getvalue()


def download_layer_assets(source: Source, layer_name: str,
                          bbox: tuple[float, float, float, float],
                          datetime: str | None = None, asset_keys: list[str] | None = None,
                          aoi: dict | None = None, sort_by: str | None = None,
                          max_items: int | None = None) -> tuple[str, str, bytes]:
    """Download raw asset file(s) of a REGISTRY STAC layer → ``(filename, media, bytes)``."""
    import tempfile

    from ..catalog import GeoDataCatalog

    layer = _build_layer(source, layer_name)
    catalog = GeoDataCatalog(layers=[layer], cache_dir=tempfile.mkdtemp(prefix="gdc_dl_"))
    request = _request_for(bbox, datetime, aoi, asset_keys, sort_by, max_items)
    results = catalog.fetch(request, no_cache=True)
    return _download_assets(layer, results, request, asset_keys, f"[{source.id}] {layer_name!r}")


def download_catalog_layer_assets(catalog_path: str | Path, layer_id: str,
                                  bbox: tuple[float, float, float, float],
                                  datetime: str | None = None,
                                  asset_keys: list[str] | None = None, aoi: dict | None = None,
                                  sort_by: str | None = None,
                                  max_items: int | None = None) -> tuple[str, str, bytes]:
    """Download raw asset file(s) of a catalog.yml STAC layer → ``(filename, media, bytes)``."""
    from ..config import load_catalog

    catalog = load_catalog(catalog_path)
    if layer_id not in catalog.layers:
        raise _missing_catalog_layer(catalog_path, layer_id)
    request = _request_for(bbox, datetime, aoi, asset_keys, sort_by, max_items)
    results = catalog.fetch(request, layers=[layer_id], no_cache=True)
    return _download_assets(catalog.layers[layer_id], results, request, asset_keys,
                            f"layer {layer_id!r}")


#: Asset keys that are not selectable spectral bands (previews, metadata, the
#: pre-rendered true-colour composite, etc.).
_NON_BAND_ASSETS = ("thumbnail", "metadata", "mtd", "overview", "tilejson",
                    "rendered_preview", "preview", "granule", "safe_manifest", "visual")


def _selectable_raster_bands(item) -> list[str]:
    """Asset keys of a STAC item that are selectable raster bands.

    Excludes helper assets (browse/thumbnail/tilejson/metadata) and any asset whose
    format isn't a readable raster (NetCDF/HDF/Zarr/GeoParquet/GeoJSON/archive), so
    the band picker never offers e.g. NASADEM's protected ``.zip`` data asset as a
    'band' (which odc.stac then rejects with ``No such band/alias``). Falls back to
    the plausibly-real assets when the role/media-type heuristic matches nothing —
    never the whole asset list (that would re-add the junk assets).
    """
    from ..layers.stac import _is_helper_asset, _nongeoref_label

    bands = []
    for key, asset in item.assets.items():
        if _is_helper_asset(asset) or any(s in key.lower() for s in _NON_BAND_ASSETS):
            continue
        if _nongeoref_label(asset):        # non-raster format (nc/hdf/zip/parquet/…)
            continue
        roles = getattr(asset, "roles", None) or []
        mt = (getattr(asset, "media_type", None) or "").lower()
        if "data" in roles or "tiff" in mt or "cog" in mt or not roles:
            bands.append(key)
    if bands:
        return bands
    return [k for k, a in item.assets.items()
            if not _is_helper_asset(a) and not _nongeoref_label(a)]


def _downloadable_assets(item, displayable: set[str]) -> list[dict]:
    """Data assets that can't be *displayed* as a raster (non-COG/Zarr) but are worth
    offering as a **download** — a NetCDF/HDF cube, a GeoParquet table, a zipped
    archive (NASADEM's protected ``.hgt.zip``).

    Each entry carries what a frontend needs to show a labelled download link:
    ``{key, href, ext, format, title, roles}``. Displayable raster bands (already in
    ``displayable``), Zarr assets (handled by the client's Zarr loader) and helper
    sidecars (browse/thumbnail/metadata — which ``STACLayer.download`` refuses
    anyway, so they'd 404) are skipped. ``format`` is the recognised label
    (``NetCDF``, ``archived (.zip)``…) else the bare extension or the media type.
    """
    from ..layers.stac import (
        _asset_ext,
        _asset_mt,
        _is_helper_asset,
        _is_zarr_asset,
        _nongeoref_label,
    )

    out = []
    for key, a in item.assets.items():
        if key in displayable or _is_zarr_asset(a) or _is_helper_asset(a):
            continue
        href = getattr(a, "href", "") or ""
        # Only browser-downloadable assets; drop cloud-native schemes (s3://, abfs://)
        # which are usually a duplicate of an http(s) sibling and can't be fetched
        # directly by the frontend anyway.
        if not href.lower().startswith(("http://", "https://")):
            continue
        ext = _asset_ext(a)
        fmt = _nongeoref_label(a) or (ext.lstrip(".").upper() if ext
                                      else (_asset_mt(a) or "file"))
        out.append({
            "key": key,
            "href": getattr(a, "href", None),
            "ext": ext or None,
            "format": fmt,
            "title": getattr(a, "title", None) or key,
            "roles": list(getattr(a, "roles", None) or []),
        })
    # The real data assets first, browse/metadata sidecars after.
    out.sort(key=lambda d: 0 if "data" in d["roles"] else 1)
    return out


def _stac_bands_for(url: str, collection: str, headers: dict | None = None) -> dict:
    """Available selectable bands of a STAC collection + a suggested RGB default.

    Reads one sample item's assets (one cheap search), keeps data/raster assets,
    and detects the red/green/blue triple via :func:`_rgb_band_default`. Returns
    ``{"bands": [...], "rgb": [...] | None, "downloads": [...]}`` — ``bands`` are
    displayable (COG) raster bands, ``downloads`` the non-raster assets a frontend
    can offer for download instead (see :func:`_downloadable_assets`).
    """
    import pystac_client

    from ..layers.stac import _rgb_band_default

    cat = pystac_client.Client.open(url.rstrip("/").removesuffix("/search"),
                                    headers={"User-Agent": USER_AGENT, **(headers or {})})
    items = list(cat.search(collections=[collection], max_items=1).items())
    if not items:
        return {"bands": [], "rgb": None, "downloads": []}
    item = items[0]
    bands = _selectable_raster_bands(item)
    return {"bands": bands, "rgb": _rgb_band_default(items),
            "downloads": _downloadable_assets(item, set(bands))}


def stac_bands(source: Source, layer_name: str) -> dict:
    """Selectable bands + RGB default for a REGISTRY STAC layer (see _stac_bands_for)."""
    from ..layers import STACLayer

    obj = _build_layer(source, layer_name)
    if not isinstance(obj, STACLayer):
        raise ValueError(f"{layer_name!r} is not a STAC layer")
    return _stac_bands_for(obj.url, obj.product.collection, getattr(obj.auth, "headers", None))


def catalog_stac_bands(catalog_path: str | Path, layer_id: str) -> dict:
    """Selectable bands + RGB default for a catalog.yml STAC layer by ``layer_id``."""
    from ..config import load_catalog
    from ..layers import STACLayer

    catalog = load_catalog(catalog_path)
    if layer_id not in catalog.layers:
        raise _missing_catalog_layer(catalog_path, layer_id)
    obj = catalog.layers[layer_id]
    if not isinstance(obj, STACLayer):
        raise ValueError(f"layer {layer_id!r} is not a STAC layer")
    return _stac_bands_for(obj.url, obj.product.collection, getattr(obj.auth, "headers", None))


def catalog_summary(path: str | Path) -> dict:
    """Read a user's catalog.yml into a frontend-friendly provider/layer listing.

    Parses the YAML directly (no fetching/building) → ``{path, providers:[{name,
    type, layers:[{id, product, productType}]}]}``; layer ids match what
    ``catalog.fetch`` exposes (``{provider}__{slug(product)}``). Returns an empty
    provider list for a missing/empty file.
    """
    import re

    import yaml

    path = Path(path)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    providers = []
    for name, block in (cfg or {}).items():
        if not isinstance(block, dict):
            continue
        search = block.get("search") or {}
        stype = search.get("type", "")
        endpoint = search.get("api_endpoint") or search.get("s3_endpoint") or ""
        # needs credentials: explicit need_auth, or any ${ENV_VAR} placeholder (auth block)
        needs_auth = bool(search.get("need_auth")) or \
            bool(re.search(r"\$\{", yaml.safe_dump(block)))
        from .search import kind_for, temporal_for
        layers = [{"id": f"{name}__{_slug(pid)}", "product": pid,
                   "productType": (prod or {}).get("productType"),
                   "description": (prod or {}).get("description"),
                   # configured categorical legend {hex_color: label}, if any
                   "legend_classes": (prod or {}).get("legend") or None,
                   # True/False/None: is the chosen date sent in the request? (merged
                   # search+product config — `static` is per-product, `datetimeParam`
                   # either; WMS/WCS stay None as it depends on the server's TIME dim)
                   "sends_datetime": temporal_for(stype, {**search, **(prod or {})})}
                  for pid, prod in (block.get("products") or {}).items()]
        providers.append({"name": name, "type": stype, "kind": kind_for(stype),
                          "needs_auth": needs_auth, "endpoint": endpoint, "layers": layers})
    n_layers = sum(len(p["layers"]) for p in providers)
    return {"path": str(path), "n_providers": len(providers),
            "n_layers": n_layers, "providers": providers}
