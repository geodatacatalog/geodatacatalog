"""`geodatacatalog` command line — explore, check and configure data sources.

Commands:
    search        FR/EN thematic search over the source registry
    check         probe services (--fetch also fetch-tests layers) → sources.yml + SOURCES.md
    md            regenerate SOURCES.md without probing
    layers        list the layers/collections of a source
    show          a source's details + how to access it (esp. download/meta sources)
    preview       fetch one layer on a small bbox → Folium HTML map
    add           add source layers to a catalog.yml (created if missing)
    init-catalog  generate a catalog.yml from a thematic search
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .registry import DEFAULT_REGISTRY, get_source, load_registry, save_registry

log = logging.getLogger(__name__)


def _sources_md_path(registry_path: Path) -> Path:
    """SOURCES.md lives at the repo root when working from a checkout."""
    candidate = registry_path.parent.parent.parent / "SOURCES.md"
    return candidate if candidate.parent.joinpath("pyproject.toml").exists() else Path("SOURCES.md")


def _print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(no result)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    print("  ".join(c.upper().ljust(widths[c]) for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


def _state(src) -> str:
    return (src.status or {}).get("state", "?")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_search(args) -> int:
    from .search import search_sources

    sources = load_registry(args.registry)
    results = search_sources(args.query, sources, coverage=args.coverage)
    if not results:
        print(f"No source matches {args.query!r}. Try `geodatacatalog md` to browse SOURCES.md.")
        return 1
    rows = [{
        "id": s.id, "type": s.type, "coverage": s.coverage,
        "status": _state(s), "themes": ",".join(s.themes[:3]),
        "notes": (s.notes or s.name)[:60],
    } for s, _score in results[: args.limit]]
    _print_table(rows, ["id", "type", "coverage", "status", "themes", "notes"])
    print(f"\nNext: geodatacatalog show <id>   (details + how to access)"
          f"   ·   geodatacatalog layers <id>   ·   geodatacatalog add <id> <layer> -o catalog.yml")
    return 0


def cmd_check(args) -> int:
    from .markdown import write_markdown
    from .probe import check_sources
    from .search import search_sources

    sources = load_registry(args.registry)
    targets = sources
    if args.id:
        targets = [s for s in sources if s.id in args.id]
    elif args.theme:
        ids = {s.id for theme in args.theme for s, _ in search_sources(theme, sources)}
        targets = [s for s in sources if s.id in ids]
    if not targets:
        print("no matching source")
        return 1

    if args.bboxes:  # dump the fetch-test AOIs for visualisation (geojson.io / QGIS)
        import json

        from .verify import test_bboxes_geojson
        fc = test_bboxes_geojson(targets)
        Path(args.bboxes).write_text(json.dumps(fc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"test AOIs written: {args.bboxes} ({len(fc['features'])} distinct bbox(es))")

    summary = check_sources(targets, concurrency=args.concurrency, timeout=args.timeout)

    fetch_summary: dict[str, int] = {}
    if args.fetch:
        from .verify import apply_results, verify_sources
        # only fetch-test endpoints the probe didn't already mark down
        fetchable = [s for s in targets if (s.status or {}).get("state") != "fail"]
        results, fetch_summary = verify_sources(fetchable, max_layers=args.max_layers)
        apply_results(targets, results)

    save_registry(sources, args.registry)
    md = write_markdown(sources, _sources_md_path(Path(args.registry or DEFAULT_REGISTRY)))
    print(f"\n{len(targets)} sources checked: "
          + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    if args.fetch:
        print("  fetch test: " + ", ".join(f"{k}={v}" for k, v in sorted(fetch_summary.items())))
    print(f"registry updated, {md} regenerated")
    return 0 if summary.get("fail", 0) == 0 else 2


def cmd_md(args) -> int:
    from .markdown import write_markdown

    sources = load_registry(args.registry)
    md = write_markdown(sources, _sources_md_path(Path(args.registry or DEFAULT_REGISTRY)))
    print(f"{md} regenerated ({len(sources)} sources)")
    return 0


def cmd_layers(args) -> int:
    from .inspect import list_layers

    sources = load_registry(args.registry)
    src = get_source(sources, args.source)
    layers = list_layers(src, grep=args.grep)
    for l in layers[: args.limit]:
        print(f"{l['id']:<60} {l['title'][:70]}")
    print(f"\n{len(layers)} layer(s)"
          + (f" (showing {args.limit})" if len(layers) > args.limit else ""))
    return 0


def cmd_show(args) -> int:
    from .inspect import _is_meta_catalogue, access_hint
    from .search import _is_drillable

    sources = load_registry(args.registry)
    src = get_source(sources, args.source)
    status = src.status or {}

    print(f"{src.name}  (`{src.id}`)")
    print(f"  type      {src.type}")
    print(f"  provider  {src.provider or '—'}")
    print(f"  coverage  {src.coverage or '—'}")
    print(f"  themes    {', '.join(src.themes) or '—'}")
    print(f"  endpoint  {src.endpoint or '—'}")
    auth = src.auth or "none"
    if src.auth_url:
        auth += f"  (register: {src.auth_url})"
    print(f"  auth      {auth}")
    if status:
        detail = status.get("detail")
        print(f"  status    {status.get('state', '?')}"
              + (f" — {detail}" if detail else "")
              + (f"  (checked {status['checked_at']})" if status.get("checked_at") else ""))
    if src.notes:
        print(f"\n  notes\n    {src.notes}")

    print("\nHow to access:")
    if _is_drillable(src) and not _is_meta_catalogue(src):
        print(f"  Live service — list its layers, then add one to a catalog.yml:\n"
              f"    geodatacatalog layers {src.id}\n"
              f"    geodatacatalog add {src.id} <layer> -o catalog.yml")
    else:
        # meta-catalogue or download dataset: single source of truth for the wording
        for line in access_hint(src).split(". "):
            line = line.strip().rstrip(".")
            if line:
                print(f"  {line}.")
    return 0


def cmd_preview(args) -> int:
    from .inspect import preview

    sources = load_registry(args.registry)
    src = get_source(sources, args.source)
    bbox = tuple(float(x) for x in args.bbox.split(","))
    out = Path(args.output)
    m = preview(src, args.layer, bbox=bbox, datetime=args.datetime, output_html=out)
    if m is None:
        print("no data returned on this bbox")
        return 1
    print(f"preview saved: {out}")
    if args.open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_add(args) -> int:
    from .inspect import merge_into_catalog, to_catalog_provider, to_catalog_provider_raw

    if args.endpoint:  # ad-hoc service (e.g. found via `discover`), not a registry source
        if not args.type:
            print("--endpoint requires --type (WFS|WMS|WMTS|WCS|STAC|REST)", file=sys.stderr)
            return 1
        block = to_catalog_provider_raw(args.source, args.type, args.endpoint,
                                        args.layers, auth=args.auth)
    else:
        sources = load_registry(args.registry)
        src = get_source(sources, args.source)
        block = to_catalog_provider(src, args.layers, resolve_descriptions=True)
    if args.output:
        merge_into_catalog(block, args.output)
        print(f"{args.output} updated — try: load_catalog('{args.output}')")
    else:
        import yaml
        print(yaml.safe_dump(block, sort_keys=False, allow_unicode=True))
    return 0


def cmd_init_catalog(args) -> int:
    from .inspect import init_catalog
    from .search import search_sources

    sources = load_registry(args.registry)
    selected = sources
    if args.theme:
        ids = {s.id for theme in args.theme for s, _ in search_sources(theme, sources)}
        selected = [s for s in sources if s.id in ids]
    if args.coverage:
        selected = [s for s in selected if s.coverage in (args.coverage, "global")]
    init_catalog(selected, args.output, only_ok=not args.include_failing)
    print(f"{args.output} generated — try: load_catalog('{args.output}')")
    return 0


def cmd_discover(args) -> int:
    from .discover import discover

    sources = load_registry(args.registry)
    src = get_source(sources, args.source)
    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else None
    res = discover(src, args.query, bbox=bbox, limit=args.limit)
    print(f"{res['matches']} match(es) in `{src.id}` for {args.query!r}\n")
    for r in res["records"]:
        print(f"• {r['title'][:78]}  [{r['type']}]")
        services = [u for u in r["urls"] if u["service"]]
        for u in services[:3]:
            print(f"    {(u['kind'] or '?')[:32]} → {u['url'][:80]}")
        if not services:
            # dataset-list records (e.g. `gee`): the id is the handle, not a URL
            if r["identifier"]:
                print(f"    id → {r['identifier']}")
            if r.get("page"):
                print(f"    doc → {r['page'][:90]}")
    if src.discover.get("dataset_list"):
        print("\nUse the dataset id with the source's own client library "
              "(e.g. ee.ImageCollection(id) for `gee`).")
    else:
        print("\nFound a service you want? Add it to sources.yml, then `geodatacatalog add ...`.")
    return 0


def _load_user_catalog(args):
    """Load the user's catalog.yml (not the source registry)."""
    from .. import load_catalog
    return load_catalog(args.catalog, cache_dir=args.cache_dir,
                        plugin_search_dirs=args.plugin_dir or None)


def cmd_info(args) -> int:
    catalog = _load_user_catalog(args)
    df = catalog.info()
    if args.grep:
        g = args.grep.lower()
        df = df[df.apply(lambda r: g in " ".join(map(str, r.values)).lower(), axis=1)]
    if args.kind:
        df = df[df["kind"].str.upper() == args.kind.upper()]
    if df.empty:
        print("no matching layer")
        return 1
    print(df.to_string(index=False))
    print(f"\n{len(df)} layer(s). Fetch with: "
          f"geodatacatalog fetch {args.catalog} -l <layer_id> --bbox ...")
    return 0


def cmd_fetch(args) -> int:
    from .. import GeoRequest

    catalog = _load_user_catalog(args)
    if args.aoi:
        import geopandas as gpd
        request = GeoRequest(aoi_gdf=gpd.read_file(args.aoi), datetime=args.datetime,
                             crs=args.crs, resolution=args.resolution)
    else:
        bbox = tuple(float(x) for x in args.bbox.split(","))
        request = GeoRequest(bbox=bbox, datetime=args.datetime,
                             crs=args.crs, resolution=args.resolution)

    targets = args.layer or list(catalog.layers)
    log.info(f"Fetching {len(targets)} layer(s) over bbox={request.bbox} "
             f"datetime={request.datetime} — this can take a while for wide WFS extents...")
    results = catalog.fetch(request, layers=args.layer or None, no_cache=args.no_cache)
    print(results)

    if args.output:
        formats = None
        if args.format:
            keys = args.layer or list(results.keys())
            formats = {lid: args.format for lid in keys}
        results.export(args.output, formats=formats)
        print(f"\nexported → {args.output}/")
    if args.explore:
        m = results.explore(lighten=not args.explore_full)
        if m is not None:
            Path(args.explore).parent.mkdir(parents=True, exist_ok=True)
            m.save(args.explore)
            size_mb = Path(args.explore).stat().st_size / 1e6
            print(f"map saved → {args.explore} ({size_mb:.1f} MB)")
            if args.open:
                import webbrowser
                webbrowser.open(Path(args.explore).resolve().as_uri())
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geodatacatalog",
        description="Explore open geospatial data sources and generate catalog.yml configs.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="info-level logging")
    parser.add_argument("--debug", action="store_true",
                        help="DEBUG logging + log every HTTP request sent to servers")
    parser.add_argument("--registry", default=None,
                        help=f"path to sources.yml (default: {DEFAULT_REGISTRY})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="FR/EN thematic search ('forêt', 'soil', 'bathymétrie'...)")
    p.add_argument("query")
    p.add_argument("--coverage", help="filter: france, europe, global...")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("check", help="probe services (optionally fetch-test), update sources.yml and SOURCES.md")
    p.add_argument("--theme", action="append", help="restrict to a theme (repeatable)")
    p.add_argument("--id", action="append", help="restrict to source ids (repeatable)")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--fetch", action="store_true",
                   help="also FETCH each layer on a small AOI (adds a Fetch column to SOURCES.md)")
    p.add_argument("--max-layers", type=int, default=1,
                   help="with --fetch: layers to fetch-test per source (default 1)")
    p.add_argument("--bboxes", metavar="PATH",
                   help="write the fetch-test AOIs as GeoJSON here (to visualise them)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("md", help="regenerate SOURCES.md without probing")
    p.set_defaults(func=cmd_md)

    p = sub.add_parser("layers", help="list the layers/collections of a source")
    p.add_argument("source")
    p.add_argument("--grep", help="filter on id/title")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_layers)

    p = sub.add_parser("show", help="show a registry source's details + how to access it")
    p.add_argument("source", help="registry source id (e.g. koppen_geiger)")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("discover", help="search a meta-catalogue (CSW services, or the "
                                        "GEE data catalog) by keyword")
    p.add_argument("source", help="a csw-type source id (e.g. eea_sdi, sextant), or gee")
    p.add_argument("query", help="keywords (e.g. 'land cover', 'bathymetry')")
    p.add_argument("--bbox", help="optional minx,miny,maxx,maxy in EPSG:4326")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("preview", help="fetch a layer on a small bbox → Folium HTML")
    p.add_argument("source")
    p.add_argument("layer")
    p.add_argument("--bbox", default="1.40,43.55,1.50,43.65",
                   help="minx,miny,maxx,maxy in EPSG:4326 (default: Toulouse)")
    p.add_argument("--datetime", default=None, help='e.g. "2024-06-01/2024-08-31"')
    p.add_argument("-o", "--output", default="preview.html")
    p.add_argument("--open", action="store_true", help="open the map in the browser")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("add", help="add layers to a catalog.yml (registry source or ad-hoc service)")
    p.add_argument("source", help="registry source id, or a provider name when using --endpoint")
    p.add_argument("layers", nargs="+", help="layer/collection/typename identifiers")
    p.add_argument("--type", help="service type for an ad-hoc service (WFS|WMS|WMTS|WCS|STAC|REST)")
    p.add_argument("--endpoint", help="service URL for an ad-hoc service (e.g. found via `discover`)")
    p.add_argument("--auth", default="none", choices=["none", "api_key", "oauth"])
    p.add_argument("-o", "--output", help="catalog.yml to create/merge (default: print)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("init-catalog", help="generate a catalog.yml from a thematic search")
    p.add_argument("--theme", action="append", help="theme(s) to include (repeatable)")
    p.add_argument("--coverage", help="filter: france, europe...")
    p.add_argument("--include-failing", action="store_true",
                   help="also include sources whose last check failed")
    p.add_argument("-o", "--output", default="catalog.yml")
    p.set_defaults(func=cmd_init_catalog)

    def _catalog_opts(par):
        par.add_argument("catalog", nargs="?", default="catalog.yml",
                         help="path to a catalog.yml (default: ./catalog.yml)")
        par.add_argument("--cache-dir", default="./geo_cache")
        par.add_argument("--plugin-dir", action="append",
                         help="extra directory to look up .py plugin modules (repeatable)")

    p = sub.add_parser("info", help="list the layers of a catalog.yml")
    _catalog_opts(p)
    p.add_argument("--grep", help="filter rows on any column")
    p.add_argument("--kind", help="filter by kind: VECTOR | RASTER | STAC_ITEMS")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("fetch", help="fetch layers from a catalog.yml and export them")
    _catalog_opts(p)
    p.add_argument("-l", "--layer", action="append", help="layer id to fetch (repeatable; default all)")
    p.add_argument("--bbox", default="1.40,43.55,1.50,43.65",
                   help="minx,miny,maxx,maxy in EPSG:4326 (default: Toulouse)")
    p.add_argument("--aoi", help="vector file (geojson/gpkg); overrides --bbox and enables clipping")
    p.add_argument("--datetime", default=None, help='e.g. "2024-06-01/2024-08-31"')
    p.add_argument("--crs", default="EPSG:3857")
    p.add_argument("--resolution", type=float, default=None, help="metres/pixel (raster)")
    p.add_argument("-o", "--output", help="directory to export results into")
    p.add_argument("--format", help="export format for all layers (gpkg/geojson/shp/tif/nc)")
    p.add_argument("--explore", help="save a Folium map to this HTML path")
    p.add_argument("--explore-full", action="store_true",
                   help="embed full-resolution geometries and all columns "
                        "in the --explore map (can be huge)")
    p.add_argument("--open", action="store_true", help="open the map (with --explore)")
    p.add_argument("--no-cache", action="store_true", help="bypass the disk cache")
    p.set_defaults(func=cmd_fetch)

    return parser


def _enable_http_debug() -> None:
    """Log every outbound HTTP request (URL/params) made to the data servers.

    Turns up the HTTP clients we go through — urllib3/requests (REST, plugins),
    owslib (WFS/WMS/WMTS/WCS), pystac-client (STAC), httpx (Sentinel Hub) — so
    their request lines appear. We avoid ``http.client`` wire dumps so request
    headers (Authorization, API keys) are not printed.
    """
    for name in ("urllib3", "requests", "owslib", "pystac_client", "httpx", "aiohttp.client"):
        logging.getLogger(name).setLevel(logging.DEBUG)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Long-running commands log their progress at INFO so the user sees activity.
    verbose_cmds = {"check", "fetch", "preview"}
    level = (logging.DEBUG if args.debug else
             logging.INFO if args.verbose or args.command in verbose_cmds else logging.WARNING)
    logging.basicConfig(level=level, format="%(message)s")
    if args.debug:
        _enable_http_debug()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130
    except KeyError as e:
        print(e.args[0] if e.args else e, file=sys.stderr)
        return 1
    except Exception as e:
        if args.verbose or args.debug:
            raise
        print(f"error: {e}  (use -v for the full traceback)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
