"""Keyword-search meta-catalogues: CSW services, open-data APIs, dataset lists.

Meta-catalogues index thousands of other services/datasets. ``discover`` runs
a keyword (and optional bbox) search and returns the matching records:

- ``type: csw`` (OGC Catalog Service for the Web) — live ``GetRecords`` query;
  records carry the service/download URLs, so you can then add the relevant
  WMS/WFS/STAC service as a source.
- ``discover: {api: ckan|datagouv|opendatasoft|arcgis_hub}`` — live keyword
  search of an open-data platform (the "Platform Search" pattern):
  CKAN ``package_search`` (data.europa.eu, catalog.data.gov...), the
  data.gouv.fr API, the OpenDataSoft Explore API (federated French
  collectivités), or the ArcGIS Hub search API. Records carry the dataset
  page and its resource/service URLs (WMS/WFS/ArcGIS REST/GeoJSON flagged
  ``service: true``). These APIs take no bbox — the ``bbox`` argument is
  ignored for them.
- ``discover: {dataset_list: <url>}`` — a single flattened JSON list of
  datasets, searched client-side (e.g. the Google Earth Engine data catalog);
  records carry the dataset id and its documentation page, to be used with the
  source's own client library (``ee.ImageCollection(id)``...), not fetched
  through geodatacatalog.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from .registry import Source
from .search import _normalize

log = logging.getLogger(__name__)

# URL protocols/schemes that point at a usable OGC service.
_SERVICE_HINTS = ("wms", "wfs", "wcs", "wmts", "ogc:", "esri", "arcgis", "rest")

# GeoNetwork CSW endpoints (the bulk of public catalogues — EEA, Géocatalogue,
# Sextant, INSPIRE) follow `.../srv/<lang>/csw`; their human record viewer is
# `.../srv/<lang>/catalog.search#/metadata/<id>`.
_GEONETWORK_CSW = re.compile(r"^(?P<base>.*/srv/[^/]+/)csw/?$", re.I)


def _is_service(entry: dict) -> bool:
    blob = f"{entry.get('kind', '')} {entry.get('url', '')}".lower()
    return any(h in blob for h in _SERVICE_HINTS) or "service=" in blob


def _record_page(endpoint: str, identifier: str) -> str | None:
    """Human catalogue page for a record id, or ``None`` if not derivable.

    Many CSW records (restricted, harvested or draft) come back as identifier-only
    stubs with no title/links; a link to the catalogue's own record viewer keeps
    them explorable. Only emitted for the GeoNetwork URL shape we can trust."""
    if not identifier:
        return None
    m = _GEONETWORK_CSW.match(endpoint.split("?")[0])
    return f"{m.group('base')}catalog.search#/metadata/{identifier}" if m else None


def discover(source: Source, query: str,
             bbox: tuple[float, float, float, float] | None = None,
             limit: int = 20) -> dict:
    """Keyword-search a meta-catalogue. Returns ``{matches, records}``.

    Each record is ``{identifier, title, type, abstract, page, urls:[{kind, url,
    service}]}``. ``page`` is the catalogue's human record viewer (GeoNetwork
    CSW) or the dataset's documentation page (dataset list), or ``None`` — handy
    for the identifier-only stubs some CSW catalogues return.
    """
    if source.type == "csw":
        return _discover_csw(source, query, bbox=bbox, limit=limit)
    api = source.discover.get("api")
    if api:
        backend = _API_BACKENDS.get(api)
        if backend is None:
            raise ValueError(f"[{source.id}] unknown discover api {api!r} — "
                             f"expected one of {sorted(_API_BACKENDS)}")
        return backend(source, query, limit=limit)
    if source.discover.get("dataset_list"):
        return _discover_dataset_list(source, query, limit=limit)
    raise ValueError(f"[{source.id}] is not searchable — discover needs a CSW catalogue "
                     f"(type=csw), a `discover: {{api: ckan|datagouv|opendatasoft|"
                     f"arcgis_hub}}` config, or a `discover: {{dataset_list: <url>}}` "
                     f"config (type={source.type!r})")


@lru_cache(maxsize=8)
def _fetch_dataset_list(url: str) -> tuple:
    """Download (once per process) a flattened JSON dataset list."""
    import requests

    from .probe import USER_AGENT

    log.info(f"fetching dataset list {url}")
    resp = requests.get(url, timeout=60, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    entries = resp.json()
    if not isinstance(entries, list):
        raise ValueError(f"dataset list at {url} is not a JSON array")
    return tuple(e for e in entries if isinstance(e, dict))


def _discover_dataset_list(source: Source, query: str, limit: int = 20) -> dict:
    """Keyword-search a flat JSON dataset list (``discover: {dataset_list: url}``).

    Matches normalised query words against each entry's id/title/tags/provider
    (id+title hits rank above tag-only hits). Field names follow the GEE lists
    (``gee_catalog.json`` / ``community_datasets.json``, which uses ``docs`` and
    ``thematic_group``); missing fields are just skipped.
    """
    entries = _fetch_dataset_list(source.discover["dataset_list"])
    words = [w for w in _normalize(query).split() if len(w) >= 2]
    if not words:
        return {"matches": 0, "records": []}

    scored = []
    for e in entries:
        strong = _normalize(f"{e.get('id', '')} {e.get('title', '')}")
        weak = _normalize(f"{e.get('tags', '')} {e.get('provider', '')} {e.get('thematic_group', '')}")
        # every word must hit somewhere; id/title hits rank above tag-only hits
        if any(w not in strong and w not in weak for w in words):
            continue
        score = sum(3.0 for w in words if w in strong) + sum(1.0 for w in words if w in weak)
        scored.append((score, e))
    scored.sort(key=lambda t: (-t[0], t[1].get("id", "")))

    records = []
    for _, e in scored[:limit]:
        start, end = e.get("start_date", ""), e.get("end_date", "")
        abstract = " · ".join(x for x in (e.get("provider", ""), e.get("tags", ""),
                                          f"{start}/{end}" if start or end else "") if x)
        page = e.get("asset_url") or e.get("docs") or None
        records.append({
            "identifier": e.get("id", ""),
            "title": e.get("title", ""),
            "type": e.get("type", ""),
            "abstract": abstract[:200],
            "page": page,
            # the doc page is not an OGC service endpoint → service=False; the
            # actionable handle is the `identifier` (dataset id) itself.
            "urls": [{"kind": "documentation", "url": page, "service": False}] if page else [],
        })
    log.info(f"[{source.id}] dataset-list search {query!r}: {len(scored)} match(es)")
    return {"matches": len(scored), "records": records}


def _get_json(source: Source, url: str, params: dict) -> dict:
    """One platform-API GET, JSON-decoded (shared by the ``api:`` backends)."""
    import requests

    from .probe import USER_AGENT

    log.info(f"[{source.id}] platform search {url} {params}")
    resp = requests.get(url, params=params, timeout=30,
                        headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def _text(value, langs: tuple[str, ...] = ("fr", "en")) -> str:
    """A displayable string from a possibly multilingual ``{lang: text}`` dict
    (data.europa.eu titles/descriptions)."""
    if isinstance(value, dict):
        for lang in langs:
            if value.get(lang):
                return str(value[lang])
        return str(next((v for v in value.values() if v), ""))
    return str(value or "")


def _plain(value, limit: int = 200) -> str:
    """Trimmed plain-text abstract from a possibly-HTML, possibly-multilingual value."""
    text = re.sub(r"<[^>]+>", " ", _text(value))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _resource_urls(resources, *, kind_key: str, url_keys: tuple[str, ...]) -> list[dict]:
    """Normalise a dataset's resources/distributions to the record ``urls`` shape."""
    urls = []
    for res in resources or []:
        if not isinstance(res, dict):
            continue
        url = next((res[k] for k in url_keys if res.get(k)), None)
        if isinstance(url, list):                    # data.europa access_url is a list
            url = next((u for u in url if u), None)
        # ... or a *stringified* list ("['http://...', ...]") on the CKAN facade
        if isinstance(url, str) and url.startswith("["):
            m = re.search(r"https?://[^'\"\s,\]]+", url)
            url = m.group(0) if m else None
        if not url:
            continue
        entry = {"kind": _text(res.get(kind_key)) or "", "url": str(url)}
        entry["service"] = _is_service(entry)
        urls.append(entry)
    return urls


def _discover_ckan(source: Source, query: str, limit: int = 20) -> dict:
    """CKAN ``package_search`` (data.europa.eu CKAN endpoint, catalog.data.gov...).

    ``source.endpoint`` is the CKAN API root (the segment before
    ``/package_search``); ``discover: {dataset_page: "https://.../{id}"}``
    optionally builds the human dataset page from the dataset id.
    """
    payload = _get_json(source, f"{source.endpoint.rstrip('/')}/package_search",
                        {"q": query, "rows": limit})
    result = payload.get("result") or {}
    page_tpl = source.discover.get("dataset_page")
    records = []
    for ds in result.get("results") or []:
        identifier = ds.get("name") or ds.get("id") or ""
        records.append({
            "identifier": identifier,
            "title": _text(ds.get("title")) or identifier,
            "type": ds.get("type") or "dataset",
            "abstract": _plain(ds.get("notes") or ds.get("description")),
            "page": page_tpl.format(id=identifier) if page_tpl and identifier else None,
            "urls": _resource_urls(ds.get("resources") or ds.get("distributions"),
                                   kind_key="format",
                                   url_keys=("url", "access_url", "download_url")),
        })
    return {"matches": result.get("count"), "records": records}


def _discover_datagouv(source: Source, query: str, limit: int = 20) -> dict:
    """data.gouv.fr dataset search (``/api/1/datasets/?q=``)."""
    payload = _get_json(source, f"{source.endpoint.rstrip('/')}/datasets/",
                        {"q": query, "page_size": limit})
    records = []
    for ds in payload.get("data") or []:
        records.append({
            "identifier": ds.get("id") or "",
            "title": ds.get("title") or "",
            "type": "dataset",
            "abstract": _plain(ds.get("description")),
            "page": ds.get("page"),
            "urls": _resource_urls(ds.get("resources"), kind_key="format",
                                   url_keys=("url",)),
        })
    return {"matches": payload.get("total"), "records": records}


def _discover_opendatasoft(source: Source, query: str, limit: int = 20) -> dict:
    """OpenDataSoft Explore API v2.1 catalog search (federated FR collectivités).

    ``source.endpoint`` is the Explore API root (``.../api/explore/v2.1``).
    Every dataset gets an ``exports/geojson`` URL — the portable download route.
    """
    root = source.endpoint.rstrip("/")
    quoted = query.replace('"', " ")
    payload = _get_json(source, f"{root}/catalog/datasets",
                        {"where": f'search("{quoted}")', "limit": limit})
    portal = root.split("/api/")[0]
    records = []
    for ds in payload.get("results") or []:
        dataset_id = ds.get("dataset_id") or ""
        metas = (ds.get("metas") or {}).get("default") or {}
        export = f"{root}/catalog/datasets/{dataset_id}/exports/geojson"
        records.append({
            "identifier": dataset_id,
            "title": _text(metas.get("title")) or dataset_id,
            "type": "dataset",
            "abstract": _plain(metas.get("description")),
            "page": f"{portal}/explore/dataset/{dataset_id}/" if dataset_id else None,
            "urls": [{"kind": "geojson", "url": export, "service": True}],
        })
    return {"matches": payload.get("total_count"), "records": records}


def _discover_arcgis_hub(source: Source, query: str, limit: int = 20) -> dict:
    """ArcGIS Hub search API (``/api/v3/datasets?q=``) — worldwide ArcGIS Online
    open data; record URLs point at the dataset's ArcGIS REST layer."""
    payload = _get_json(source, f"{source.endpoint.rstrip('/')}/datasets",
                        {"q": query, "page[size]": limit})
    records = []
    for ds in payload.get("data") or []:
        attrs = ds.get("attributes") or {}
        identifier = ds.get("id") or ""
        urls = _resource_urls([attrs], kind_key="type", url_keys=("url",))
        records.append({
            "identifier": identifier,
            "title": attrs.get("name") or identifier,
            "type": attrs.get("type") or "dataset",
            "abstract": _plain(attrs.get("searchDescription")
                               or attrs.get("description")),
            "page": (f"https://hub.arcgis.com/datasets/{identifier}"
                     if identifier else None),
            "urls": urls,
        })
    matches = ((payload.get("meta") or {}).get("stats") or {}).get("totalCount")
    return {"matches": matches, "records": records}


# Open-data platform backends for `discover: {api: ...}` (see `discover()`).
_API_BACKENDS = {
    "ckan": _discover_ckan,
    "datagouv": _discover_datagouv,
    "opendatasoft": _discover_opendatasoft,
    "arcgis_hub": _discover_arcgis_hub,
}


def _discover_csw(source: Source, query: str,
                  bbox: tuple[float, float, float, float] | None = None,
                  limit: int = 20) -> dict:
    """Keyword-search a CSW catalogue (see :func:`discover` for the record shape)."""
    from owslib.csw import CatalogueServiceWeb
    from owslib.fes import BBox, PropertyIsLike

    csw = CatalogueServiceWeb(source.endpoint, timeout=30)
    # Use '*' as the wildcard, NOT the default '%' — GeoNetwork servers (INSPIRE,
    # Géocatalogue, EEA...) pass the term through Java String.format, where '%'
    # is a format specifier and raises UnknownFormatConversionException.
    like = PropertyIsLike("csw:AnyText", f"*{query}*",
                          wildCard="*", singleChar="?", escapeChar="\\")
    constraints = [[like, BBox(list(bbox))]] if bbox else [like]
    log.info(f"[{source.id}] CSW search {query!r}" + (f" bbox={bbox}" if bbox else ""))
    csw.getrecords2(constraints=constraints, maxrecords=limit, esn="full")

    records = []
    for rec in csw.records.values():
        urls = []
        for ref in (getattr(rec, "references", []) or []):
            if ref.get("url"):
                urls.append({"kind": ref.get("scheme") or "", "url": ref["url"]})
        for uri in (getattr(rec, "uris", []) or []):
            if uri.get("url"):
                urls.append({"kind": uri.get("protocol") or "", "url": uri["url"]})
        for u in urls:
            u["service"] = _is_service(u)
        identifier = getattr(rec, "identifier", "") or ""
        records.append({
            "identifier": identifier,
            "title": rec.title or "",
            "type": getattr(rec, "type", "") or "",
            "abstract": (getattr(rec, "abstract", "") or "")[:200],
            "page": _record_page(source.endpoint, identifier),
            "urls": urls,
        })

    return {"matches": csw.results.get("matches"), "records": records}
