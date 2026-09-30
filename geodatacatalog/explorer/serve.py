"""Optional HTTP adapter exposing the stateful NL sessions over FastAPI.

This is the thin sidecar a frontend (e.g. GeoLibre's webview) talks to. It serves
two concerns:

1. **Catalog browser** (no LLM needed) — part 1 lists the user's ``catalog.yml``;
   part 2 explores the full registry (``sources.yml``) with a search filter; "add"
   appends a source's layers to the user's ``catalog.yml``::

       GET    /catalog?path=...                       -> {providers:[...]}
       GET    /sources?q=&theme=&coverage=            -> {sources:[...]}
       GET    /sources/{id}                           -> {source record}
       GET    /sources/{id}/layers?grep=              -> {layers:[...]}
       POST   /catalog/add  {catalog_path, source_id, layers} -> {catalog:{...}}
       POST   /catalog/fetch {catalog_path, layer_id, bbox, datetime?} -> {geojson, ...}
       POST   /fetch        {source_id, layer, bbox, datetime?} -> {geojson, n_features}
       POST   /catalog/raster {catalog_path, layer_id, bbox, datetime?} -> GeoTIFF bytes
       POST   /raster       {source_id, layer, bbox, datetime?} -> GeoTIFF bytes
       POST   /catalog/asset {catalog_path, layer_id, bbox, bands} -> raw file / .zip
       POST   /asset        {source_id, layer, bbox, bands}      -> raw file / .zip

2. **AI sessions** — keeps sessions in process, handing the client an opaque
   ``session_id`` for the NL two-step flow::

       POST   /aoi             {query}              -> {session_id, featurecollection}
       POST   /aoi/refine      {session_id, query}  -> {featurecollection}
       POST   /layers          {aoi, query, ...}    -> {session_id, result}
       POST   /layers/refine   {session_id, query}  -> {result}
       DELETE /session/{id}                          -> {closed}

The core library stays framework-free: this module (and its deps) only load with
the optional ``geodatacatalog[serve]`` extra. Run it with::

    python -m geodatacatalog.explorer.serve            # 127.0.0.1:8766
    python -m geodatacatalog.explorer.serve --reload   # auto-restart on code change (dev)

CORS defaults to GeoLibre's Tauri/Vite origins; override with
``GEODATACATALOG_SERVE_ORIGINS`` (comma-separated, or ``*``).
"""
from __future__ import annotations

import logging
import os
import uuid
from collections import OrderedDict

log = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import backend
from .inspect import (
    catalog_stac_bands,
    catalog_summary,
    download_catalog_layer_assets,
    download_layer_assets,
    fetch_catalog_layer_geojson,
    fetch_catalog_layer_geotiff,
    fetch_layer_geojson,
    fetch_layer_geotiff,
    list_catalogs,
    merge_into_catalog,
    new_catalog,
    remove_from_catalog,
    stac_bands,
    to_catalog_provider,
    to_catalog_provider_raw,
)
from .probe import USER_AGENT
from .registry import get_source, load_registry
from .search import browse_sources, source_record

#: Recently colourised raster PNGs, served by HTTP (a MapLibre image source can't
#: reliably load a multi-MB data: URI in the Tauri webview). Bounded LRU.
_RENDER_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_RENDER_CACHE_MAX = 48


def _store_render(png: bytes) -> str:
    uid = uuid.uuid4().hex
    _RENDER_CACHE[uid] = png
    while len(_RENDER_CACHE) > _RENDER_CACHE_MAX:
        _RENDER_CACHE.popitem(last=False)
    return uid


def _as_image_url(out: dict) -> dict:
    """Turn a rendered raster result's PNG bytes into a served ``/render`` URL."""
    if out.get("kind") == "image" and "png" in out:
        out = {**out}
        out["image"] = f"/render/{_store_render(out.pop('png'))}.png"
    return out

# Tauri webview + Vite dev server, mirroring GeoLibre's own sidecar CORS.
_DEFAULT_ORIGIN_REGEX = (
    r"^(http://localhost:5173|http://127\.0\.0\.1:5173"
    r"|tauri://localhost|http://tauri\.localhost)$"
)


class AoiRequest(BaseModel):
    query: str


class LayersRequest(BaseModel):
    aoi: dict | str
    query: str
    fetch: bool = False
    export_dir: str | None = None


class RefineRequest(BaseModel):
    session_id: str
    query: str


class AddRequest(BaseModel):
    catalog_path: str
    source_id: str                      # registry id — or a provider name with
    layers: list[str]                   # `endpoint` + `service_type` (ad-hoc)
    endpoint: str | None = None
    service_type: str | None = None


class RemoveRequest(BaseModel):
    catalog_path: str
    provider: str                       # provider key in catalog.yml (e.g. "ign")
    products: list[str] | None = None   # product ids to drop; None = whole provider


class NewCatalogRequest(BaseModel):
    path: str                        # path of the catalog.yml to create (per project)
    theme: str | None = None         # seed with this theme's registry sources (optional)
    overwrite: bool = False          # replace an existing file instead of failing


class FetchRequest(BaseModel):
    source_id: str
    layer: str
    bbox: list[float]            # [west, south, east, north] in EPSG:4326
    datetime: str | None = None
    vmin: float | None = None    # raster colour stretch (default: 2–98th percentile)
    vmax: float | None = None
    colormap: str = "viridis"
    aoi: dict | None = None      # GeoJSON AOI → fetch is clipped to it (else use bbox)
    bands: list[str] | None = None   # STAC bands; None → RGB-if-available default
    sort_by: str | None = None       # STAC: "date" (recent first) | "date_asc" (oldest first) | "cloud_cover"
    max_items: int | None = None     # STAC: cap on items fetched (default 100)


class CatalogFetchRequest(BaseModel):
    catalog_path: str
    layer_id: str                # e.g. "ign__plan_d_eau" (as listed by GET /catalog)
    bbox: list[float]            # [west, south, east, north] in EPSG:4326
    datetime: str | None = None
    vmin: float | None = None
    vmax: float | None = None
    colormap: str = "viridis"
    aoi: dict | None = None      # GeoJSON AOI → clip the fetch to it
    bands: list[str] | None = None   # STAC bands; None → RGB-if-available default
    sort_by: str | None = None       # STAC: "date" (recent first) | "date_asc" (oldest first) | "cloud_cover"
    max_items: int | None = None     # STAC: cap on items fetched (default 100)


def _open(opener, *args, **kwargs) -> str:
    """Open a session, surfacing a missing-AI-provider as HTTP 503."""
    try:
        return opener(*args, **kwargs)
    except RuntimeError as e:  # pick_provider: no API key configured
        raise HTTPException(status_code=503, detail=str(e))


def _ask(session_id: str, query: str) -> dict:
    try:
        return backend.session_ask(session_id, query)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


def create_app(sources=None) -> FastAPI:
    """Build the FastAPI app (factory so tests can spin up an isolated instance)."""
    # uvicorn only configures its OWN loggers (uvicorn/uvicorn.access/uvicorn.error);
    # it never touches the root logger. Without this, every log.info() in this module
    # and in the core library (catalog/layers/*, incl. the STAC search's datetime/
    # sort_by trace) is silently dropped — the terminal shows only uvicorn's request
    # line ("POST /catalog/fetch ... 200 OK"), never the app-level detail. Configured
    # here (not just in main()) so it also applies under `--reload`, whose worker
    # subprocess calls this factory directly without going through main().
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(title="geodatacatalog sessions", version="0.1.0")
    sources = sources if sources is not None else load_registry()

    origins = os.environ.get("GEODATACATALOG_SERVE_ORIGINS")
    if origins == "*":
        app.add_middleware(CORSMiddleware, allow_origins=["*"],
                           allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex="|".join(origins.split(",")) if origins else _DEFAULT_ORIGIN_REGEX,
            allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"],
        )

    @app.get("/")
    def index() -> dict:
        """Service index — a quick map of what's available."""
        return {
            "service": "geodatacatalog sessions",
            "docs": "/docs",
            "catalog_browser": {
                "GET /catalog?path=": "the user's catalog.yml",
                "GET /catalogs?dir=": "list per-project catalog.yml files",
                "POST /catalog/new": "{path, theme?, overwrite?} create a project catalog",
                "GET /sources?q=&theme=&coverage=": "explore the registry",
                "GET /sources/{id}[/layers]": "source detail / drill layers",
                "GET /sources/{id}/discover?q=": "keyword-search a meta-catalogue (CSW or dataset list, e.g. gee)",
                "GET /legend?url=": "proxy a WMS/WMTS legend graphic (dodges http/CORS)",
                "POST /catalog/add": "{catalog_path, source_id, layers}",
            },
            "ai_sessions": {
                "POST /aoi[/refine]": "resolve/refine an AOI (NL)",
                "POST /layers[/refine]": "find/refine layers over an AOI (NL)",
                "DELETE /session/{id}": "close a session",
            },
        }

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    # --- Catalog browser (no AI): part 1 = user catalog, part 2 = source explorer ---

    @app.get("/catalog")
    def get_catalog(path: str) -> dict:
        """Part 1: the user's catalog.yml as a provider/layer listing."""
        return catalog_summary(path)

    @app.get("/catalogs")
    def list_catalogs_route(dir: str = ".") -> dict:
        """List the catalog.yml files in a project directory (per-project switcher)."""
        return list_catalogs(dir)

    @app.get("/themes")
    def themes() -> dict:
        """Canonical themes — offered when seeding a new catalog (e.g. 'water')."""
        from .search import THEMES
        return {"themes": sorted(THEMES)}

    @app.post("/catalog/new")
    def new_catalog_route(req: NewCatalogRequest) -> dict:
        """Create a fresh project catalog.yml — blank, or seeded from a theme's sources."""
        picked = None
        if req.theme:
            picked = [get_source(sources, r["id"])
                      for r in browse_sources(None, sources, theme=req.theme)]
        try:
            new_catalog(req.path, picked, overwrite=req.overwrite)
        except FileExistsError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}")
        return {"path": req.path, "catalog": catalog_summary(req.path)}

    @app.get("/sources")
    def list_sources(q: str | None = None, theme: str | None = None,
                     coverage: str | None = None) -> dict:
        """Part 2: the full registry (sources.yml), filterable by text/theme/coverage."""
        recs = browse_sources(q, sources, theme=theme, coverage=coverage)
        return {"count": len(recs), "sources": recs}

    @app.get("/sources/{source_id}")
    def get_source_detail(source_id: str) -> dict:
        try:
            return source_record(get_source(sources, source_id))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")

    @app.get("/sources/{source_id}/layers")
    def get_source_layers(source_id: str, grep: str | None = None) -> dict:
        """Drill a source's layers (live for OGC/STAC, curated for REST)."""
        from .inspect import list_layers, NotEnumerable
        from .search import temporal_for
        try:
            src = get_source(sources, source_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
        try:
            layers = list_layers(src, grep=grep)
            # OGC/STAC layers already carry a per-layer `sends_datetime` (from the live
            # capabilities); fill the source-level default for REST/plugin/curated layers
            # so every layer tells the frontend whether the chosen date is sent.
            default_dt = temporal_for(src.type, src.catalog)
            for layer in layers:
                layer.setdefault("sends_datetime", default_dt)
            return {"source_id": source_id, "auth": src.auth,
                    "auth_url": src.auth_url or None, "layers": layers}
        except NotEnumerable as e:  # expected: free-form REST with no listable layers
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:  # live probe failed
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")

    @app.get("/sources/{source_id}/discover")
    def discover_source(source_id: str, q: str, limit: int = 20) -> dict:
        """Keyword-search a meta-catalogue: a CSW service (indexes OTHER services)
        or a flat dataset list (e.g. `gee`). Returns ``{matches, records}`` where
        each record carries the title/abstract plus the service/download URLs
        (CSW) or the dataset id + doc page (dataset list)."""
        from .discover import discover as meta_discover
        try:
            src = get_source(sources, source_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
        try:
            return {"source_id": source_id, **meta_discover(src, q, limit=limit)}
        except Exception as e:  # not searchable / catalogue probe failed
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")

    @app.post("/catalog/add")
    def add_to_catalog(req: AddRequest) -> dict:
        """The 'add' action: append a source's layers to the user's catalog.yml.

        With ``endpoint`` + ``service_type`` set, ``source_id`` is a provider
        name for an ad-hoc service (found via discover / proposed by the
        agent), bypassing the registry — same as the CLI's ``add --endpoint``.
        """
        try:
            if req.endpoint and req.service_type:
                block = to_catalog_provider_raw(req.source_id, req.service_type,
                                                req.endpoint, req.layers)
            else:
                try:
                    src = get_source(sources, req.source_id)
                except KeyError:
                    raise HTTPException(status_code=404,
                                        detail=f"unknown source {req.source_id!r}")
                # infer_static: tag single-epoch STAC collections `static: true` so
                # the catalog view's temporal symbol matches the explorer's (and the
                # fetch skips a useless datetime window). One extra /collections
                # request. resolve_descriptions: store each layer's real
                # abstract/title (info panel) instead of the generic provider notes.
                block = to_catalog_provider(src, req.layers, infer_static=True,
                                            resolve_descriptions=True)
            merge_into_catalog(block, req.catalog_path)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}")
        return {"added": req.source_id, "layers": req.layers,
                "catalog": catalog_summary(req.catalog_path)}

    @app.post("/catalog/remove")
    def remove_from_catalog_route(req: RemoveRequest) -> dict:
        """The 'remove' action: drop products (or a whole provider) from catalog.yml."""
        try:
            remove_from_catalog(req.catalog_path, req.provider, req.products)
        except (KeyError, FileNotFoundError) as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}")
        return {"removed": req.provider, "products": req.products,
                "catalog": catalog_summary(req.catalog_path)}

    @app.post("/fetch")
    def fetch_layer(req: FetchRequest) -> dict:
        """Fetch a vector layer on a bbox → GeoJSON, for `app.addGeoJsonLayer`."""
        if len(req.bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [west, south, east, north]")
        log.info("POST /fetch source_id=%r layer=%r bbox=%s datetime=%r bands=%s aoi=%s",
                 req.source_id, req.layer, req.bbox, req.datetime, req.bands, bool(req.aoi))
        try:
            src = get_source(sources, req.source_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown source {req.source_id!r}")
        try:
            out = fetch_layer_geojson(src, req.layer, tuple(req.bbox), req.datetime,
                                      vmin=req.vmin, vmax=req.vmax, colormap=req.colormap,
                                      aoi=req.aoi, bands=req.bands,
                                      sort_by=req.sort_by, max_items=req.max_items)
        except Exception as e:  # fetch failure, missing creds…
            log.warning("POST /fetch FAILED source_id=%r layer=%r bbox=%s → %s: %s",
                        req.source_id, req.layer, req.bbox, type(e).__name__, e)
            raise HTTPException(status_code=422, detail=f"{type(e).__name__}: {e}")
        return {"source_id": req.source_id, "layer": req.layer, **_as_image_url(out)}

    @app.get("/stac/bands")
    def stac_layer_bands(source_id: str, layer: str) -> dict:
        """Selectable bands + suggested RGB default for a registry STAC layer."""
        try:
            src = get_source(sources, source_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
        try:
            return stac_bands(src, layer)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"{type(e).__name__}: {e}")

    @app.get("/catalog/stac/bands")
    def catalog_stac_layer_bands(path: str, layer_id: str) -> dict:
        """Selectable bands + suggested RGB default for a catalog.yml STAC layer."""
        try:
            return catalog_stac_bands(path, layer_id)
        except (KeyError, FileNotFoundError) as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"{type(e).__name__}: {e}")

    @app.get("/render/{uid}.png")
    def render_png(uid: str) -> Response:
        png = _RENDER_CACHE.get(uid)
        if png is None:
            raise HTTPException(status_code=404, detail="render expired")
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/legend")
    def legend(url: str) -> Response:
        """Proxy a WMS/WMTS legend graphic so the (https) frontend can display it.

        Many legend URLs are plain ``http://`` (e.g. BRGM's mapsref) and would be
        blocked as mixed content in an https page; serving them through the (local,
        trusted) sidecar also sidesteps CORS. Only http(s) URLs are proxied.
        """
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="url must be http(s)")
        import requests
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"legend fetch failed: {e}")
        media = r.headers.get("Content-Type", "image/png").split(";")[0].strip()
        if not media.startswith("image/"):
            raise HTTPException(status_code=415, detail=f"not an image ({media})")
        return Response(content=r.content, media_type=media,
                        headers={"Cache-Control": "public, max-age=86400"})

    def _geotiff_response(getter, name: str):
        try:
            data = getter()
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:               # non-raster / fetch failure / missing creds
            raise HTTPException(status_code=422, detail=f"{type(e).__name__}: {e}")
        return Response(content=data, media_type="image/tiff",
                        headers={"Content-Disposition": f'attachment; filename="{name}.tif"'})

    @app.post("/raster")
    def raster(req: FetchRequest) -> Response:
        """Download a registry source's raster layer on a bbox as a GeoTIFF."""
        if len(req.bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [west, south, east, north]")
        try:
            src = get_source(sources, req.source_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown source {req.source_id!r}")
        return _geotiff_response(
            lambda: fetch_layer_geotiff(src, req.layer, tuple(req.bbox), req.datetime,
                                        aoi=req.aoi, bands=req.bands,
                                        sort_by=req.sort_by, max_items=req.max_items),
            f"{req.source_id}__{req.layer}")

    @app.post("/catalog/raster")
    def catalog_raster(req: CatalogFetchRequest) -> Response:
        """Download a raster layer FROM the user's catalog.yml on a bbox as a GeoTIFF."""
        if len(req.bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [west, south, east, north]")
        return _geotiff_response(
            lambda: fetch_catalog_layer_geotiff(req.catalog_path, req.layer_id,
                                                tuple(req.bbox), req.datetime,
                                                aoi=req.aoi, bands=req.bands,
                                                sort_by=req.sort_by, max_items=req.max_items),
            req.layer_id)

    def _asset_response(getter):
        """Stream a downloaded asset file (or a .zip of several) to the client."""
        try:
            name, media, data = getter()
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:               # non-STAC / no items / fetch failure / missing creds
            raise HTTPException(status_code=422, detail=f"{type(e).__name__}: {e}")
        return Response(content=data, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/asset")
    def download_asset(req: FetchRequest) -> Response:
        """Download the raw file(s) of a registry STAC layer's non-raster asset(s).

        ``bands`` carries the asset key(s) chosen from the ``downloads`` list of the
        ``/stac/bands`` response — the non-COG/Zarr assets (a zipped .hgt, a NetCDF
        cube…). Fetched with the source's own auth, so credential-gated files
        (e.g. NASA Earthdata) download server-side."""
        if len(req.bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [west, south, east, north]")
        try:
            src = get_source(sources, req.source_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown source {req.source_id!r}")
        return _asset_response(
            lambda: download_layer_assets(src, req.layer, tuple(req.bbox), req.datetime,
                                          asset_keys=req.bands, aoi=req.aoi,
                                          sort_by=req.sort_by, max_items=req.max_items))

    @app.post("/catalog/asset")
    def download_catalog_asset(req: CatalogFetchRequest) -> Response:
        """Download the raw file(s) of a catalog.yml STAC layer's non-raster asset(s)."""
        if len(req.bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [west, south, east, north]")
        return _asset_response(
            lambda: download_catalog_layer_assets(req.catalog_path, req.layer_id,
                                                  tuple(req.bbox), req.datetime,
                                                  asset_keys=req.bands, aoi=req.aoi,
                                                  sort_by=req.sort_by, max_items=req.max_items))

    @app.post("/catalog/fetch")
    def fetch_catalog_layer(req: CatalogFetchRequest) -> dict:
        """Display a catalog.yml layer (part 1): vector → GeoJSON, raster → tiles/image."""
        if len(req.bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [west, south, east, north]")
        log.info("POST /catalog/fetch layer_id=%r bbox=%s datetime=%r bands=%s aoi=%s",
                 req.layer_id, req.bbox, req.datetime, req.bands, bool(req.aoi))
        try:
            out = fetch_catalog_layer_geojson(req.catalog_path, req.layer_id,
                                              tuple(req.bbox), req.datetime,
                                              vmin=req.vmin, vmax=req.vmax, colormap=req.colormap,
                                              aoi=req.aoi, bands=req.bands,
                                              sort_by=req.sort_by, max_items=req.max_items)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown layer_id {req.layer_id!r}")
        except Exception as e:
            log.warning("POST /catalog/fetch FAILED layer_id=%r bbox=%s datetime=%r → %s: %s",
                        req.layer_id, req.bbox, req.datetime, type(e).__name__, e)
            raise HTTPException(status_code=422, detail=f"{type(e).__name__}: {e}")
        return {"layer_id": req.layer_id, **_as_image_url(out)}

    # --- AI sessions (need an LLM provider) -------------------------------------

    @app.post("/aoi")
    def aoi(req: AoiRequest) -> dict:
        sid = _open(backend.open_aoi_session)
        return {"session_id": sid, "featurecollection": _ask(sid, req.query)}

    @app.post("/aoi/refine")
    def aoi_refine(req: RefineRequest) -> dict:
        return {"featurecollection": _ask(req.session_id, req.query)}

    @app.post("/layers")
    def layers(req: LayersRequest) -> dict:
        sid = _open(backend.open_layers_session, req.aoi,
                    fetch=req.fetch, export_dir=req.export_dir)
        return {"session_id": sid, "result": _ask(sid, req.query)}

    @app.post("/layers/refine")
    def layers_refine(req: RefineRequest) -> dict:
        return {"result": _ask(req.session_id, req.query)}

    @app.delete("/session/{session_id}")
    def close(session_id: str) -> dict:
        backend.close_session(session_id)
        return {"closed": session_id}

    return app


def main() -> None:
    import argparse
    from pathlib import Path

    import uvicorn

    # Load .env from the current working directory (the repo root) so secrets like
    # CDSE_S3_ACCESS_KEY referenced as ${VAR} in catalog.yml are present in the
    # environment before any catalog is loaded. Without this, ${VAR} resolves to an
    # empty string and S3 reads fail with "The AWS Access Key Id ... does not exist".
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    p = argparse.ArgumentParser(description="geodatacatalog NL-session HTTP sidecar")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--reload", action="store_true",
                   help="auto-restart when the source changes (dev; needs watchfiles)")
    args = p.parse_args()
    if args.reload:
        try:
            import watchfiles  # noqa: F401  (uvicorn's reloader backend)
        except ImportError:
            p.error("--reload needs watchfiles: pip install watchfiles "
                    "(or pip install 'geodatacatalog[serve]')")
        # An import string + factory lets uvicorn re-import and rebuild the app in
        # the reloader subprocess; reload_dirs scopes the watch to the whole
        # geodatacatalog package (parent.parent — serve.py sits in explorer/), so
        # edits to layers/, base.py, catalog.py etc. trigger a reload too. Watching
        # only explorer/ left workers running stale layer code after a save.
        pkg_root = Path(__file__).resolve().parent.parent
        uvicorn.run("geodatacatalog.explorer.serve:create_app", factory=True,
                    host=args.host, port=args.port, reload=True,
                    reload_dirs=[str(pkg_root)])
    else:
        uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
