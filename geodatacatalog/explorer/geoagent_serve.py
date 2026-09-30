"""GeoAgent browser server with the geodatacatalog tool pack attached.

Runs opengeos/GeoAgent's FastAPI WebSocket backend (``/geoagent/ws`` — chat +
MapLibre ``map_command`` protocol) with the geodatacatalog tools injected, so a
browser frontend (GeoLibre plugin, GeoAgent's ``examples/browser_maplibre``)
gets an agent that can search sources, build a justified catalog.yml, fetch
data and display it on the connected map.

Run::

    python -m geodatacatalog.explorer.geoagent_serve --yes      # tout-en-un
    python -m geodatacatalog.explorer.geoagent_serve --provider anthropic

One command serves everything the GeoLibre plugin needs: the GeoAgent WS
backend + /files/ on **8767**, and (unless ``--no-sidecar``) the catalog
browser sidecar (``explorer.serve``) on **8766** in a background thread.
8765 is GeoLibre's web ``/sidecar`` proxy target; GeoLibre's embedded Jupyter
also defaults to 8766 — use ``--no-sidecar`` and run ``explorer.serve``
elsewhere if they clash.

Requires ``pip install geodatacatalog[geoagent]`` (+ ``[serve]`` for the
sidecar).
"""

from __future__ import annotations

import argparse
import inspect as _inspect
import logging
from pathlib import Path

log = logging.getLogger("geodatacatalog")

DEFAULT_PORT = 8767

# Appended to GeoAgent's browser system prompt: the browser map tools run in a
# webview where direct service fetches usually die on CORS (many FR services
# send no CORS headers) or WebKit URL strictness — the geodatacatalog tools
# fetch server-side and hand back a display-ready preview instead.
BROWSER_DATA_SYSTEM_PROMPT = """\
Data loading in this browser session: do NOT point add_vector_data at
WFS/Overpass/REST service URLs — browser fetches die on CORS and strict URL
parsing. To load thematic data (buildings, rivers, flood zones...), use the
geodatacatalog workflow instead: search_data_sources → list_source_layers →
propose_layers (justify with `reason`) → fetch_catalog_layers over the
map view bbox (get_map_state) or a geocoded AOI — it fetches server-side (no
CORS) and returns a `preview_url`. Display it with
add_vector_data(url=preview_url, name=<layer name>), copying `preview_url`
EXACTLY as returned — never build or guess a /files/ URL yourself, and never
turn an exported file path into a URL: exported paths are local files for the
user, only `preview_url` is fetchable. When a fetch result has no
`preview_url`, there is nothing to display for it. Never paste feature
contents, coordinates or GeoJSON into the conversation. add_xyz_tile_layer only accepts a verified
`tile_url` returned by preview_source_layer (WMS/WMTS/XYZ rasters) — NEVER
write a GetMap/tile URL yourself, the map rejects hand-built ones; likewise
add_image_overlay only accepts image URLs returned by the tools (a
`quicklook`, a render_stac_imagery `image_url`), never a hand-built GetMap.
list_layers only returns the data layers added in this session:
if the layer the user asks about is not in that list, fetch it through the
geodatacatalog workflow — never toggle basemap style layers and present them
as the requested data.
"""

# Appended to GeoAgent's browser system prompt: its stock prompts end on "keep
# replies concise", which turns a multi-layer fetch into a one-liner. The
# GeoLibre panel renders simple markdown (geoagent.ts), so ask for a real recap.
RECAP_SYSTEM_PROMPT = """\
Recap style for data loading: when a turn added map layers or fetched catalog
data, end with a structured recap in the user's language instead of a one-line
confirmation:
- one opening sentence saying what is now on the map (a fitting emoji is fine);
- a small markdown table of the layers added this turn: layer name as shown on
  the map, provider/source, and time period or product type when relevant;
- a final note section (bullet list): what the features represent (e.g. points
  vs polygons, detections vs perimeters), resolution/scale, and any limitation
  hit this turn (skipped source, auth required, empty result) with a possible
  alternative.
Only describe layers and results actually returned by the tools. The chat
panel renders simple markdown: headings, bold, bullet lists and tables (no
images, no links). This recap rule takes precedence over the general
conciseness rule; keep non-data-loading answers concise.
"""


def create_app(*, provider: str | None = None, model_id: str | None = None,
               workdir: str | Path = ".",
               catalog_path: str | Path | None = None,
               allow_browser_code: bool = False,
               auto_approve: bool = False,
               public_url: str | None = None):
    """The GeoAgent browser FastAPI app with geodatacatalog extra tools.

    ``public_url`` is the base URL clients reach this server at; exports are
    served under ``{public_url}/files/`` so fetch results carry a
    ``preview_url`` and the data never crosses the LLM context.
    """
    from geoagent.browser import server as geoagent_server
    from geoagent.core import factory as geoagent_factory

    from .geoagent_tools import _default_provider_model, geodatacatalog_tools

    provider, model_id = _default_provider_model(provider, model_id)
    public_url = (public_url or f"http://127.0.0.1:{DEFAULT_PORT}").rstrip("/")

    # for_browser_maplibre reads this module global at call time; extending it
    # here reaches every agent the WS backend creates. Guarded: create_app may
    # run more than once in a process (tests, reload).
    from .geoagent_tools import SYSTEM_PROMPT as GDC_WORKFLOW_PROMPT
    for extra_prompt in (GDC_WORKFLOW_PROMPT, BROWSER_DATA_SYSTEM_PROMPT,
                         RECAP_SYSTEM_PROMPT):
        if extra_prompt not in geoagent_factory.BROWSER_MAPLIBRE_SYSTEM_PROMPT:
            geoagent_factory.BROWSER_MAPLIBRE_SYSTEM_PROMPT += (
                "\n\n" + extra_prompt)
    # Models resolve "12 juin" against their training year (2024...) and then
    # request dates the user never asked for; anchor them. Replaced (not just
    # appended) so a long-lived process crossing midnight doesn't stack dates.
    import datetime as _dt
    import re as _re
    date_prompt = (   # single line so the replace-sub below stays trivial
        f"Current date (UTC): "
        f"{_dt.datetime.now(_dt.timezone.utc).date().isoformat()} — resolve "
        'any user date without a year (e.g. "le 12 juin") against it, never '
        "against your training data.")
    geoagent_factory.BROWSER_MAPLIBRE_SYSTEM_PROMPT = _re.sub(
        r"\n\nCurrent date \(UTC\): [^\n]*", "",
        geoagent_factory.BROWSER_MAPLIBRE_SYSTEM_PROMPT) + "\n\n" + date_prompt
    # The browser confirm policy (auto_approve_safe_only) has no client
    # round-trip: confirmation-gated tools would be silently cancelled. --yes
    # lifts the gate on the geodatacatalog write/fetch tools instead.
    # propose_writes: the GeoLibre timeline offers per-layer buttons (add to
    # catalog / display / download / info) — the agent proposes, the user acts.
    tools = geodatacatalog_tools(workdir=workdir, catalog_path=catalog_path,
                                 confirm_writes=not auto_approve,
                                 preview_base_url=public_url,
                                 propose_writes=True)
    kwargs = dict(provider=provider, model_id=model_id,
                  allow_browser_code=allow_browser_code,
                  auto_approve_browser_tools=auto_approve)

    params = _inspect.signature(geoagent_server.create_browser_app).parameters
    if "extra_tools" in params:      # patched/recent GeoAgent
        app = geoagent_server.create_browser_app(extra_tools=tools, **kwargs)
    else:
        # Released GeoAgent without the extra_tools plumb-through: inject the
        # tools by wrapping the module-level for_browser_maplibre the WS
        # handler calls.
        log.warning("GeoAgent %s has no create_browser_app(extra_tools=...); "
                    "monkeypatching for_browser_maplibre",
                    getattr(__import__("geoagent"), "__version__", "?"))
        original = geoagent_server.for_browser_maplibre

        def _with_pack(*args, **kw):
            kw.setdefault("extra_tools", tools)
            return original(*args, **kw)

        geoagent_server.for_browser_maplibre = _with_pack
        app = geoagent_server.create_browser_app(**kwargs)

    _mount_file_routes(app, workdir)
    return app


# Tauri webview + Vite dev server, mirroring explorer/serve.py's CORS defaults
# (the /files/ route is fetched cross-origin by the GeoLibre plugin).
_DEFAULT_ORIGIN_REGEX = (
    r"^(http://localhost:5173|http://127\.0\.0\.1:5173"
    r"|tauri://localhost|http://tauri\.localhost)$"
)


def _mount_file_routes(app, workdir: str | Path) -> None:
    """Serve ``workdir`` under /files/ so map clients fetch exports directly."""
    import os

    from fastapi import HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse

    origins = os.environ.get("GEODATACATALOG_SERVE_ORIGINS")
    if origins == "*":
        app.add_middleware(CORSMiddleware, allow_origins=["*"],
                           allow_methods=["GET"], allow_headers=["*"])
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex="|".join(origins.split(",")) if origins else _DEFAULT_ORIGIN_REGEX,
            allow_methods=["GET"], allow_headers=["*"],
        )

    workroot = Path(workdir).resolve()

    @app.get("/files/{file_path:path}")
    def get_file(file_path: str):
        target = (workroot / file_path).resolve()
        if not target.is_file() or not target.is_relative_to(workroot):
            raise HTTPException(status_code=404, detail="no such export")
        media = "application/geo+json" if target.suffix == ".geojson" else None
        return FileResponse(target, media_type=media)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="geodatacatalog-geoagent",
        description="GeoAgent WebSocket backend with the geodatacatalog tools.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--provider", default=None,
                        help="LLM provider (default: litellm→Mistral, or "
                             "anthropic when only ANTHROPIC_API_KEY is set)")
    parser.add_argument("--model", default=None, dest="model_id",
                        help="model id (litellm form, e.g. "
                             "mistral/mistral-small-latest)")
    parser.add_argument("--workdir", default=".",
                        help="directory anchoring catalogs and exports")
    parser.add_argument("--catalog", default=None, dest="catalog_path",
                        help="default catalog.yml the tools read/write")
    parser.add_argument("--allow-browser-code", action="store_true",
                        help="enable the run_maplibre_script tool")
    parser.add_argument("--yes", "--auto-approve", action="store_true",
                        dest="auto_approve",
                        help="auto-approve confirmation-gated tools "
                             "(catalog writes, fetches)")
    parser.add_argument("--no-sidecar", action="store_true",
                        help="do not start the catalog browser sidecar "
                             "(explorer.serve) alongside")
    parser.add_argument("--sidecar-port", type=int, default=8766,
                        help="port for the catalog browser sidecar")
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("uvicorn is required: pip install "
                         "geodatacatalog[geoagent]") from exc

    if not args.no_sidecar:
        # The GeoLibre plugin's catalog tabs talk to explorer.serve on 8766;
        # host it here too so one command serves the whole plugin. uvicorn
        # skips signal handlers off the main thread, so this is safe.
        import threading
        try:
            from .serve import create_app as create_sidecar_app
            sidecar = uvicorn.Server(uvicorn.Config(
                create_sidecar_app(), host=args.host, port=args.sidecar_port))
            threading.Thread(target=sidecar.run, daemon=True,
                             name="geodatacatalog-sidecar").start()
            log.info("catalog sidecar on http://%s:%s", args.host,
                     args.sidecar_port)
        except ImportError as exc:
            log.warning("catalog sidecar not started (%s) — the assistant "
                        "still works; run `python -m geodatacatalog.explorer"
                        ".serve` separately for the catalog tabs", exc)

    app = create_app(provider=args.provider, model_id=args.model_id,
                     workdir=args.workdir, catalog_path=args.catalog_path,
                     allow_browser_code=args.allow_browser_code,
                     auto_approve=args.auto_approve,
                     public_url=f"http://{args.host}:{args.port}")
    log.info("GeoAgent + geodatacatalog tools on ws://%s:%s/geoagent/ws",
             args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
