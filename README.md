# GeoDataCatalog

**One catalog, one request, every geospatial source.**

GeoDataCatalog aggregates heterogeneous geospatial sources — WFS, WMS, WMTS, WCS, STAC, OpenStreetMap, REST APIs and custom Python plugins — behind a single YAML configuration, with disk caching, parallel download and normalised outputs (`GeoDataFrame`, `xarray`, `pystac` items).

```python
from geodatacatalog import load_catalog, GeoRequest

catalog = load_catalog("catalog.yml")
request = GeoRequest(bbox=(1.42, 43.56, 1.46, 43.59),
                     datetime="2024-06-01/2024-08-31")

results = catalog.fetch(request, layers=[
    "ign__communes",            # WFS  → GeoDataFrame
    "ign_wmts__foret",          # WMTS → xarray.DataArray (categorical, legend)
    "earth_search__S2_L2A",     # STAC → list[pystac.Item]
])
results.explore()               # interactive Folium map
```

The bundled [`catalog.yml`](catalog.yml) is a ready-to-use **extract of the source registry**: it picks a curated subset of providers (IGN, Copernicus (Sentinel, EFFIS, Corine Land Cover), CNES THEIA, Hub'Eau, BRGM, SANDRE, ISRIC SoilGrids, GEBCO and OpenStreetMap) extracted from [`geodatacatalog/explorer/sources.yml`](geodatacatalog/explorer/sources.yml) (159 sources); [`SOURCES.md`](SOURCES.md) is generated from it, and `geodatacatalog add … -o catalog.yml` materialises any registry entry as a catalog layer. Adding a provider requires zero Python code.

> Interactive walkthrough: see [notebooks/example.ipynb](notebooks/example.ipynb).

---

## Contents

- [Why](#why)
- [Installation](#installation)
- [Quickstart](#quickstart)
- [GeoRequest — the unified request](#georequest--the-unified-request)
- [The catalog — where sources become a fetchable config](#the-catalog--where-sources-become-a-fetchable-config)
- [CatalogResults — working with results](#catalogresults--working-with-results)
- [Bundled providers](#bundled-providers)
- [REST APIs — no plugin needed](#rest-apis--no-plugin-needed)
- [Writing a plugin](#writing-a-plugin)
- [Exploring new sources](#exploring-new-sources)
- [Caching](#caching)
- [Architecture](#architecture)
- [Contributing](#contributing)

---

## Why

Fetching "all the data over my area of interest" usually means juggling owslib, pystac-client, osmnx, raw REST calls, five different CRS conventions and as many output formats. GeoDataCatalog gives you:

| Feature | Detail |
|---|---|
| **Multi-protocol** | WFS · WMS · WMTS · WCS · STAC · OSM · REST · Python plugins |
| **100% YAML config** | add/modify providers without touching Python |
| **Secrets via env vars** | `${MY_API_KEY}` resolved at load time, never stored in the file |
| **Disk cache** | `diskcache` (SQLite + GeoTIFF), configurable TTL, SHA-256 keys |
| **Parallel fetch** | `asyncio.gather` across layers, thread pool for sync clients |
| **Multi-polygon AOI** | one sub-fetch per polygon, per-polygon time windows, merged results |
| **Normalised output** | `GeoDataFrame` (vector) · `xarray.DataArray/Dataset` (raster) · `list[pystac.Item]` |
| **Auto clip** | vector and raster results clipped to your AOI |
| **WMS/WMTS legends** | RGBA → categorical band with a `legend` attribute |
| **STAC tooling** | cloud filter (metadata or SCL-on-AOI), coverage threshold, band mapping, lazy load |
| **Network resilience** | exponential-backoff retry on every provider |
| **Visualisation** | `.explore()` Folium maps for vectors, rasters and STAC footprints/thumbnails |
| **Zonal statistics** | `.compute_stats()` per polygon and per layer |
| **Export / reload** | gpkg, geojson, shp, GeoTIFF, NetCDF — and `from_files()` to reload |

## Installation

The core installs with pip / [uv](https://docs.astral.sh/uv/) — the published
wheels of `geopandas`, `shapely`, `pyogrio`, `rasterio` and `pyproj` **bundle
GEOS, GDAL and PROJ**, so nothing has to be built and no system GDAL is needed
(verified on Linux wheels: GEOS ≥ 3.13 — so `shapely.remove_repeated_points`
works — and GDAL 3.12):

```bash
uv pip install .                      # core
uv pip install ".[all]"               # + viz, jupyter, osm, s3, overture, netcdf
uv pip install ".[viz,jupyter]"       # or pick individual extras
```

Pip works the same (`pip install ".[all]"`). Use **Python 3.11 or 3.12**: the
project targets `>=3.11`, and a few extras (`odc-algo` → `numexpr`) have no
wheel yet on 3.13+ and would need a C++ toolchain to build.

Available extras:

| Extra      | Adds                                                              |
| ---------- | ----------------------------------------------------------------- |
| `viz`      | `folium`, `ipywidgets`, `matplotlib`, `mapclassify` (maps, plots) |
| `jupyter`  | `viz` + `nest_asyncio` (interactive notebooks)                    |
| `osm`      | `osmnx` (OpenStreetMap)                                           |
| `s3`       | `boto3` (signed S3 downloads, e.g. Copernicus Data Space)         |
| `overture` | `pmtiles`, `mapbox-vector-tile`, `mercantile`, `duckdb` (Overture) |
| `netcdf`   | `netcdf4`, `odc-algo`, `h5netcdf`, `fsspec[http]`, `s3fs`         |
| `dev`      | `pytest`, `ruff`                                                  |

`all` composes every extra above (it does **not** include `dev`).

**When conda still helps.** `environment.yml` pins the whole geo stack from
conda-forge for ABI consistency (pyogrio + fiona sharing one GDAL, `netcdf4` /
`s3fs` built against the same HDF5/netCDF) and fixes Python 3.11. That is the
safer path on platforms without prebuilt wheels (some ARM/macOS combos, older
glibc) or when you need a strictly reproducible binary stack:

```bash
conda env create -f environment.yml      # or: mamba env create -f environment.yml
conda activate geodatacatalog
```

For development, either path works:

```bash
git clone https://github.com/tduval/geodatacatalog
cd geodatacatalog
uv pip install -e ".[all,dev]"           # or: pip install -e ".[all,dev]"
pytest
```

### Setting the secrets

Provider credentials (`${VAR}` in `catalog.yml`) are read from the environment at
load time — never stored in the file. Copy [.env.example](.env.example) to `.env`
(gitignored), fill in your keys, and load it:

```bash
cp .env.example .env
set -a && source .env && set +a       # bash / zsh
```

or from Python/Jupyter, before `load_catalog()`:

```python
from dotenv import load_dotenv        # pip install python-dotenv
load_dotenv()
```

Providers whose variables are unset are simply skipped with a warning; everything
else keeps working.

## Quickstart

```python
import logging
import geopandas as gpd
from geodatacatalog import load_catalog, GeoRequest

logging.basicConfig(level=logging.INFO)

# 1. Load the catalog and inspect available layers
catalog = load_catalog("catalog.yml")
catalog.info()                       # DataFrame: layer_id · provider · type · kind · url

# 2. Describe what you want — once, for every provider
aoi = gpd.read_file("my_area.geojson")
request = GeoRequest(
    aoi_gdf=aoi,                     # bbox derived automatically, results clipped
    crs="EPSG:2154",
    resolution=10,                   # metres / pixel
    datetime="2024-06-01/2024-08-31",
)

# 3. Fetch
results = catalog.fetch(request, layers=[
    "ign__communes",
    "soilgrids_clay__clay_0_5cm",
    "earth_search__S2_L2A",
])
print(results)
# CatalogResults (3 layers):
#   ign__communes              : GeoDataFrame (142 rows)
#   soilgrids_clay__clay_0_5cm : DataArray {'band': 1, 'y': 128, 'x': 128}
#   earth_search__S2_L2A       : list[Item] (8)

# 4. Visualise, load imagery, export
results.explore()
results.load("earth_search__S2_L2A", bands=["red", "green", "blue", "nir"])
results.export("./output")
```

In Jupyter, pick layers interactively:

```python
sel = catalog.select_layers(kind="VECTOR")    # checkbox widget
results = catalog.fetch(request, layers=sel.get())
```

## GeoRequest — the unified request

Every provider receives the same request object.

```python
request = GeoRequest(
    bbox=(-2.5, 43.0, 2.5, 45.5),    # EPSG:4326, or derive it from aoi_gdf
    aoi_gdf=aoi,                     # GeoDataFrame: enables clipping & multi-polygon
    crs="EPSG:2154",                 # target CRS of the results
    resolution=10,                   # m/pixel (auto-derived from bbox when omitted)
    datetime="2024-06-01/2024-08-31",
    cloud_cover=15,                  # max cloud % (overrides the layer setting)
    bands=["red", "nir"],            # STAC bands
    max_features=5000,               # vector cap
    zoom=14,                         # force WMTS/XYZ zoom
)
request.explore()                    # show AOI + bbox on a map
```

**Multi-polygon AOIs.** If `aoi_gdf` has several rows, `fetch` runs one sub-request per polygon (bounded concurrency) and returns a multi-polygon `CatalogResults`. A `datetime` column in the GeoDataFrame sets a per-polygon time window — handy for event-based analysis (one date per site). An `id`/`fid`/`uid` column becomes the `polygon_id`.

## The catalog — where sources become a fetchable config

A single YAML file drives every fetch: the **catalog** you pass to `load_catalog()`.
Each top-level key is a provider, describing how to search it, how to authenticate,
and which products it exposes. A real (trimmed) example:

```yaml
earth_search:                          # provider name → layer ids `earth_search__<product>`
    search:
        type: StacSearch               # StacSearch | WFS | WMS | WMTS | WCS | REST | OSMNX | XYZ | <plugin>.py
        api_endpoint: https://earth-search.aws.element84.com/v1/search
        need_auth: false               # true → provider skipped when credentials are missing
    auth:
        no-sign-request: true          # public bucket; see "Authentication patterns" below
    products:
        S2_L2A:                        # product → layer id `earth_search__S2_L2A`
            productType: sentinel-2-l2a
            description: Sentinel-2 L2A surface reflectance (10 m, COG)
            cloudCover: 100            # item filter on eo:cloud_cover
            bandMapping:               # provider asset → common name (pivot); optional
            - blue: blue
            - red: red
            - nir: nir
```

Layer ids are `{provider_name}__{product_id}`. The format is inspired by
[EODAG](https://github.com/CS-SI/eodag) provider files.

That file is **generated, then curated** — you rarely write it from scratch. The
explorer's source registry (`geodatacatalog/explorer/sources.yml`, the source of
truth behind [`SOURCES.md`](SOURCES.md)) describes *where data lives*; `geodatacatalog
add … -o catalog.yml` turns a registry entry into this fetch-oriented shape. In
short: **the registry is for discovery, the catalog is for fetching** — `load_catalog()`
only understands the catalog format, while the registry is browsed through the CLI
(see [Exploring new sources](#exploring-new-sources) for the field-by-field
correspondence).

### The fetch workflow — request in, datasets out

`fetch(request, layers=[...])` takes one [`GeoRequest`](#georequest--the-unified-request)
and a list of layer ids, then runs one provider call per layer **in parallel** (async
I/O, sync clients on a thread pool), caches every result on disk, and returns a
[`CatalogResults`](#catalogresults--working-with-results) keyed by layer id — each
value already in its natural type (GeoDataFrame, DataArray, items…). Layers that are
missing credentials are skipped with a warning instead of failing the whole request.

Every provider shares that contract. What differs is how a layer's *items* are turned
into data — the STAC case below is the most involved one.

#### The STAC workflow — inspect, sort, then load

STAC layers return raw `pystac.Item`s — inspect, filter, sort, *then* load or download:

```python
sel = results.sel("earth_search__S2_L2A")
sel                                          # items with date, cloud % / coverage %

(results
    .sort(by="cloud_cover")                  # or "datetime", "coverage"
    .filter(coverage=100)                    # full AOI coverage only
    .first()                                 # keep the best item
    .load(bands=["red", "green", "blue"]))   # odc-stac → xr.Dataset, clipped to AOI
```

Useful config knobs (per product, in `catalog.yml`):

- `cloudCover`: filter on the `eo:cloud_cover` metadata.
- `cloudCoverAoi`: compute the *actual* cloud fraction over your AOI from the SCL band (slower, much more accurate).
- `imageCoverageThreshold`: discard items covering less than N % of the AOI.
- `bandMapping`: map provider asset names to common band names, so `bands=["red","nir"]` works identically on every provider (see below).

#### load() vs download()

`.load()` builds an `xr.Dataset` in memory (odc-stac, reprojected and clipped to the
AOI) — the convenient path for COGs readable anonymously or through cloud-native
credentials.

`.download()` instead saves the **original asset file(s) as-is** to disk, one file
per `(item, asset)`, and returns `{layer_id: [Path, ...]}`:

```python
paths = results.sel("nasa_pocloud__opera_dswx_s1").download(
    dest="./tiles", bands=["wtr"])           # → {"<layer_id>": [Path(".../B01_WTR.tif")]}
# paths["nasa_pocloud__opera_dswx_s1"][0]  → open it with rasterio / xarray yourself
```

Reach for `download()` when `.load()` can't (or shouldn't) turn the asset into a
dataset:

- **non-COG / non-georeferenced formats** the raster loader can't open (e.g. NASA
  LP DAAC's zipped `.hgt`) — a clear error is raised rather than a cryptic one;
- **auth-gated COGs served through a redirect to a pre-signed URL** (e.g. OPERA
  DSWx-S1 on NASA POCLOUD): `download` follows the redirect with `requests` and
  correctly *drops* the `Authorization` header on the signed cross-host URL, whereas
  odc-stac's `/vsicurl` read re-sends it and gets a `404`;
- whenever you want the **raw tile** (native resolution/CRS/nodata) rather than a
  reprojected, AOI-clipped array.

`bands=` accepts provider asset keys *or* the catalog's common names (`bandMapping`
pivots, e.g. `"wtr"`), exactly like `load()`. Without `layer_id`, every layer still
holding STAC items is downloaded into its own `dest/{layer_id}` subfolder.

#### Band mapping — one name, every provider

Providers name the same physical band differently: Sentinel-2's red band is the
`red` asset on Earth Search, `B04` elsewhere, and OPERA DSWx-S1's water band is
the `0_B01_WTR` asset. `bandMapping` declares a **pivot** (a provider-agnostic
common name) for each provider asset, so your code never mentions the provider's
own key:

```yaml
products:
    opera_dswx_s1:
        productType: OPERA_L3_DSWX-S1_V1_1.0
        bandMapping:
        - 0_B01_WTR: wtr      # provider asset → pivot
        - 0_B02_BWTR: bwtr
        - 0_B03_CONF: conf
```

```python
results.load(bands=["wtr"])                       # XR Dataset, the provider key stays hidden
results.download(bands=["wtr"], dest="./tiles")   # same pivot, raw file on disk
```

Both `load()` and `download()` accept **either** the provider asset key
(`"0_B01_WTR"`) **or** the pivot (`"wtr"`); resolution goes through `bandMapping`.

**How it works.** At search time the mapping is written onto each STAC item as an
`eo:bands` entry — `{"name": <asset>, "common_name": <pivot>}` — and the `eo`
extension is registered on the item, so odc-stac's alias resolver exposes the
pivot as a band name (`eo:common_name` → the asset's canonical name). This is
also why a product can map only the bands you care about: assets left out of the
mapping get no `eo:bands`, so no spurious alias is exposed.

> **Tip:** keep the pivot identical across providers (`wtr`, `red`, `nir`, …) and
> the same request works whatever the source — that is the whole point of the
> mapping.

#### Authentication patterns

```yaml
auth:
    credentials:
        api_key: ${KEY}          # → x-api-key header
        # token: ${TOKEN}        # → Authorization: Bearer
        # username: ${USER}      # → Authorization: Basic
        # password: ${PASS}

# S3-backed STAC (e.g. Copernicus Data Space):
auth:
    type: AwsAuth
    s3_endpoint: eodata.dataspace.copernicus.eu
    credentials:
        aws_access_key_id: ${CDSE_S3_ACCESS_KEY}
        aws_secret_access_key: ${CDSE_S3_SECRET_KEY}

# Public S3 buckets:
auth:
    no-sign-request: true
```

`${VAR}` / `${VAR:-default}` are resolved from the environment at load time.
Providers whose variables are unset are skipped with a warning; everything else
keeps working. See [Installation](#installation) for how to provide the secrets.

## CatalogResults — working with results

`fetch()` returns a `CatalogResults`: dict-like access plus a fluent API.

```python
gdf   = results["ign__communes"]            # GeoDataFrame
da    = results["ign_wmts__foret"]          # xarray.DataArray
items = results["earth_search__S2_L2A"]     # list[pystac.Item]

results.explore()                           # all layers on one Folium map
results.sel("ign_wmts__foret").explore()    # a single layer
results.compute_stats()                     # per-polygon zonal statistics
results.export("./output", formats={"ign__communes": "gpkg"})
```

Multi-polygon mode:

```python
results[0]                  # CatalogResults of the first polygon
results["layer_id"]         # that layer across all polygons (list)
results.sel("layer_id")     # filter every polygon down to one layer
results.compute_stats()     # one row per polygon
```

### Export and reload — save results, reopen them later

```python
results.export("./output")                       # geojson for vectors, GeoTIFF for rasters
results.export("./output", formats={
    "ign__communes": "gpkg",
    "earth_search__S2_L2A": "nc",
})

# Later — rebuild a CatalogResults from the exported files
results = catalog.from_files("./output")         # request restored from _request.json
results.compute_stats()
```

## Bundled providers

The default [`catalog.yml`](catalog.yml) ships ~40 layers, oriented towards French and European open data:

| Provider                   | Type  | Data                                                                                                                                 | Auth                  |
| -------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------ | --------------------- |
| `earth_search`             | STAC  | Sentinel-2 L2A, Sentinel-1 GRD, Landsat C2L2, Copernicus DEM                                                                         | —                     |
| `cop_dataspace`            | STAC  | Sentinel-2 L2A, Sentinel-5P NO₂ (official ESA archive)                                                                               | env vars for download |
| `geodes`                   | STAC  | THEIA OSO land cover, LAI (CNES)                                                                                                     | `GEODES_API_KEY`      |
| `ign`                      | WFS   | communes, départements, buildings, roads, railways, rivers, forest, cadastre, agricultural parcels (RPG), ZNIEFF, Natura 2000, parks | —                     |
| `ign_wmts`                 | WMTS  | orthophotos, Plan IGN, BD Forêt v2 (legend), Corine Land Cover 2018 (legend)                                                         | —                     |
| `brgm`                     | WFS   | geological faults 1:1M                                                                                                               | —                     |
| `sandre`                   | WFS   | water bodies & watercourses (BD Topage)                                                                                              | —                     |
| `hubeau` / `hubeau_nappes` | REST  | real-time hydrometry, groundwater piezometers                                                                                        | —                     |
| `effis`                    | WMS   | Fire Weather Index, fuel map (Copernicus)                                                                                            | —                     |
| `soilgrids_*`              | WCS   | sand / clay / organic carbon 0-5 cm (ISRIC, global)                                                                                  | —                     |
| `gebco`                    | WMS   | global bathymetry                                                                                                                    | —                     |
| `openstreetmap`            | OSMNX | road network, water bodies                                                                                                           | —                     |

Providers with missing credentials are simply skipped at load time — the rest of the catalog still works.

## REST APIs (no plugin needed)

The `REST` provider is config-driven and covers most bbox-filtered JSON APIs — templated query parameters plus optional pagination, all from YAML. Example: GBIF species occurrences, which filter by coordinate *ranges* (not a single bbox param) and paginate:

```yaml
gbif:
    search:
        type: REST
        api_endpoint: https://api.gbif.org/v1/occurrence/search
        queryParam: scientificName       # productType → ?scientificName=...
        dataKey: results                 # record list lives under this JSON key
        lonField: decimalLongitude
        latField: decimalLatitude
        bboxParams:                      # templates: {minx} {miny} {maxx} {maxy} {bbox}
            decimalLatitude: "{miny},{maxy}"
            decimalLongitude: "{minx},{maxx}"
        params: { hasCoordinate: "true" }
        datetimeParam: year              # request.datetime → ?year=start,end
        paginate: { offset_param: offset, limit_param: limit, page_size: 300,
                    max_records: 5000, end_field: endOfRecords }
    products:
        gypaete_barbu: { productType: Gypaetus barbatus }
```

```python
results = catalog.fetch(GeoRequest(bbox=(-5.5, 41, 9.8, 51.5),
                                   datetime="2015-01-01/2025-12-31"),
                        layers=["gbif__gypaete_barbu"])   # → GeoDataFrame of points
```

The simpler single-parameter form still works for APIs like Hub'Eau (`bbox_key: {key: bbox}`).

## Writing a plugin

For sources that fit no standard protocol *and no REST shape* (custom auth flows, binary formats, on-the-fly computation), drop a Python file next to your `catalog.yml` (or in `plugin_search_dirs`) exposing one function:

```python
# my_plugin.py
def fetch(request, product_type, config):
    """
    request      : GeoRequest (bbox, datetime, crs, resolution, aoi_gdf, ...)
    product_type : the productType string from the YAML
    config       : the full provider config dict (auth, search, ...)

    Return a GeoDataFrame, an xarray.DataArray/Dataset, or a list[pystac.Item].
    """
```

```yaml
my_provider:
    search:
        type: my_plugin.py
        api_endpoint: https://example.com/api
    products:
        my_product:
            productType: some-identifier
```

A complete commented template (vector + raster examples) lives in [examples/plugin_template.py](examples/plugin_template.py) — it is exercised by the test suite, fully offline.

For deeper integration (custom caching, async fetch), subclass `BaseLayer` and implement `_fetch_sync(request)` or override `fetch_async(...)`; then register it with `catalog.add_layer(...)`.

## Exploring new sources

The `geodatacatalog` CLI (the *explorer* sub-project) ships a curated, health-checked registry of **159 open geospatial services** — see [SOURCES.md](SOURCES.md) (generated from the registry). It takes you from "where can I find X?" to a working `catalog.yml`:

```bash
# Discover & configure sources (operate on the source registry)
geodatacatalog search "forêt"                 # FR/EN thematic search
geodatacatalog check                          # probe every service, update SOURCES.md
geodatacatalog layers ign_wfs --grep commune  # list the layers of a source
geodatacatalog discover eea_sdi "land cover"  # search a CSW meta-catalogue for services
geodatacatalog add herault_dfci ns:pistes --type WFS --endpoint <url> -o catalog.yml  # ad-hoc service
geodatacatalog preview gebco GEBCO_LATEST --open      # Folium preview on a small bbox
geodatacatalog add gebco GEBCO_LATEST -o catalog.yml  # generate/merge a catalog.yml
geodatacatalog init-catalog --theme soil -o soil.yml  # full catalog from a theme

# Use a catalog.yml (operate on your config)
geodatacatalog info catalog.yml                                   # list its layers
geodatacatalog fetch catalog.yml -l ign__communes --bbox 1.4,43.5,1.5,43.6 \
                     -o out --explore map.html --open             # download + map
```

Generated catalogs are validated with `load_catalog()` before being written. The registry lives in [geodatacatalog/explorer/sources.yml](geodatacatalog/explorer/sources.yml) — PRs adding open sources are welcome.

**Registry vs catalog — how an entry maps.** The registry is organised for *discovery* (a flat list of sources with editorial metadata), the catalog for *fetching* (a provider→products dict). `geodatacatalog add` performs the translation:

| `sources.yml` (registry) | `catalog.yml` (fetch config)             |
| ------------------------ | ---------------------------------------- |
| root: **list** of sources | root: **dict** `{provider_name: …}`     |
| `id: earth_search`       | root key `earth_search:`                 |
| `type: stac`             | `search.type: StacSearch`                |
| `endpoint: …/v1`         | `search.api_endpoint: …/v1/search`       |
| `auth: none`             | `search.need_auth: false`                |
| `auth: api_key`          | `auth.credentials.api_key: ${ID_API_KEY}` |
| `auth: oauth`            | `auth.credentials.token: ${ID_TOKEN}`    |
| `layers: [...]`          | `products: {slug: {productType: layer}}` |
| `themes` · `notes` · `coverage` · `status` · `fetch` | *(discovery-only — not in the catalog)* |

Walkthrough notebook: [examples/explore_sources.ipynb](examples/explore_sources.ipynb).

## Caching

Every fetch is cached on disk (`diskcache`, SQLite index; rasters stored as GeoTIFF). The key hashes `layer_id + bbox + crs + resolution + datetime + bands + cloud_cover + filters + zoom`.

```python
catalog = load_catalog("catalog.yml",
                       cache_dir="./geo_cache",
                       cache_ttl=86400 * 7)      # 7 days

catalog.fetch(request, no_cache=True)            # bypass for one call
catalog.clear_cache("ign__communes")             # or clear_cache() for everything
```

To diagnose performance, a profiling script is included:

```bash
python scripts/profile_fetch.py catalog.yml -l ign__communes --aoi zones.geojson
```

## Architecture

```
catalog.yml ──▶ load_catalog() ──▶ GeoDataCatalog
                                      │ .fetch(GeoRequest) · .info() · .select_layers()
                                      │
                          asyncio.gather + aiohttp + diskcache
                                      │
       ┌──────────┬─────────┬─────────┼─────────┬─────────┬────────┐
    WFSLayer  WMSLayer  WMTSLayer  WCSLayer  STACLayer  RESTLayer  ...
       │          │         │         │         │          │
  GeoDataFrame DataArray DataArray DataArray list[Item] GeoDataFrame
       └──────────┴─────────┴─────────┴─────────┴──────────┘
                                      │
                              CatalogResults
                .explore() · .load() · .download() · .sel/.sort/.filter/.first()
                .export() · .from_files() · .compute_stats()
```

Package layout:

```
geodatacatalog/
├── request.py        # GeoRequest — the unified request
├── base.py           # BaseLayer, LayerKind, LayerResult, retry
├── catalog.py        # GeoDataCatalog (fetch orchestration, cache, widgets)
├── results.py        # CatalogResults (fluent API, export, stats)
├── config.py         # load_catalog() — YAML + env-var interpolation
├── aoi.py            # geocode_aoi() — place name → AOI GeoDataFrame
├── viz.py            # Folium rendering
├── utils.py          # legends, raster export, GeoJSON helpers
├── layers/           # one module per protocol (wfs, wms, wmts, wcs, stac,
│                     #   rest, osmnx, xyz, local, plugin)
└── explorer/         # the `geodatacatalog` CLI: source registry (sources.yml),
                      #   thematic search, health checks, catalog.yml generation
```

## Contributing

Issues and pull requests are welcome. To add a provider to the default catalog, edit `catalog.yml` — please stick to **open, account-free** sources or document the required environment variables. Run `pytest` (offline-safe) before submitting.

## License

[MIT](LICENSE)
