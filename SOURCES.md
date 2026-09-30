# Open geospatial data sources

> **Generated file — do not edit.** The source of truth is
> [`geodatacatalog/explorer/sources.yml`](geodatacatalog/explorer/sources.yml);
> regenerate with `geodatacatalog check` (probes every service) or `geodatacatalog md`.
>
> 158 sources · last check: 2026-09-09 · search them with
> `geodatacatalog search "forêt"` (FR/EN) and turn them into a config with
> `geodatacatalog add <source> <layer> -o catalog.yml`.

Each entry: name · type · coverage · last-check status · 🔒 if credentials are needed (linked to the registration page when known) · 💰 if there's no free tier · coloured theme tags. Click **Détails** for the full note.
Status: ✅ reachable & fetch confirmed · 🟡 reachable, fetch not confirmed (failed/empty/untested) · ❌ unreachable · 🔐 credentials required · ⏭ skipped · ❔ not checked

## 🛰️ Satellite imagery & Earth observation

- **[Earth Search (Element 84)](https://earth-search.aws.element84.com/v1)** (`earth_search`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🌐 ✅ · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![elevation](https://img.shields.io/badge/elevation-slategray)
  Sentinel-2 L2A COG, Sentinel-1 GRD, Landsat C2L2, NAIP and Copernicus DEM via a public STAC API.
  <details><summary>Détails</summary>

  Sentinel-2 L2A COG, Sentinel-1 GRD, Landsat C2L2, NAIP, Copernicus DEM. API + Sentinel-2/DEM free & anonymous, but Sentinel-1 GRD (s3://sentinel-s1-l1c) and Landsat C2L2 (s3://usgs-landsat) are REQUESTER-PAYS — need AWS creds + requester-pays. For a free Sentinel-1 GRD in Europe use cop_dataspace instead.

  </details>
- **[Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/api/stac/v1)** (`planetary_computer`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🌐 ✅ · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![climate](https://img.shields.io/badge/climate-orange) ![elevation](https://img.shields.io/badge/elevation-slategray)
  126+ Microsoft-hosted STAC collections (land cover, DEM...) with auto-signed, free asset access.
  <details><summary>Détails</summary>

  126+ collections (incl. esa-worldcover 10 m land cover, cop-dem-glo-30 DEM). Assets are signed automatically (free, no account) when the planetary-computer package is installed — no credentials needed.

  </details>
- **[Google Earth Engine (catalogue public)](https://earthengine-stac.storage.googleapis.com/catalog/catalog.json)** (`gee`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🌐 🟡 [🔒](https://code.earthengine.google.com/register) · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![elevation](https://img.shields.io/badge/elevation-slategray) ![climate](https://img.shields.io/badge/climate-orange) ![fire](https://img.shields.io/badge/fire-orangered) ![water](https://img.shields.io/badge/water-dodgerblue)
  Google's public Earth Engine catalog (~900 datasets), searchable, used via the earthengine-api.
  <details><summary>Détails</summary>

  Catalogue public Earth Engine (~900 datasets) publié en STAC statique, cherchable par mot-clé; chaque résultat est un id Earth Engine (ex. COPERNICUS/S2_SR_HARMONIZED) à utiliser avec la bibliothèque earthengine-api (ee.ImageCollection(id)) — le catalogue STAC lui-même ne sert que la recherche, pas les données. Accès: compte Google + projet Cloud (gratuit usage non commercial).

  Fetch test: 🔐 auth=oauth, no creds in env

  </details>
- **[Copernicus Data Space STAC](https://stac.dataspace.copernicus.eu/v1)** (`cop_dataspace`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🌐 ✅ · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![climate](https://img.shields.io/badge/climate-orange)
  ESA's official Sentinel 1/2/3/5P archive via STAC, assets on S3 (needs free CDSE S3 keys).
  <details><summary>Détails</summary>

  Official ESA archive — Sentinel 1/2/3/5P. Data assets live on the eodata S3 store: set CDSE_S3_ACCESS_KEY / CDSE_S3_SECRET_KEY (free, from the S3 keys manager) before fetching, or reads fail with "AWS Access Key Id does not exist" / empty rasters.

  </details>
- **[USGS LandsatLook](https://landsatlook.usgs.gov/stac-server)** (`usgs_landsatlook`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🌐 ✅ · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue)
  Landsat Collection 2 browse/search via USGS's STAC-like LandsatLook API.
  <details><summary>Détails</summary>

  Landsat Collection 2

  </details>
- **[NASA CMR-STAC (LPCLOUD)](https://cmr.earthdata.nasa.gov/cloudstac/LPCLOUD)** (`nasa_cmr_lpcloud`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🌐 ✅ · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![elevation](https://img.shields.io/badge/elevation-slategray) ![landcover](https://img.shields.io/badge/landcover-yellowgreen)
  NASA HLS, ASTER, SRTM and NASADEM via STAC — needs a free Earthdata login for data access.
  <details><summary>Détails</summary>

  HLS, ASTER, SRTM, NASADEM... Reading the data assets needs a free NASA Earthdata login (set up ~/.netrc for urs.earthdata.nasa.gov); without it the COGs come back as a login redirect and the fetch fails.

  </details>
- **[GEODES (CNES)](https://geodes-portal.cnes.fr/api/stac)** (`geodes`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🇫🇷 🟡 [🔒](https://geodes-portal.cnes.fr/MyInformation) · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![agriculture](https://img.shields.io/badge/agriculture-gold)
  CNES portal for THEIA land cover/LAI/reflectance products plus Pléiades and SPOT imagery.
  <details><summary>Détails</summary>

  THEIA products (OSO land cover, LAI, reflectances), Pléiades, SPOT. STAC 1.0.0-beta.2 — pystac_client get_collections() raises STACTypeError, so listing falls back to the raw /collections document. Item search/fetch needs GEODES_API_KEY.

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[Sentinel Hub — indices & bandes spectrales (CDSE)](https://sh.dataspace.copernicus.eu/api/v1/process)** (`sentinelhub`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 [🔒](https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings) · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![agriculture](https://img.shields.io/badge/agriculture-gold) ![forest](https://img.shields.io/badge/forest-forestgreen)
  On-the-fly Sentinel-2 spectral indices (NDVI, NBR...) and raw bands via Sentinel Hub (CDSE).
  <details><summary>Détails</summary>

  THE source for named spectral INDICES — any spyndex Sentinel-2 index (NDVI, NDMI, NBR, NDWI, EVI, SAVI...) — and single BANDS (B01-B12), computed on the fly from Sentinel-2 L2A (SCL cloud-masked) via the Sentinel Hub Processing API. productType = the index/band name; prefix it with d (dNBR, dBAI, dNDVI...) for the CHANGE between the two ends of the datetime range (index[date2] - index[date1], e.g. burn severity). STAC collections only expose RAW bands, not named indices — use THIS for an index. Needs a CDSE OAuth client ID/secret.

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[Digital Earth Africa](https://explorer.digitalearth.africa/stac)** (`dea_africa`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🌍 ❌ · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![water](https://img.shields.io/badge/water-dodgerblue)
  Digital Earth Africa: Sentinel-2/Landsat surface reflectance, radar mosaics and water/crop products.
  <details><summary>Détails</summary>

  STAC API over Sentinel-2/Landsat surface reflectance, ALOS PALSAR radar mosaics, WOfS (Water Observations from Space), and continental crop/cropland masks for Africa, hosted on AWS af-south-1 as a public (no-sign-request) S3 bucket of Cloud-Optimized GeoTIFFs.

  </details>
- **[Digital Earth Australia](https://explorer.dea.ga.gov.au/stac)** (`dea_australia`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🇦🇺 ✅ · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![landcover](https://img.shields.io/badge/landcover-yellowgreen)
  Digital Earth Australia: analysis-ready Sentinel-2/Landsat, water and coastal products.
  <details><summary>Détails</summary>

  STAC API over Geoscience Australia's Sentinel-2/Landsat ARD (Analysis Ready Data), Fractional Cover, Water Observations from Space, and coastal/geomorphology products, hosted as public Cloud-Optimized GeoTIFFs on AWS.

  </details>
- **[CEDA STAC (UK)](https://api.stac.ceda.ac.uk)** (`ceda_stac`) ![STAC](https://img.shields.io/badge/STAC-blueviolet) 🌐 🟡 · ![satellite](https://img.shields.io/badge/satellite-blue) ![climate](https://img.shields.io/badge/climate-orange) ![weather](https://img.shields.io/badge/weather-lightblue)
  UK CEDA's atmospheric and Earth observation archives via STAC.
  <details><summary>Détails</summary>

  Atmospheric and EO archives

  Fetch test: ∅ 0 STAC item(s)

  </details>
- **[Capella Space Open Data (SAR)](https://capella-open-data.s3.us-west-2.amazonaws.com/stac/catalog.json)** (`capella_opendata`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue)
  Sample SAR imagery from Capella Space's radar satellite constellation.
  <details><summary>Détails</summary>

  Static STAC catalog — SAR imagery samples

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[NASA GIBS WMTS](https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/wmts.cgi)** (`nasa_gibs_wmts`) ![WMTS](https://img.shields.io/badge/WMTS-cadetblue) 🌐 ✅ · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![weather](https://img.shields.io/badge/weather-lightblue)
  Hundreds of daily global satellite imagery layers (MODIS, VIIRS...) as tiles, from NASA GIBS.
  <details><summary>Détails</summary>

  Daily global imagery (MODIS, VIIRS...), also epsg4326/3413/3031. Hundreds of GIBS layers — this is the source behind many IntMap-style overlays: night lights / lumieres nocturnes (VIIRS Black Marble Night Lights), true-colour daily satellite / imagerie couleur naturelle, cloud fraction / couverture nuageuse, cloud-top temperature, water vapour / vapeur d'eau, land surface temperature day & night / temperature de surface, sea-surface temperature (SST) / temperature de surface de la mer et son anomalie, chlorophyll-a ocean colour / chlorophylle, aerosol optical depth & UV aerosol index / aerosols, snow & ice cover / neige et glace, sea-ice concentration / concentration de glace de mer, vegetation index NDVI / indice de vegetation.

  </details>
- **[NASA GIBS WMS](https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi)** (`nasa_gibs_wms`) ![WMS](https://img.shields.io/badge/WMS-blue) 🌐 🟡 · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue) ![weather](https://img.shields.io/badge/weather-lightblue) ![fire](https://img.shields.io/badge/fire-orangered)
  Same NASA GIBS satellite imagery catalogue as nasa_gibs_wmts, served as georeferenced WMS images.
  <details><summary>Détails</summary>

  GIBS WMS (GetMap on a bbox). Same catalogue as the WMTS tiles, handy when you need a georeferenced image rather than tiles: VIIRS/MODIS thermal anomalies / anomalies thermiques (active-fire overlays — see also nasa_firms for the point CSV), AMSRU2 sea-ice concentration / concentration de glace de mer, sea-surface temperature / temperature de surface de la mer, aerosol / aerosols, and night lights / lumieres nocturnes. Pass an explicit per-layer TIME= (the server default is a single stale date, so a bare request often returns a blank or out-of-range image).

  Fetch test: ❌ ServiceException: msWMSApplyTime: WMS server error. Time value(s) 2023-06-01T00:00:00Z given is invalid or outside the time extent defined (2026-08-03T17:14:34Z/2026-08-03T17:14:34Z/PT59M41S,2026-08-0

  </details>
- **[OpenAerialMap](https://api.openaerialmap.org)** (`openaerialmap`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![satellite](https://img.shields.io/badge/satellite-blue) ![imagery](https://img.shields.io/badge/imagery-blue)
  Crowdsourced open-licence drone/aerial imagery, mostly humanitarian captures (OpenAerialMap).
  <details><summary>Détails</summary>

  Crowdsourced/open-licence drone & aerial imagery catalogue (mostly humanitarian/disaster-response captures) — `meta` returns COG asset metadata + a bbox per image, not the pixels themselves; fetch the linked COG separately once you have a scene of interest.

  Fetch test: ∅ 0 feature(s)

  </details>
- **[Maxar Open Data (imagerie catastrophes)](https://maxar-opendata.s3.amazonaws.com/events/catalog.json)** (`maxar_open_data`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![imagery](https://img.shields.io/badge/imagery-blue) ![satellite](https://img.shields.io/badge/satellite-blue) ![hazards](https://img.shields.io/badge/hazards-red)
  High-resolution disaster-event satellite imagery, released free after major events (Maxar).
  <details><summary>Détails</summary>

  High-resolution imagery for disaster events (CC-BY-NC), as a STATIC STAC catalog. Browse with pystac; not a STAC API (no /search endpoint), so list it, do not drill it.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[ESA WorldCover (couverture terrestre 10 m)](https://services.terrascope.be/wms/v2)** (`esa_worldcover`) ![WMS](https://img.shields.io/badge/WMS-blue) 🌐 ❌ · ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![satellite](https://img.shields.io/badge/satellite-blue)
  Global 10m land cover for 2020/2021 from Sentinel-1/2 (ESA WorldCover).
  <details><summary>Détails</summary>

  Global 10 m land cover (2020 and 2021 maps, Sentinel-1/2). Same data is on Planetary Computer as STAC collection esa-worldcover (COGs).

  </details>
- **[Esri World Imagery Wayback (imagerie historique)](https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/WMTSCapabilities.xml)** (`esri_wayback`) ![WMTS](https://img.shields.io/badge/WMTS-cadetblue) 🌐 🟡 · ![imagery](https://img.shields.io/badge/imagery-blue) ![satellite](https://img.shields.io/badge/satellite-blue) ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Every historical release of Esri's World Imagery basemap since 2014, for change detection.
  <details><summary>Détails</summary>

  Every historical release of the Esri World Imagery basemap (2014→today) as WMTS layers (WB_<year>_R<nn>) — visual change detection over a decade. Free for non-commercial use.

  Fetch test: ❌ AttributeError: 'NoneType' object has no attribute 'findall'

  </details>
- **[CelesTrak (éléments orbitaux satellites)](https://celestrak.org/NORAD/elements/gp.php)** (`celestrak`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![satellite](https://img.shields.io/badge/satellite-blue)
  Free orbital element sets for tracked satellites, the modern TLE replacement (CelesTrak).
  <details><summary>Détails</summary>

  Free, no-key orbital element sets (OMM/JSON, GP = "general perturbations", the modern TLE replacement). Records are Keplerian elements (inclination, eccentricity, mean motion...), NOT lon/lat — a ground position at a given instant needs SGP4 propagation (Python `sgp4` package) from the elements plus the requested datetime. CelesTrak's usage policy asks clients to cache results and not re-query more than a few times a day per GROUP/file — element sets update at most once daily, so poll accordingly rather than per-request.

  Fetch test: ⏭ orbital elements only (no lon/lat) — needs an SGP4-propagation plugin, see notes

  </details>

## 🗺️ Basemaps & general purpose

- **[IGN Géoplateforme WMS raster](https://data.geopf.fr/wms-r/wms)** (`ign_wms_raster`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇫🇷 ✅ · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![imagery](https://img.shields.io/badge/imagery-blue) ![elevation](https://img.shields.io/badge/elevation-slategray) ![landcover](https://img.shields.io/badge/landcover-yellowgreen)
  France's IGN raster basemaps: orthophotos, Plan IGN, altitude shading.
  <details><summary>Détails</summary>

  Orthophotos, Plan IGN, altitude...

  </details>
- **[IGN Géoplateforme WMS vecteur](https://data.geopf.fr/wms-v/ows)** (`ign_wms_vector`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇫🇷 ✅ · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![admin](https://img.shields.io/badge/admin-grey) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  Styled vector-rendered WMS counterpart to IGN's raster basemap — admin boundaries, Plan IGN v2.
  <details><summary>Détails</summary>

  Vector-styled WMS counterpart to ign_wms_raster — administrative boundaries (ACADEMIES, REGIONS, DEPARTEMENTS...), the Plan IGN v2 vector basemap, and other stylised INSEE/admin layers rendered server-side as images (still a raster GetMap response, just drawn from vector data).

  </details>
- **[IGN Géoplateforme WMTS](https://data.geopf.fr/wmts)** (`ign_wmts`) ![WMTS](https://img.shields.io/badge/WMTS-cadetblue) 🇫🇷 ✅ · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![imagery](https://img.shields.io/badge/imagery-blue) ![forest](https://img.shields.io/badge/forest-forestgreen) ![landcover](https://img.shields.io/badge/landcover-yellowgreen)
  France's IGN tiled basemaps: orthophotos, Plan IGN v2, BD Forêt v2, Corine Land Cover.
  <details><summary>Détails</summary>

  Orthophotos, Plan IGN v2, BD Forêt v2, Corine Land Cover

  </details>
- **[Open Maps For Europe](https://www.mapsforeurope.org/)** (`open_maps_europe`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇪🇺 🟡 · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![admin](https://img.shields.io/badge/admin-grey)
  Pan-European reference basemaps and administrative boundaries from EuroGeographics.
  <details><summary>Détails</summary>

  EuroGeographics' pan-European reference portal — pan-European basemaps and administrative boundaries (EuroBoundaryMap, EuroGlobalMap, EuroRegionalMap) sourced from national mapping/cadastral agencies. A download portal (view/order per dataset), not a live OGC service — browse it, then fetch the specific WMS/download it points to.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Allemagne — BKG TopPlus Open](https://sgx.geodatenzentrum.de/wms_topplus_open)** (`bkg_topplus`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇩🇪 ✅ · ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Germany's free national topographic basemap, with greyscale/night variants (BKG TopPlus).
  <details><summary>Détails</summary>

  Germany's free national topographic basemap (Bundesamt für Kartographie und Geodäsie) — the "web" layer is the standard colour cartography; greyscale and night variants exist under the same service.

  </details>
- **[Royaume-Uni — Ordnance Survey Maps API](https://api.os.uk/maps/raster/v1/wmts)** (`os_uk`) ![WMTS](https://img.shields.io/badge/WMTS-cadetblue) 🇬🇧 🔐 🔒 · ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Great Britain's official raster basemap at OS-grade detail (Ordnance Survey).
  <details><summary>Détails</summary>

  Great Britain's national mapping agency raster basemap (Leisure/Road/Outdoor styles) at OS-grade detail — free tier requires a project API key from OS Data Hub (osdatahub.os.uk).

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[Espagne — IGN base](https://www.ign.es/wms-inspire/ign-base)** (`ign_es`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇪🇸 ✅ · ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Spain's national reference basemap, combined topo/admin styling (IGN).
  <details><summary>Détails</summary>

  Spain's national mapping agency reference basemap (IGNBaseTodo = combined topo/admin styling; other IGNBase* layers isolate individual themes).

  </details>
- **[Suisse — swisstopo geo.admin](https://wms.geo.admin.ch/)** (`swisstopo`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇨🇭 ✅ · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![elevation](https://img.shields.io/badge/elevation-slategray) ![admin](https://img.shields.io/badge/admin-grey)
  Switzerland's federal geoportal — hundreds of topo, DEM and thematic WMS layers (swisstopo).
  <details><summary>Détails</summary>

  Switzerland's federal geoportal (swisstopo) — hundreds of WMS layers spanning topo basemaps, DEM/relief shading, administrative boundaries, and thematic layers down to niche ones like the test layer (war memorials, ch.vbs.armee-kriegsdenkmaeler); grep GetCapabilities for a topic rather than guessing a layer name.

  </details>
- **[Canada — GéoBase CanVec](https://maps.geogratis.gc.ca/wms/canvec_en)** (`nrcan_canvec`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇨🇦 🟡 · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![admin](https://img.shields.io/badge/admin-grey)
  Canada's national topographic vector basemap: roads, hydrography, buildings, boundaries (CanVec).
  <details><summary>Détails</summary>

  Natural Resources Canada's CanVec topographic vector basemap (roads, hydrography, buildings, admin boundaries), rendered as a single combined WMS layer (canvec). The host has been unreliable (connection refused/timeouts observed) — worth a retry/status check before relying on it.

  Fetch test: ❌ RuntimeError: Failed after 4 attempts: HTTPConnectionPool(host='maps.geogratis.gc.ca', port=80): Max retries exceeded with url: /wms/canvec_en?service=WMS&version=1.3.0&request=GetMap&layers=canvec&st

  </details>
- **[Norvège — Kartverket topo](https://wms.geonorge.no/skwms1/wms.topo)** (`geonorge_no`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇳🇴 ✅ · ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Norway's national topographic basemap (roads, terrain shading, place names) — Kartverket.
  <details><summary>Détails</summary>

  Norway's national mapping agency (Kartverket) topographic basemap WMS (layer "topo" — combined roads, terrain shading, place names); Geonorge hosts many more per-theme services beyond this basemap one.

  </details>
- **[HOT Export Tool (OSM humanitaire)](https://export.hotosm.org/)** (`hotosm_export`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![hazards](https://img.shields.io/badge/hazards-red) ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Draw an AOI, export themed OpenStreetMap extracts for humanitarian mapping (HOT Export Tool).
  <details><summary>Détails</summary>

  Lets you draw an AOI and export OpenStreetMap data as a themed extract (buildings, roads, waterways, health facilities...) in Shapefile/GeoJSON/GeoPackage/KML, built for humanitarian mapping response. A browser export tool, not a queryable API — use the Overpass API directly (source "overpass") for a programmatic equivalent.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Natural Earth — Rivers & Lake Centerlines](https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson)** (`natural_earth_rivers`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![water](https://img.shields.io/badge/water-dodgerblue) ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Named major world rivers and lake centerlines, public-domain (Natural Earth).
  <details><summary>Détails</summary>

  Global river/fleuve POLYLINES (one named line per major river — the Nile, Amazon…), plus lake centerlines. Static GeoJSON (the whole file is fetched, then clipped to the AOI client-side), EPSG:4326. Use 10m for detail/names, 50m or 110m for a light world overview. No server-side bbox filter.

  </details>
- **[OSM Overpass API](https://overpass-api.de/api/interpreter)** (`overpass`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey) ![admin](https://img.shields.io/badge/admin-grey)
  Query any OpenStreetMap tag/feature worldwide via Overpass QL.
  <details><summary>Détails</summary>

  OSM vector features via Overpass QL (POST) → points/lines/polygons. The curated list is a STARTER set — productType is ANY OSM tag filter: a key (highway), a key=value (amenity=restaurant), or several separated by , / | (any-of, e.g. amenity=school,amenity=university). A tag filter alone matches EVERY feature with that tag in the AOI — append ;name=<text> to match a SINGLE named feature instead (e.g. waterway=river;name=Nile). Query per AOI/region (a planet-wide request times out). See the OSM wiki Map Features for every available key/value.

  </details>
- **[OSM Nominatim (géocodage)](https://nominatim.openstreetmap.org)** (`nominatim`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![admin](https://img.shields.io/badge/admin-grey) ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  OpenStreetMap's free-text place search and reverse address geocoder.
  <details><summary>Détails</summary>

  OpenStreetMap's public geocoder — free-text place search (search) and point-to-address reverse lookup (reverse), global coverage from OSM data. The shared demo instance enforces a strict usage policy (~1 req/s, proper User-Agent, no heavy/bulk use) — hence the 403 on a bbox-styled request below; for France-only needs prefer ban_geoplateforme (Géoplateforme/BAN), which has no such rate limit.

  Fetch test: ❌ HTTPError: 403 Client Error: Forbidden for url: https://nominatim.openstreetmap.org/search?bbox=1.4%2C43.55%2C1.5%2C43.65&size=200&format=json

  </details>
- **[OSM standard tiles](https://tile.openstreetmap.org/{z}/{x}/{y}.png)** (`osm_tiles`) ![XYZ](https://img.shields.io/badge/XYZ-gold) 🌐 🟡 · ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  The canonical OpenStreetMap 'Standard' raster basemap tiles.
  <details><summary>Détails</summary>

  The canonical OSM "Standard" style raster basemap (the tiles shown on openstreetmap.org). The public tile server has a strict usage policy (no heavy/bulk scripted use, proper User-Agent) — for an app serving many users, prefer carto_basemaps or a dedicated tile provider instead of hammering this one.

  Fetch test: ⏭ xyz: raster tile template, no queryable layer; see Status for reachability

  </details>
- **[CARTO basemaps (dark / light / voyager)](https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png)** (`carto_basemaps`) ![XYZ](https://img.shields.io/badge/XYZ-gold) 🌐 🟡 · ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Free global raster basemaps: dark, light, voyager styles (CARTO).
  <details><summary>Détails</summary>

  Free global raster basemaps (© CARTO © OpenStreetMap). The endpoint is the dark night basemap — a fond de carte sombre pour la nuit. Swap the style path for its siblings: light_all (positron clair), dark_all (dark nuit sombre), voyager, dark_nolabels, light_nolabels. Plain XYZ raster tiles — not a MapLibre style JSON.

  Fetch test: ⏭ xyz: raster tile template, no queryable layer; see Status for reachability

  </details>
- **[Sentinel-2 cloudless by EOX (fond satellite mondial)](https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg)** (`eox_s2cloudless`) ![XYZ](https://img.shields.io/badge/XYZ-gold) 🌐 🟡 · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![satellite](https://img.shields.io/badge/satellite-blue)
  Free, cloud-free global Sentinel-2 satellite mosaic, usable as a world basemap (EOX).
  <details><summary>Détails</summary>

  Free, no-key, global cloud-free Sentinel-2 mosaic (10 m, 2020 edition), suitable as a world base layer under national orthophotos. WMTS RESTful, but the path order is z/y/x (TileMatrix/TileRow/TileCol, per the WMTS spec) rather than the usual XYZ z/x/y — the {y} before {x} above is deliberate, not a typo; xyz.py substitutes placeholders by name so this template works as-is. Attribution required: "Contains modified Copernicus Sentinel data 2020".

  Fetch test: ⏭ xyz: raster tile template, no queryable layer; see Status for reachability

  </details>
- **[OpenFreeMap (tuiles vectorielles, toponymes)](https://tiles.openfreemap.org)** (`openfreemap`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Free, unlimited global vector basemap tiles for place-name labelling (OpenFreeMap).
  <details><summary>Détails</summary>

  Free, no-key, unlimited global vector tiles (OpenMapTiles schema, OSM data) — suitable for place-name labels drawn over a raster satellite/orthophoto base (a `toponyms` vector source in a MapLibre style, glyphs at tiles.openfreemap.org/fonts). MVT/PBF vector tiles — not rasterisable/mosaicable and has no per-record lon/lat, so it's a MapLibre style/vector-source layer rather than a bbox-fetchable raster or point dataset.

  Fetch test: ⏭ vector tiles (MVT/PBF), MapLibre styling only — not fetchable via geodatacatalog, see notes

  </details>
- **[Overture Maps (vector tiles)](https://overturemaps.org/)** (`overture_maps`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey) ![admin](https://img.shields.io/badge/admin-grey)
  Global harmonised places, buildings, roads and admin boundaries from multiple providers (Overture).
  <details><summary>Détails</summary>

  Global harmonised features (OSM + Meta + Microsoft + Esri + Foursquare), published as PMTiles (range-read of the few covering tiles → fast on a live bbox, at the cost of tile generalisation/clipping and a reduced attribute set) or as GeoParquet over S3 (exact geometry, all columns, but slower on a live bbox — transportation/buildings span hundreds of files). Official docs: docs.overturemaps.org.

  </details>
- **[Natural Earth (vecteurs monde)](https://www.naturalearthdata.com/downloads/)** (`natural_earth`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![admin](https://img.shields.io/badge/admin-grey)
  Public-domain cartographic world vectors at 3 scales: boundaries, coastlines, rivers, cities.
  <details><summary>Détails</summary>

  Public-domain, cartographer-curated world vector dataset at 3 generalisation scales (1:10m/1:50m/1:110m) — countries/admin-1 boundaries, coastlines, rivers/lakes, populated places, roads/railroads. A static download page (Shapefile/GeoPackage zips per theme+scale), not a live API — good as a lightweight offline basemap/AOI reference when a live WFS is overkill.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Historical country borders (aourednik)](https://raw.githubusercontent.com/aourednik/historical-basemaps/master/geojson)** (`historical_basemaps`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![admin](https://img.shields.io/badge/admin-grey) ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  Reconstructed world political borders for many historical years, one file per year.
  <details><summary>Détails</summary>

  World political boundaries reconstructed for many historical years (BC 123000 to 1994), one GeoJSON file per year (world_<year>.geojson). Whole world per file, clip client-side.

  </details>

## 🏛️ Administrative, cadastre & urban

- **[IGN Géoplateforme WFS](https://data.geopf.fr/wfs/ows)** (`ign_wfs`) ![WFS](https://img.shields.io/badge/WFS-green) 🇫🇷 ✅ · ![admin](https://img.shields.io/badge/admin-grey) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey) ![forest](https://img.shields.io/badge/forest-forestgreen) ![agriculture](https://img.shields.io/badge/agriculture-gold) ![water](https://img.shields.io/badge/water-dodgerblue) ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![urban](https://img.shields.io/badge/urban-purple)
  France's IGN vector data: BD TOPO, cadastre, RPG, ZNIEFF/Natura 2000, BD Forêt and more.
  <details><summary>Détails</summary>

  BD TOPO, cadastre, RPG, ZNIEFF/Natura 2000, BD Forêt... (hundreds of layers)

  </details>
- **[Géoplateforme / Base Adresse Nationale (géocodage)](https://data.geopf.fr/geocodage)** (`ban_geoplateforme`) ![REST](https://img.shields.io/badge/REST-orange) 🇫🇷 🟡 · ![admin](https://img.shields.io/badge/admin-grey)
  France's official address/geocoding API (Base Adresse Nationale).
  <details><summary>Détails</summary>

  French national address/geocoding API (BAN data), successor of api-adresse.data.gouv.fr — e.g. to label a clicked point with its commune. No key needed, France only (use Nominatim for global coverage). Point-query only (one lon/lat per request, no bbox) — better suited to enrichment/lookup than to bulk area downloads.

  Fetch test: ⏭ point-query only (lon/lat → nearest address, no bbox) — not a fetchable layer, see notes

  </details>
- **[OpenRouteService (routage & isochrones)](https://api.openrouteservice.org)** (`openrouteservice`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 🔒 · ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey) ![admin](https://img.shields.io/badge/admin-grey)
  Routing, isochrones and travel-time matrices over OpenStreetMap.
  <details><summary>Détails</summary>

  Routing, isochrones and matrices over OSM. Free API key required (Authorization header). profile = driving-car|foot-walking|cycling-regular...

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[Eurostat GISCO (NUTS, LAU, pays)](https://gisco-services.ec.europa.eu/distribution/v2)** (`eurostat_gisco`) ![REST](https://img.shields.io/badge/REST-orange) 🇪🇺 ✅ · ![admin](https://img.shields.io/badge/admin-grey) ![population](https://img.shields.io/badge/population-hotpink)
  EU statistical boundaries (NUTS, LAU, countries) as static GeoJSON files (Eurostat GISCO).
  <details><summary>Détails</summary>

  EU statistical boundaries as static GeoJSON files (whole-Europe download per file, no bbox filter); statistics themselves are on the separate Eurostat dissemination API (ec.europa.eu/eurostat/api/dissemination).

  </details>
- **[geoBoundaries (limites administratives monde)](https://www.geoboundaries.org/api/current)** (`geoboundaries`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![admin](https://img.shields.io/badge/admin-grey)
  Open administrative boundaries for every country, downloadable by level (geoBoundaries).
  <details><summary>Détails</summary>

  Open database of administrative boundaries for every country. The API returns metadata JSON with a gjDownloadURL (GeoJSON on GitHub) — no bbox filter, download the level you need client-side.

  Fetch test: ❌ HTTPError: 404 Client Error: Not Found for url: https://www.geoboundaries.org/api/current/gbOpen/%7BISO%7D/%7BADM%7D?bbox=1.4%2C43.55%2C1.5%2C43.65&size=200&format=json

  </details>

## ⛰️ Elevation & terrain

- **[IGN Géoplateforme altimétrie (REST)](https://data.geopf.fr/altimetrie)** (`ign_alti`) ![REST](https://img.shields.io/badge/REST-orange) 🇫🇷 ✅ · ![elevation](https://img.shields.io/badge/elevation-slategray)
  Point elevation lookups from France's IGN RGE Alti terrain model.
  <details><summary>Détails</summary>

  RGE Alti point elevation API (the old WCS endpoint was retired). Point query — samples the bbox centre (lon/lat), not a bbox.

  </details>
- **[EMODnet Bathymetry (WMS)](https://ows.emodnet-bathymetry.eu/wms)** (`emodnet_bathymetry`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇪🇺 ✅ · ![ocean](https://img.shields.io/badge/ocean-blue) ![elevation](https://img.shields.io/badge/elevation-slategray)
  European seabed depth model (bathymetry DTM) from EMODnet, as a WMS.
  <details><summary>Détails</summary>

  European bathymetry DTM (also /wfs and /wcs on the same host)

  </details>
- **[EMODnet Bathymetry (WCS)](https://ows.emodnet-bathymetry.eu/wcs)** (`emodnet_bathymetry_wcs`) ![WCS](https://img.shields.io/badge/WCS-teal) 🇪🇺 🟡 · ![ocean](https://img.shields.io/badge/ocean-blue) ![elevation](https://img.shields.io/badge/elevation-slategray)
  Same European bathymetry DTM as emodnet_bathymetry, but as raw WCS elevation values.
  <details><summary>Détails</summary>

  WCS coverage access to the same European bathymetry DTM as emodnet_bathymetry's WMS (~115 m resolution digital terrain model of sea depth), for raw elevation values rather than a rendered image.

  Fetch test: ❌ HTTPError: 404 Client Error: Not Found for url: https://ows.emodnet-bathymetry.eu/wcs?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=emodnet__mean&FORMAT=image/tiff&SUBSET=Long(4.0,4.4)&SUBS

  </details>
- **[GEBCO global bathymetry](https://wms.gebco.net/mapserv)** (`gebco`) ![WMS](https://img.shields.io/badge/WMS-blue) 🌐 ✅ · ![ocean](https://img.shields.io/badge/ocean-blue) ![elevation](https://img.shields.io/badge/elevation-slategray)
  Reference global ocean+land bathymetry grid, with sub-ice terrain variants for polar ice sheets.
  <details><summary>Détails</summary>

  The reference global ocean+land bathymetry/topography grid (GEBCO_LATEST, ~450 m/15 arc-second), plus sub-ice terrain variants (GEBCO_LATEST_SUB_ICE_TOPO) that show bedrock under Antarctic/Greenland ice sheets instead of the ice surface.

  </details>
- **[USGS 3DEP Elevation (WCS)](https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WCSServer)** (`usgs_3dep`) ![WCS](https://img.shields.io/badge/WCS-teal) 🇺🇸 🟡 · ![elevation](https://img.shields.io/badge/elevation-slategray)
  Seamless US elevation model (10m, LiDAR-derived in many areas) from USGS 3DEP.
  <details><summary>Détails</summary>

  3D Elevation Program seamless DEM mosaic for the US (1/3 arc-second ~10 m, with 1-meter LiDAR-derived coverage in many areas), served as a single dynamic WCS coverage (DEP3Elevation) rather than per-tile products.

  Fetch test: ❌ HTTPError: 404 Client Error: Not Found for url: https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WCSServer?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=DEP3Eleva

  </details>
- **[OpenTopography Global DEM API](https://portal.opentopography.org/API/globaldem)** (`opentopography`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ❌ 🔒 · ![elevation](https://img.shields.io/badge/elevation-slategray)
  Global DEMs (SRTM, Copernicus, ALOS...) via OpenTopography.
  <details><summary>Détails</summary>

  SRTM, Copernicus DEM, ALOS... (free API key)

  </details>
- **[USGS Elevation Point Query (EPQS)](https://epqs.nationalmap.gov/v1)** (`usgs_epqs`) ![REST](https://img.shields.io/badge/REST-orange) 🇺🇸 ✅ · ![elevation](https://img.shields.io/badge/elevation-slategray)
  Single-point US elevation lookup from the 3DEP model (USGS EPQS).
  <details><summary>Détails</summary>

  Point query: samples the bbox centre, returns one feature with the 3DEP elevation `value` (US and territories). For DEM rasters use the 3DEP ImageServer or opentopography.

  </details>
- **[AWS Terrain Tiles (global DEM, Terrarium)](https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png)** (`aws_terrain_tiles`) ![XYZ](https://img.shields.io/badge/XYZ-gold) 🌐 🟡 · ![elevation](https://img.shields.io/badge/elevation-slategray)
  Global elevation as RGB-encoded raster tiles, assembled from SRTM/ETOPO1/GMTED/NED (AWS).
  <details><summary>Détails</summary>

  Global elevation as Terrarium-encoded raster tiles (RGB → metres), assembled from SRTM, ETOPO1, GMTED, NED and others. Free & anonymous. Decode height = (R*256 + G + B/256) - 32768; also served as normal, geotiff and skadi formats.

  Fetch test: ⏭ xyz: raster tile template, no queryable layer; see Status for reachability

  </details>

## 🌍 Land cover & land use

- **[Corine Land Cover 2018 (EEA)](https://image.discomap.eea.europa.eu/arcgis/services/Corine/CLC2018_WM/MapServer/WMSServer)** (`eea_clc2018`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇪🇺 ✅ · ![landcover](https://img.shields.io/badge/landcover-yellowgreen)
  Corine Land Cover 2018, Europe's standard land-cover classification, from the EEA.
  <details><summary>Détails</summary>

  ArcGIS MapServer WMS, SCALE-TIERED: each numbered layer (1..13) is visible only at one zoom level, so a single numbered layer renders transparent at other scales. Request them ALL together — layer "1,2,3,4,5,7,8,9,10,11,12,13" (comma-separated GetMap) — to display Corine Land Cover at every zoom. Do NOT propose a single numbered layer.

  </details>
- **[EMODnet Seabed Habitats (EUSeaMap)](https://ows.emodnet-seabedhabitats.eu/geoserver/emodnet_open/wms)** (`emodnet_habitats`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇪🇺 ✅ · ![ocean](https://img.shields.io/badge/ocean-blue) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen) ![landcover](https://img.shields.io/badge/landcover-yellowgreen)
  EUSeaMap broad-scale seabed habitat classification for European seas (EMODnet).
  <details><summary>Détails</summary>

  EUSeaMap broad-scale seabed habitat classification (EUNIS habitat types, substrate, biological zone) covering European seas, plus specific habitat layers like the test one (coralligenous_platforms_group).

  </details>
- **[OpenLandMap STAC](https://s3.eu-central-1.wasabisys.com/stac/openlandmap/catalog.json)** (`openlandmap`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![soil](https://img.shields.io/badge/soil-sienna) ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![climate](https://img.shields.io/badge/climate-orange)
  Global 30-250m ML-predicted soil, land cover and climate layers from OpenGeoHub.
  <details><summary>Détails</summary>

  Static STAC catalog of OpenGeoHub's global 30m/250m soil, land cover and climate prediction layers (machine-learning gap-filled from satellite + in-situ data — soil pH/texture/carbon, forest/forêt cover change, cropland extent...), an alternative/complement to ISRIC SoilGrids at finer resolution for some products.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[MRLC NLCD (land cover USA)](https://www.mrlc.gov/geoserver/ows)** (`mrlc_nlcd`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇺🇸 🟡 · ![landcover](https://img.shields.io/badge/landcover-yellowgreen)
  30m US land cover classification across multiple years, plus impervious/canopy layers (NLCD).
  <details><summary>Détails</summary>

  Multi-Resolution Land Characteristics Consortium's National Land Cover Database — 30 m land cover classification for the US (multiple release years for change detection) plus impervious surface, canopy cover and fractional component layers (e.g. the test layer's Exotic_Annual_Grass rangeland product).

  Fetch test: ❌ ServiceException: java.io.IOException: Failed to create reader from file:///netapp/sharedwebfs1/shared/mrlc/data/MRLC-CurrentViewer-Layers/ExoticAnnualGrass_2025Apr12_PercentCover_native and hints Hin

  </details>
- **[Global Forest Watch Data API](https://data-api.globalforestwatch.org)** (`gfw_data_api`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![forest](https://img.shields.io/badge/forest-forestgreen) ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  Deforestation alerts and forest change data: tree cover loss, GLAD/RADD, primary forest (GFW).
  <details><summary>Détails</summary>

  Deforestation and forest change: tree cover loss, GLAD and RADD alerts, primary forest, key biodiversity areas. The dataset list is open; queries/downloads of some datasets need a free API key.

  Fetch test: ∅ 0 feature(s)

  </details>
- **[RESOLVE Ecoregions 2017 (WWF terrestrial biomes)](https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/Terrestrial_Ecoregions_World/FeatureServer)** (`resolve_ecoregions`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen) ![landcover](https://img.shields.io/badge/landcover-yellowgreen)
  846 terrestrial ecoregions across 14 biomes, the standard biogeographic units (RESOLVE/WWF).
  <details><summary>Détails</summary>

  RESOLVE Ecoregions 2017 (846 ecoregions, 14 biomes) — the standard biogeographic units. ArcGIS FeatureServer: query 0/query?where=1=1&outFields=*&f=geojson (add geometry/geometryType=esriGeometryEnvelope &inSR=4326 for a bbox). Original data also as a shapefile ZIP from ecoregions.appspot.com.

  Fetch test: ❌ JSONDecodeError: Expecting value: line 1 column 1 (char 0)

  </details>

## 🔥 Fire

- **[Copernicus EFFIS (feux de forêt)](https://maps.effis.emergency.copernicus.eu/effis)** (`effis`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇪🇺 ✅ · ![fire](https://img.shields.io/badge/fire-orangered) ![hazards](https://img.shields.io/badge/hazards-red) ![forest](https://img.shields.io/badge/forest-forestgreen)
  European wildfire danger indices, burnt areas and hotspots (Copernicus EFFIS).
  <details><summary>Détails</summary>

  Fire info — WMS layers use prefixed codes, not plain words: fire danger indices are mf010.fwi / mf010.ffmc / mf010.dmc / mf010.dc / mf010.isi / mf010.bui (NOT bare "fwi"), hs (active fire hot spots: modis/viirs/noaa), ba (burnt areas), fuel_map. Grep these codes, not "fire". GetMap REQUIRES an explicit STYLES= parameter, even empty (MapServer 8). TIME-dimensioned layers (fire danger indices) silently render BLANK without an explicit TIME=<date>: the server default is stale (2021-01-01) — always pass a recent date. EXCEPTION — HOTSPOTS (all.hs, viirs.hs, modis.hs...): they declare a time dimension but an explicit TIME= renders EMPTY everywhere; request them WITHOUT TIME to get the current detections. BURNT AREAS: this is a raster-only WMS — the .poly (polygon) variants render BLANK through GetMap, so for a visible map or a raster fetch use modis.ba.<year> (e.g. modis.ba.2024) or modis.ba.point.<year>, NEVER modis.ba.poly.*. For actual burnt-area POLYGONS (vector), the same MapServer host answers WFS GetFeature instead of WMS GetMap — see source id effis_ba (typenames need the ms: namespace prefix, e.g. ms:modis.ba.poly.season). effis.nrt.ba is BROKEN server-side for WMS (checked 2026-07-13): the group includes effis.nrt.ba.point whose GetMap answers HTTP 200 text/xml ServiceException "msPostGISLayerWhichShapes(): Query error" — for near-real-time burnt areas use nrt.ba.today / nrt.ba.week on the gwis endpoint (source id: gwis) instead, or effis_ba's WFS which works for both.

  </details>
- **[Copernicus EFFIS — zones brûlées (polygones)](https://maps.effis.emergency.copernicus.eu/effis)** (`effis_ba`) ![WFS](https://img.shields.io/badge/WFS-green) 🇪🇺 ✅ · ![fire](https://img.shields.io/badge/fire-orangered) ![hazards](https://img.shields.io/badge/hazards-red) ![forest](https://img.shields.io/badge/forest-forestgreen)
  Burnt-area polygons (vector) for EFFIS — the WFS counterpart to the effis WMS raster.
  <details><summary>Détails</summary>

  Vector counterpart to the `effis` WMS: its .poly layers render blank through GetMap, but the same MapServer host serves the same data as real polygons through WFS GetFeature — this source. Typenames MUST carry the ms: namespace prefix (bare "modis.ba.poly.season" is absent from GetCapabilities, only "ms:modis.ba.poly.season" is). modis.ba.poly.<today|week|month|season|YYYY> carry real per-fire attributes (AREA_HA, FIREDATE, COMMUNE, COUNTRY, land-cover breakdown...); effis.nrt.ba.poly returns geometry only, empty properties — prefer modis.ba.poly.today/.week for a dated near-real-time vector with attributes. GOTCHA: GetCapabilities is SLOW (~65-115s observed 2026-09) — geodatacatalog's default 60s layer timeout is too short and will time out on the first fetch; set a per-product `timeout: 120` (or more) in catalog.yml (config.py forwards a WFS product's `timeout` key to WFSLayer) or via WFSLayer(timeout=...) directly. Verified through geodatacatalog's own WFSLayer (not just curl): ms:modis.ba.poly.season over a France bbox returned 2903 polygons with correct axis order/CRS after reprojection (2026-09-09). Discovered by reverse-engineering github.com/rozierguillaume/flamap's fetch_fires.py, which uses this same WFS.

  </details>
- **[Copernicus GWIS (feux mondiaux)](https://maps.effis.emergency.copernicus.eu/gwis)** (`gwis`) ![WMS](https://img.shields.io/badge/WMS-blue) 🌐 ✅ · ![fire](https://img.shields.io/badge/fire-orangered) ![hazards](https://img.shields.io/badge/hazards-red)
  Global wildfire danger, burnt areas and hotspots — GWIS, EFFIS's worldwide counterpart.
  <details><summary>Détails</summary>

  Global fire info (MapServer WMS, same family as effis; GetMap REQUIRES an explicit STYLES= parameter, even empty, and TIME-dimensioned layers render BLANK without an explicit recent TIME=<date>; hotspots hs layers are the exception: request them WITHOUT TIME). NRT BURNT AREAS are SCALE-DEPENDENT: the bare nrt.ba group only draws at world zooms (z<=2), .point variants only at low zooms, .poly variants only from ~z9 — a blank tile at one zoom does NOT mean "no data". For a visible map use the date-windowed groups nrt.ba.today / nrt.ba.week / nrt.ba.month / nrt.ba.season (point+poly combined with scale rules, what the official EFFIS viewer uses), no TIME needed; for composites use mcd64a1.* (e.g. mcd64a1.annual_composite.c12, mcd64a1.fire_frequency). These windowed groups render SLOWLY on first hit (~15-30 s per tile, then instant from the server cache): tell the user to leave the map still for ~30 s — browser "GET aborted" on their tiles just means MapLibre cancelled a pending tile on pan/zoom, not a server failure. gwis.globfire.finalperim is BROKEN server-side (checked 2026-07-13): every GetMap answers HTTP 200 text/xml ServiceException "msPostGISLayerWhichShapes(): Query error" regardless of BBOX/TIME/CRS — use nrt.ba.today/.week or the mcd64a1.* composites for burnt areas instead.

  </details>
- **[NASA FIRMS (feux actifs)](https://firms.modaps.eosdis.nasa.gov/api)** (`nasa_firms`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 🔒 · ![fire](https://img.shields.io/badge/fire-orangered) ![hazards](https://img.shields.io/badge/hazards-red)
  Near-real-time active fire hotspots from MODIS/VIIRS thermal anomalies (NASA FIRMS).
  <details><summary>Détails</summary>

  MODIS/VIIRS near-real-time active fires / feux actifs, i.e. thermal anomalies / anomalies thermiques (the fire-hotspot layer). Free MAP_KEY required (in the URL path). Returns CSV with latitude/longitude — needs CSV parsing. For a ready-made raster overlay of the same product see nasa_gibs_wms.

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[PSFDF (incendies signalés en France)](https://test1.evan-rngt83060.workers.dev)** (`psfdf`) ![REST](https://img.shields.io/badge/REST-orange) 🇫🇷 🟡 · ![fire](https://img.shields.io/badge/fire-orangered) ![hazards](https://img.shields.io/badge/hazards-red)
  Crowdsourced live wildfire reports for France, with aircraft/ground resources engaged (PSFDF).
  <details><summary>Détails</summary>

  Crowdsourced forest-fire/wildfire reports (statut/commune/surface/moyens aériens & terrestres engagés — Hélicoptère, Avion, Canadair, Dash, AirTractor...), maintained by the PSFDF association to complement NASA FIRMS/EFFIS with human-confirmed status. The endpoint is an undocumented Cloudflare Worker (test1.evan-rngt83060.workers.dev) reverse-engineered from association-psfdf.fr's own map — not a published/versioned API, so it may change or break without notice; CORS is wide open (access-control-allow-origin: *), confirming it is meant for public browser consumption. Returns a bare JSON array (no bbox filter, always the full feed) with flat Latitude/Longitude fields per record; extra query params are ignored server-side.

  Fetch test: ⏭ not yet fetched through geodatacatalog — verified reachable + parseable via curl, see notes

  </details>
- **[NASA EONET (événements naturels en direct)](https://eonet.gsfc.nasa.gov/api/v3)** (`nasa_eonet`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![hazards](https://img.shields.io/badge/hazards-red) ![fire](https://img.shields.io/badge/fire-orangered) ![weather](https://img.shields.io/badge/weather-lightblue)
  Curated live and historical natural events (fires, storms, volcanoes...) from multiple sources (NASA EONET).
  <details><summary>Détails</summary>

  Earth Observatory Natural Event Tracker: curated live + historical natural events aggregated from GDACS, InciWeb, USGS... bbox is upper-left/lower-right (lonmin,latmax,lonmax,latmin).

  Fetch test: ❌ HTTPError: 404 Client Error: Not Found for url: https://eonet.gsfc.nasa.gov/api/v3/events/geojson/events/geojson?bbox=1.4%2C43.65%2C1.5%2C43.55&status=all&limit=500

  </details>

## 🟤 Soils

- **[ISRIC SoilGrids — sand](https://maps.isric.org/mapserv?map=/map/sand.map)** (`soilgrids_sand`) ![WCS](https://img.shields.io/badge/WCS-teal) 🌐 ✅ · ![soil](https://img.shields.io/badge/soil-sienna)
  Global sand content by depth, from ISRIC SoilGrids.
  <details><summary>Détails</summary>

  Other properties use the same pattern: clay, silt, soc, phh2o, nitrogen, bdod, cec...

  </details>
- **[ISRIC SoilGrids — clay](https://maps.isric.org/mapserv?map=/map/clay.map)** (`soilgrids_clay`) ![WCS](https://img.shields.io/badge/WCS-teal) 🌐 ✅ · ![soil](https://img.shields.io/badge/soil-sienna)
  Global clay content by depth, from ISRIC SoilGrids.
  <details><summary>Détails</summary>

  Same WCS pattern as soilgrids_sand, for clay content (g/kg fine earth) — 6 standard depths (0-5cm...100-200cm) each as mean/uncertainty-quantile layers.

  </details>
- **[ISRIC SoilGrids — organic carbon](https://maps.isric.org/mapserv?map=/map/soc.map)** (`soilgrids_soc`) ![WCS](https://img.shields.io/badge/WCS-teal) 🌐 ✅ · ![soil](https://img.shields.io/badge/soil-sienna)
  Global soil organic carbon content by depth, from ISRIC SoilGrids.
  <details><summary>Détails</summary>

  Same WCS pattern as soilgrids_sand, for soil organic carbon content (g/kg) — 6 standard depths each as mean/uncertainty-quantile layers.

  </details>
- **[ISRIC SoilGrids REST API](https://rest.isric.org/soilgrids/v2.0)** (`soilgrids_rest`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![soil](https://img.shields.io/badge/soil-sienna)
  Point lookup of ISRIC SoilGrids soil properties (clay, sand, carbon, pH...) at a single location.
  <details><summary>Détails</summary>

  Point query: samples the bbox centre and returns a single GeoJSON Feature (soil properties in properties.layers). Set property/depth/value as needed (clay, sand, soc, phh2o, nitrogen...).

  </details>
- **[USDA Soil Data Access](https://sdmdataaccess.sc.egov.usda.gov)** (`usda_sda`) ![REST](https://img.shields.io/badge/REST-orange) 🇺🇸 🟡 · ![soil](https://img.shields.io/badge/soil-sienna)
  US SSURGO/STATSGO2 soil survey database (soil map units + attribute tables), SQL-queried.
  <details><summary>Détails</summary>

  SSURGO/STATSGO2 soil survey database for the US (soil map units + hundreds of attribute tables — texture, drainage, suitability...), queried with SQL-like "Tabular/post.rest" requests, not a REST/OGC bbox API — needs a dedicated client to build the SDA query and join the returned attributes to the mapunit polygons.

  Fetch test: ⏭ no testable layer (REST without curated layers)

  </details>

## 🪨 Geology

- **[BRGM géologie 1:1M (INSPIRE)](http://mapsref.brgm.fr/wxs/1GG/BRGM_1M_INSPIRE_geolUnits_geolFaults)** (`brgm_geologie`) ![WFS](https://img.shields.io/badge/WFS-green) 🇫🇷 ✅ · ![geology](https://img.shields.io/badge/geology-brown)
  1:1M INSPIRE-harmonised geological units and faults for France, from BRGM.
  <details><summary>Détails</summary>

  Geological units and faults

  </details>
- **[BRGM InfoTerre géoservices](http://geoservices.brgm.fr/geologie)** (`brgm_infoterre`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇫🇷 ✅ · ![geology](https://img.shields.io/badge/geology-brown)
  BRGM's national bedrock/surficial geology map, mineral resources, quarries and mines.
  <details><summary>Détails</summary>

  BRGM's national geology WMS — bedrock/surficial geology map (Carte géologique de la France, harmonised 1:50k-1:1M), mineral resources, and quarries/mines, viewed through GetCapabilities-listed layers under the GEOSERVICES_GEOLOGIE group.

  </details>
- **[Macrostrat API](https://macrostrat.org/api)** (`macrostrat`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![geology](https://img.shields.io/badge/geology-brown)
  Stratigraphic/lithologic database: geologic units, drilled columns and timescale reference data.
  <details><summary>Détails</summary>

  Stratigraphic/lithologic database — geologic units at a point, drilled stratigraphic columns, and lithology/timescale reference tables, mostly US/global synthesis rather than a detailed national geology map (for that, prefer a national survey like brgm_geologie or bgs_uk).

  Fetch test: ❌ HTTPError: 500 Server Error: Internal Server Error for url: https://macrostrat.org/api/geologic_units/map?bbox=1.4%2C43.55%2C1.5%2C43.65&size=200&format=json

  </details>
- **[USGS State Geologic Map (SGMC)](https://mrdata.usgs.gov/services/sgmc)** (`usgs_sgmc`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇺🇸 🟡 · ![geology](https://img.shields.io/badge/geology-brown)
  Harmonised state-by-state bedrock geology map covering the entire US (USGS SGMC).
  <details><summary>Détails</summary>

  State Geologic Map Compilation — a harmonised, state-by-state bedrock geology map for the entire US (each state its own layer/style, hence per-state layer names like the test one, mrds-AL). The GetCapabilities URL and the actual GetMap host differ (mrdata.usgs.gov/services vs .../mapcache), which is why a naive GetMap against the capabilities host 404s.

  Fetch test: ❌ HTTPError: 404 Client Error: Not Found for url: https://mrdata.usgs.gov/mapcache/?service=WMS&version=1.1.1&request=GetMap&layers=mrds-AL&styles=&width=113&height=102&srs=EPSG%3A4326&bbox=-77.05%2C38.

  </details>
- **[British Geological Survey](https://map.bgs.ac.uk/arcgis/services/)** (`bgs_uk`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇬🇧 🟡 · ![geology](https://img.shields.io/badge/geology-brown)
  UK's national geological survey: bedrock/superficial geology, boreholes, seismic hazard (BGS).
  <details><summary>Détails</summary>

  UK's national geological survey ArcGIS Server — notable folders: GeologyOfBritain (bedrock/superficial geology), GeoIndex_Onshore/GeoIndex_Offshore/GeoIndex_GSNI (borehole & site indexes), Seismic_Hazard_Maps, hydroTimeline (groundwater levels), and quaternary. Each folder is its own MapServer, browsed via the ArcGIS REST API.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Tectonic plate boundaries (Peter Bird PB2002)](https://raw.githubusercontent.com/fraxen/tectonicplates/master/GeoJSON)** (`tectonic_plates`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![geology](https://img.shields.io/badge/geology-brown)
  World tectonic plate boundaries and plate polygons (Peter Bird PB2002 model).
  <details><summary>Détails</summary>

  Peter Bird (2003) tectonic plate model as static GeoJSON, whole world per file (no bbox filter — clip client-side). Plates are polygons, boundaries are lines.

  </details>
- **[Smithsonian Global Volcanism Program (GVP) WFS](https://webservices.volcano.si.edu/geoserver/GVP-VOTW/ows)** (`gvp_volcanoes`) ![WFS](https://img.shields.io/badge/WFS-green) 🌐 🟡 · ![geology](https://img.shields.io/badge/geology-brown) ![hazards](https://img.shields.io/badge/hazards-red)
  All 1,300+ Holocene volcanoes worldwide, plus eruption records (Smithsonian GVP).
  <details><summary>Détails</summary>

  Volcanoes of the World database — all 1,300+ Holocene volcanoes as points (GVP-VOTW:Smithsonian_VOTW_Holocene_Volcanoes), plus Pleistocene volcanoes and Holocene eruptions. GeoServer WFS, GetFeature returns GeoJSON. GVP-VOTW:E3WebApp_Emissions is broken server-side (backing DB view vrf.Emission missing, every GetFeature fails; checked 2026-07-17) — do not propose it.

  Fetch test: ∅ 0 feature(s)

  </details>

## 💧 Inland water

- **[Marine Regions (VLIZ) WFS](https://geo.vliz.be/geoserver/MarineRegions/wfs)** (`marine_regions`) ![WFS](https://img.shields.io/badge/WFS-green) 🌐 ✅ · ![ocean](https://img.shields.io/badge/ocean-blue) ![water](https://img.shields.io/badge/water-dodgerblue)
  Named sea/ocean boundaries and maritime zones (EEZ, IHO sea areas) from VLIZ.
  <details><summary>Détails</summary>

  Named sea/ocean polygons — Global Oceans & Seas (MarineRegions:goas), IHO Sea Areas (MarineRegions:iho). Also the maritime-boundary layers: Exclusive Economic Zones / ZEE (MarineRegions:eez, MarineRegions:eez_boundaries) and the 12-nautical-mile territorial seas / limites des 12 milles marins (MarineRegions:eez_12nm, plus eez_24nm contiguous zone). Ideal AOI source for marine areas (e.g. Mediterranean).

  </details>
- **[SANDRE Eau France (géo)](https://services.sandre.eaufrance.fr/geo/sandre)** (`sandre`) ![WFS](https://img.shields.io/badge/WFS-green) 🇫🇷 ✅ · ![water](https://img.shields.io/badge/water-dodgerblue)
  France's BD Topage water bodies and watercourses reference network (SANDRE).
  <details><summary>Détails</summary>

  BD Topage — water bodies, watercourses

  </details>
- **[SANDRE masses d'eau](https://services.sandre.eaufrance.fr/geo/mdo)** (`sandre_mdo`) ![WFS](https://img.shields.io/badge/WFS-green) 🇫🇷 ✅ · ![water](https://img.shields.io/badge/water-dodgerblue)
  Official EU Water Framework Directive water body geometries for France (SANDRE).
  <details><summary>Détails</summary>

  SANDRE's "masses d'eau" (DCE/WFD water bodies) reference geometries — river, lake, coastal, groundwater and transitional water body polygons/lines used to report France's water status under the EU Water Framework Directive (layer sa:BassinDCE = river basin districts; other typenames cover each water-body category).

  </details>
- **[Hub'Eau (APIs eau France)](https://hubeau.eaufrance.fr/api)** (`hubeau`) ![REST](https://img.shields.io/badge/REST-orange) 🇫🇷 ✅ · ![water](https://img.shields.io/badge/water-dodgerblue)
  France's Hub'Eau: hydrometry, piezometry, water quality and fish-population station data.
  <details><summary>Détails</summary>

  Hydrometry, piezometry, water quality, fish... (station endpoints support bbox + format=geojson)

  </details>
- **[Royaume-Uni — DEFRA / Environment Agency](https://environment.data.gov.uk/spatialdata/)** (`defra_uk`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇬🇧 ❌ · ![water](https://img.shields.io/badge/water-dodgerblue) ![hazards](https://img.shields.io/badge/hazards-red) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  England's flood risk, water quality, LiDAR terrain and habitat data (DEFRA/Environment Agency).
  <details><summary>Détails</summary>

  England's environmental open-data portal — flood risk maps (Risk of Flooding from Rivers and Sea), river/bathing water quality, LiDAR terrain, and Natural England habitat/protected-site data, each dataset with its own WMS/WFS under environment.data.gov.uk. This specific /spatialdata/ path 404s (checked 2026-08-08); browse from the portal root (environment.data.gov.uk) to find a live dataset endpoint.

  </details>
- **[USACE National Inventory of Dams (NID)](https://nid.sec.usace.army.mil/)** (`usace_nid`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇺🇸 🟡 · ![hazards](https://img.shields.io/badge/hazards-red) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey) ![water](https://img.shields.io/badge/water-dodgerblue)
  US dam inventory: location, hazard class, height, purpose (USACE NID).
  <details><summary>Détails</summary>

  Inventory of US dams (location, hazard class, height, purpose). Bulk download (CSV/GeoJSON) from the portal; the JSON API has no documented open list route.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[WRI Aqueduct 4.0 (risque eau par bassin)](https://livingatlas.esri.in/server/rest/services/Aqueduct_Water_Risk/FeatureServer)** (`wri_aqueduct`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![water](https://img.shields.io/badge/water-dodgerblue) ![hazards](https://img.shields.io/badge/hazards-red) ![climate](https://img.shields.io/badge/climate-orange)
  Water-risk indicators (stress, drought, flood) per river sub-basin worldwide (WRI Aqueduct).
  <details><summary>Détails</summary>

  WRI Aqueduct 4.0 water-risk indicators (baseline water stress, drought risk, riverine/coastal flood risk...) per HydroBASINS sub-basin, served as an ArcGIS FeatureServer (f=geojson).

  Fetch test: ∅ 0 feature(s)

  </details>

## 🚢 Transport & mobility

- **[EMODnet Human Activities](https://ows.emodnet-humanactivities.eu/wfs)** (`emodnet_human`) ![WFS](https://img.shields.io/badge/WFS-green) 🇪🇺 🟡 · ![ocean](https://img.shields.io/badge/ocean-blue) ![transport](https://img.shields.io/badge/transport-navy) ![maritime](https://img.shields.io/badge/maritime-teal) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  Vessel traffic, fishing effort, pipelines and wind farms — human sea-use data for Europe (EMODnet).
  <details><summary>Détails</summary>

  [maritime · static/aggregate, not real-time] Human uses of the sea (WFS vectors): port vessels (portvessels, portvesselsbytype), fishing effort by vessel type, pipelines, wind farms, aggregates... AIS vessel/route density grids are the raster product on the WMS — see emodnet_vessel_density.

  Fetch test: ∅ 0 feature(s)

  </details>
- **[EMODnet Vessel Density (trafic maritime AIS)](https://ows.emodnet-humanactivities.eu/wms)** (`emodnet_vessel_density`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇪🇺 ✅ · ![transport](https://img.shields.io/badge/transport-navy) ![maritime](https://img.shields.io/badge/maritime-teal) ![ocean](https://img.shields.io/badge/ocean-blue) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  Monthly AIS-derived vessel/route density grids for all European seas (EMODnet).
  <details><summary>Détails</summary>

  [maritime · aggregate, monthly, not real-time] Monthly AIS-derived vessel & route density grids for all European seas (open, no auth). Raster layers VesselDensity/RouteDensity and per-ship-type vesseldensity_NN (00=all, then fishing, cargo, tanker, passenger...), each with _avg and _seasonal variants.

  </details>
- **[OpenSky Network (trafic aérien ADS-B)](https://opensky-network.org/api)** (`opensky`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![transport](https://img.shields.io/badge/transport-navy) ![air](https://img.shields.io/badge/air-skyblue) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  Live aircraft positions worldwide from ADS-B receivers (OpenSky Network).
  <details><summary>Détails</summary>

  [air · real-time] Live aircraft positions. Responses are positional ARRAYS (state vectors) rather than flat lon/lat records (states/all). Anonymous access is rate-limited; a free OpenSky account raises the limits.

  </details>
- **[adsb.lol (positions ADS-B temps réel)](https://api.adsb.lol/v2)** (`adsblol`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![transport](https://img.shields.io/badge/transport-navy) ![air](https://img.shields.io/badge/air-skyblue)
  Free community-fed live aircraft positions, denser European coverage than OpenSky (adsb.lol).
  <details><summary>Détails</summary>

  [air · real-time] Free, no-auth, community-fed alternative to OpenSky (often denser European coverage). Each record is a flat JSON object with lat/lon fields inside a top-level "ac" list — but every endpoint takes lat/lon/radius as PATH segments (point/{lat}/{lon}/{radius}), not query-string bbox params, so a bbox request needs converting to a centre + radius first. Pair with adsbdb to resolve a returned hex/callsign into aircraft type/owner and route origin-destination.

  Fetch test: ⏭ path-templated (lat/lon/radius in the URL, not bbox query params) — needs a plugin, see notes

  </details>
- **[adsbdb (référentiel aéronefs & routes de vol)](https://api.adsbdb.com/v0)** (`adsbdb`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![transport](https://img.shields.io/badge/transport-navy) ![air](https://img.shields.io/badge/air-skyblue)
  Aircraft type/owner and flight-route lookup by ICAO24 hex or callsign (adsbdb).
  <details><summary>Détails</summary>

  [air · reference/lookup, not positional] Lookup-only reference API (one hex/registration/callsign per request, no bbox) — the natural companion to live-position sources (adsblol, opensky) to enrich a state vector's hex/callsign with aircraft type/owner and the route's origin/destination airports (which do carry lat/lon). Not bbox-fetchable on its own — useful for discovery/enrichment rather than bulk area downloads.

  Fetch test: ⏭ lookup-only API (single hex/callsign per request, no bbox) — not a fetchable layer, see notes

  </details>
- **[Airplanes.live (positions ADS-B temps réel, moyens aériens)](https://api.airplanes.live/v2)** (`airplanes_live`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![transport](https://img.shields.io/badge/transport-navy) ![air](https://img.shields.io/badge/air-skyblue)
  Another community live-aircraft-position feed, same design as adsb.lol (Airplanes.live).
  <details><summary>Détails</summary>

  [air · real-time] Same shape and API design as adsblol (another community ADS-B feeder network, "ac" list of flat lat/lon records, PATH-parameterized point/{lat}/{lon}/{radius} rather than bbox query params — same limitation, see adsblol's notes). Can track a known fleet by ICAO24 hex (v2/hex/{comma-list}), e.g. firefighting aircraft/helicopters, though the source itself is generic air-traffic tracking, not fire-specific.

  Fetch test: ⏭ path-templated (lat/lon/radius or hex list in the URL, not bbox query params) — needs a plugin, see notes

  </details>
- **[OpenFlights (aéroports, compagnies, routes aériennes)](https://raw.githubusercontent.com/jpatokal/openflights/master/data)** (`openflights`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![transport](https://img.shields.io/badge/transport-navy) ![air](https://img.shields.io/badge/air-skyblue)
  Static reference dataset of world airports, airlines and flight routes (OpenFlights).
  <details><summary>Détails</summary>

  [air · static reference, not real-time] Static, unheadered CSV snapshots (community-maintained, last major update ~2017 but still widely reused) — no live API, no bbox filter, whole file per download. airports.dat carries lat/lon per row so it geolocates directly; routes.dat only has airport codes (needs a join against airports.dat for geometry). Positional (unheadered) CSV columns — needs custom parsing to geolocate.

  Fetch test: ⏭ unheadered positional CSV, whole file per download — needs a plugin to parse/geolocate, see notes

  </details>
- **[Digitraffic Marine (trafic maritime AIS)](https://meri.digitraffic.fi/api)** (`digitraffic_marine`) ![REST](https://img.shields.io/badge/REST-orange) 🇫🇮 🟡 · ![transport](https://img.shields.io/badge/transport-navy) ![maritime](https://img.shields.io/badge/maritime-teal) ![ocean](https://img.shields.io/badge/ocean-blue) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  Live AIS vessel traffic in Finnish waters (Digitraffic Marine).
  <details><summary>Détails</summary>

  [maritime · real-time] Open AIS vessel traffic in Finnish waters only (Fintraffic) — not Europe-wide. Returns a GeoJSON FeatureCollection (geometry, not flat lon/lat), so it needs GeoJSON parsing. The server requires an Accept-Encoding gzip header.

  Fetch test: ❌ HTTPError: 400 Client Error: Bad Request for url: https://meri.digitraffic.fi/api/ais/v1/locations?bbox=24.9%2C60.13%2C25.0%2C60.18&size=200&format=json

  </details>
- **[aisstream.io (positions AIS mondiales temps réel)](https://stream.aisstream.io/v0/stream)** (`aisstream_io`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 [🔒](https://aisstream.io/authenticate) · ![transport](https://img.shields.io/badge/transport-navy) ![maritime](https://img.shields.io/badge/maritime-teal) ![ocean](https://img.shields.io/badge/ocean-blue)
  Free-tier global live AIS vessel positions via WebSocket (aisstream.io).
  <details><summary>Détails</summary>

  [maritime · real-time] Global, free-tier AIS aggregator (25,000+ vessels) covering far more ground than the region-locked AIS sources already in this registry (digitraffic_marine=Finland, marine_cadastre=USA). WebSocket only (wss://stream.aisstream.io/v0/stream): the client sends a JSON subscription message (APIKey + BoundingBoxes + FilterMessageTypes) then receives a continuous push of PositionReport/ShipStaticData frames — not a simple request/response GET. Free API key required (register at aisstream.io/authenticate).

  Fetch test: ⏭ auth=api_key, no creds in env; also WebSocket-only, needs a plugin — see notes

  </details>
- **[MarineTraffic (référence AIS commerciale)](https://services.marinetraffic.com/api)** (`marinetraffic`) 💰 ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 [🔒](https://www.marinetraffic.com/en/p/api-services) · ![transport](https://img.shields.io/badge/transport-navy) ![maritime](https://img.shields.io/badge/maritime-teal) ![ocean](https://img.shields.io/badge/ocean-blue)
  Commercial-grade global AIS vessel tracking, port calls and predictive arrivals (MarineTraffic).
  <details><summary>Détails</summary>

  The industry-reference commercial AIS/vessel-tracking provider — satellite + terrestrial AIS fused into live and historical positions, vessel master data (IMO/MMSI, type, dimensions), port calls, and predictive-arrival analytics; the de-facto benchmark the free sources in this registry (aisstream_io, digitraffic_marine, global_fishing_watch) are usually compared against for coverage/latency/completeness. Acquired by Kpler in 2024 (see kpler_maritime) but still operated and billed as its own product/API family. Paid, per-endpoint API keys (URL pattern services.marinetraffic.com/api/{service}/v:{version}/{API_KEY}/...), no free tier; each service (positions, port calls, congestion, forecasts...) is licensed separately.

  Fetch test: ⏭ auth=api_key, no creds in env, no free tier — see notes

  </details>
- **[Kpler Maritime AIS (ex-Spire Maritime)](https://servicedocs-sm.kpler.com)** (`kpler_maritime`) 💰 ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 [🔒](https://developers.kpler.com/) · ![transport](https://img.shields.io/badge/transport-navy) ![maritime](https://img.shields.io/badge/maritime-teal) ![ocean](https://img.shields.io/badge/ocean-blue)
  Kpler's enterprise satellite AIS product, global open-ocean vessel coverage.
  <details><summary>Détails</summary>

  Kpler's enterprise maritime AIS product line — formerly Spire Maritime's satellite AIS constellation (space-based coverage of open ocean where terrestrial AIS receivers cannot reach), acquired by Kpler alongside MarineTraffic (see marinetraffic) to form Kpler's combined maritime-intelligence stack. The endpoint above is the public documentation portal (servicedocs-sm.kpler.com) — the actual API host/base URL and credentials are only handed out with a commercial contract (contact developers.kpler.com / cs@kpler.com), so there is no self-serve key or free tier to verify a live call against; registered here for discovery rather than as a directly usable dataset.

  Fetch test: ⏭ enterprise-only, no self-serve API host/credentials — see notes

  </details>
- **[Global Fishing Watch API (trafic & pêche AIS)](https://gateway.api.globalfishingwatch.org/v3)** (`global_fishing_watch`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🔐 🔒 · ![transport](https://img.shields.io/badge/transport-navy) ![maritime](https://img.shields.io/badge/maritime-teal) ![ocean](https://img.shields.io/badge/ocean-blue) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  Global AIS-based vessel tracking and apparent fishing effort (Global Fishing Watch).
  <details><summary>Détails</summary>

  [maritime · near-real-time & historical] Global AIS-based vessel tracking and apparent fishing effort. Free API token required (Bearer header; register at globalfishingwatch.org). 4wings/report returns gridded activity and events return point/track features — needs a plugin for clean geo parsing.

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[MarineCadastre AIS (USA)](https://marinecadastre.gov/ais/)** (`marine_cadastre`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇺🇸 🟡 · ![transport](https://img.shields.io/badge/transport-navy) ![maritime](https://img.shields.io/badge/maritime-teal) ![ocean](https://img.shields.io/badge/ocean-blue) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  US historical AIS vessel-traffic archive (bulk download, NOAA/BOEM).
  <details><summary>Détails</summary>

  [maritime · historical archive, not real-time] US AIS vessel-traffic archive (NOAA/BOEM): yearly/daily AIS point tracks and vessel-transit-count rasters for US waters. Bulk download (CSV/GeoTIFF), not a live API.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[IMF PortWatch (trafic portuaire & perturbations)](https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services)** (`imf_portwatch`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![transport](https://img.shields.io/badge/transport-navy) ![maritime](https://img.shields.io/badge/maritime-teal) ![economy](https://img.shields.io/badge/economy-gold) ![ocean](https://img.shields.io/badge/ocean-blue)
  Daily AIS-derived port activity and trade-disruption monitoring, ~1800 ports (IMF PortWatch).
  <details><summary>Détails</summary>

  [maritime · daily aggregate, not real-time] portwatch.imf.org open data (ArcGIS Online, f=geojson): AIS-derived port activity and trade disruption monitoring by the IMF and Oxford.

  Fetch test: ∅ 0 feature(s)

  </details>

## 🌊 Oceans & marine

- **[SHOM (littoral & marine)](https://services.data.shom.fr/INSPIRE/wms/r)** (`shom`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇫🇷 ✅ · ![ocean](https://img.shields.io/badge/ocean-blue) ![basemap](https://img.shields.io/badge/basemap-lightgrey)
  France's official nautical charts and coastal/bathymetric data (SHOM).
  <details><summary>Détails</summary>

  Nautical charts, coastal data

  </details>
- **[Copernicus Marine WMTS](https://wmts.marine.copernicus.eu/teroWmts)** (`cmems_wmts`) ![WMTS](https://img.shields.io/badge/WMTS-cadetblue) 🌐 ✅ · ![ocean](https://img.shields.io/badge/ocean-blue) ![climate](https://img.shields.io/badge/climate-orange)
  Copernicus Marine visualisation tiles (ocean physics/biogeochemistry); full data needs an account.
  <details><summary>Détails</summary>

  Visualisation tiles; full data via data.marine.copernicus.eu (account)

  </details>
- **[EMODnet Biology](https://geo.vliz.be/geoserver/Emodnetbio/wfs)** (`emodnet_biology`) ![WFS](https://img.shields.io/badge/WFS-green) 🇪🇺 🟡 · ![ocean](https://img.shields.io/badge/ocean-blue) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  Modelled marine species distribution and abundance layers across European seas (EMODnet).
  <details><summary>Détails</summary>

  Marine species distribution & abundance layers modelled from occurrence data (e.g. cti_* = Community Temperature Index per taxon group, by region — the test layer cti_macroalgae_nw_spain covers NW Spain only, hence the empty fetch elsewhere). GetCapabilities lists the full set of species/region combinations.

  Fetch test: ∅ 0 feature(s)

  </details>
- **[NOAA ERDDAP (CoastWatch)](https://coastwatch.pfeg.noaa.gov/erddap/index.html)** (`noaa_erddap`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![ocean](https://img.shields.io/badge/ocean-blue) ![climate](https://img.shields.io/badge/climate-orange) ![weather](https://img.shields.io/badge/weather-lightblue)
  NOAA's large oceanographic data server (griddap/tabledap) — CoastWatch.
  <details><summary>Détails</summary>

  Huge oceanographic data server (griddap/tabledap)

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[OBIS (biodiversité marine)](https://api.obis.org/v3)** (`obis`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![ocean](https://img.shields.io/badge/ocean-blue) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  Global marine species occurrence records, the ocean counterpart to GBIF (OBIS).
  <details><summary>Détails</summary>

  Ocean Biodiversity Information System — georeferenced marine species occurrence records (WoRMS-aligned taxonomy) contributed by research institutes worldwide, the marine counterpart to GBIF's terrestrial-leaning dataset. Same shape as GBIF (decimalLongitude/decimalLatitude, WKT geometry filter, pagination).

  </details>
- **[NOAA nowCOAST](https://nowcoast.noaa.gov/)** (`noaa_nowcoast`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇺🇸 🟡 · ![weather](https://img.shields.io/badge/weather-lightblue) ![ocean](https://img.shields.io/badge/ocean-blue) ![hazards](https://img.shields.io/badge/hazards-red)
  US coastal radar/satellite imagery, marine forecasts, tides and storm tracks (NOAA nowCOAST).
  <details><summary>Détails</summary>

  NOAA's coastal-focused visualization portal — near-real-time radar/satellite imagery, marine forecasts (waves, sea surface temperature), tides/currents, and storm/hurricane tracks over US waters, exposed as ArcGIS MapServer WMS/WMTS per dataset behind the portal.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[TeleGeography Submarine Cable Map](https://www.submarinecablemap.com/api/v3)** (`submarine_cables`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey) ![ocean](https://img.shields.io/badge/ocean-blue)
  World submarine telecom cable routes and landing points, one GeoJSON file (TeleGeography).
  <details><summary>Détails</summary>

  Open GeoJSON, whole world in one file — the static endpoint ignores bbox/params, so it returns all features (clip client-side). Cables are lines, landing points are points.

  </details>
- **[OpenSeaMap nautical seamarks (tiles)](https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png)** (`openseamap`) ![XYZ](https://img.shields.io/badge/XYZ-gold) 🌐 🟡 · ![ocean](https://img.shields.io/badge/ocean-blue) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  Nautical seamark overlay tiles: buoys, lights, harbours, depth marks (OpenSeaMap).
  <details><summary>Détails</summary>

  Nautical seamark overlay tiles (buoys, lights, harbours, depth marks) from OSM. Transparent PNG overlay; underlying seamark features are queryable via Overpass (seamark:* tags).

  Fetch test: ⏭ xyz: raster tile template, no queryable layer; see Status for reachability

  </details>

## ⚠️ Hazards & disasters

- **[Géorisques (risques naturels)](https://www.georisques.gouv.fr/services)** (`brgm_risques`) ![WMS](https://img.shields.io/badge/WMS-blue) 🇫🇷 ✅ · ![hazards](https://img.shields.io/badge/hazards-red) ![geology](https://img.shields.io/badge/geology-brown) ![water](https://img.shields.io/badge/water-dodgerblue)
  France's official natural-hazard maps: floods, landslides, clay shrinkage, seismic risk, cavities.
  <details><summary>Détails</summary>

  Risques naturels FR (inondation, mouvements de terrain, retrait-gonflement des argiles, séismes, cavités...). WMS Géorisques — remplace l'ancien geoservices.brgm.fr/risques (fermé). Couches listées en live via GetCapabilities.

  </details>
- **[GDACS (alertes catastrophes)](https://www.gdacs.org/gdacsapi)** (`gdacs`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![hazards](https://img.shields.io/badge/hazards-red)
  Near-real-time global disaster alerts: earthquakes, cyclones, floods, volcanoes, droughts, tsunamis.
  <details><summary>Détails</summary>

  Global Disaster Alert and Coordination System — near-real-time alerts (green/orange/red severity) for earthquakes, tropical cyclones, floods, volcanoes, droughts and tsunamis worldwide, each event a point/polygon with a magnitude/impact estimate. Complements usgs_earthquake (quakes only) with a broader, multi-hazard feed.

  </details>
- **[NOAA NWS API (météo & alertes)](https://api.weather.gov)** (`nws_api`) ![REST](https://img.shields.io/badge/REST-orange) 🇺🇸 🟡 · ![weather](https://img.shields.io/badge/weather-lightblue) ![hazards](https://img.shields.io/badge/hazards-red)
  US National Weather Service alerts, point forecasts and observation stations.
  <details><summary>Détails</summary>

  US National Weather Service — active weather alerts/warnings (polygons), point forecasts, and observation station metadata/readings. `alerts/active` filters by `area` (state code) or `point` (lat,lon), not a bbox param, hence the 400 on a plain bbox query.

  Fetch test: ❌ HTTPError: 400 Client Error: Bad Request for url: https://api.weather.gov/alerts/active?bbox=-77.05%2C38.88%2C-76.95%2C38.95&size=200&format=json

  </details>
- **[USGS Earthquake API](https://earthquake.usgs.gov/fdsnws/event/1)** (`usgs_earthquake`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![hazards](https://img.shields.io/badge/hazards-red) ![geology](https://img.shields.io/badge/geology-brown)
  Global earthquake catalogue with magnitude/location/time (USGS).
  <details><summary>Détails</summary>

  fdsnws uses min/maxlongitude & min/maxlatitude (not a bbox param) and returns GeoJSON; the REST layer now parses FeatureCollection geometry. Default test bbox is central California (active).

  </details>
- **[ReliefWeb API (humanitaire)](https://api.reliefweb.int/v2)** (`reliefweb`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ❌ · ![hazards](https://img.shields.io/badge/hazards-red)
  UN OCHA's humanitarian situation reports and disaster event records (mostly non-geospatial).
  <details><summary>Détails</summary>

  UN OCHA's humanitarian information hub — situation reports, disaster event records (linked to GLIDE numbers), and country crisis profiles; not primarily geospatial (reports/text with a country/disaster reference), so pair it with GDACS or nasa_firms/effis for the actual event geometry. The 403 above looks like it needs an `appname=` query param on every request (bare GETs get blocked), which the test URL already supplies.

  </details>
- **[GDELT 2.0 (événements & actualités géolocalisés)](https://api.gdeltproject.org/api/v2)** (`gdelt`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![hazards](https://img.shields.io/badge/hazards-red) ![meta](https://img.shields.io/badge/meta-blueviolet)
  Global news and events monitor, updated every 15 minutes, with geolocated place mentions (GDELT).
  <details><summary>Détails</summary>

  World news and events, updated every 15 min; rate-limited to about one request per 5 s. The companion geo/geo endpoint returns GeoJSON of place mentions.

  Fetch test: ❌ HTTPError: 429 Client Error: Too Many Requests for url: https://api.gdeltproject.org/api/v2/doc/doc?bbox=1.4%2C43.55%2C1.5%2C43.65&size=200&format=json

  </details>
- **[FEMA OpenFEMA (catastrophes USA)](https://www.fema.gov/api/open)** (`fema_openfema`) ![REST](https://img.shields.io/badge/REST-orange) 🇺🇸 🟡 · ![hazards](https://img.shields.io/badge/hazards-red)
  US disaster declarations, flood insurance claims and mitigation data (FEMA OpenFEMA).
  <details><summary>Détails</summary>

  US disaster and hazard data (declarations, NFIP claims, mitigation). Mostly tabular by FIPS; flood-zone POLYGONS are the separate NFHL ArcGIS/WMS service at hazards.fema.gov. Uses $-prefixed OData params.

  Fetch test: ∅ 0 feature(s)

  </details>
- **[ACLED (conflits & manifestations géolocalisés)](https://api.acleddata.com)** (`acled`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ❌ [🔒](https://developer.acleddata.com/) · ![hazards](https://img.shields.io/badge/hazards-red)
  Geolocated political violence and protest events worldwide, updated weekly (ACLED).
  <details><summary>Détails</summary>

  Armed Conflict Location & Event Data: geolocated political violence and protest events, weekly updates. Free registration; set ACLED_KEY and ACLED_EMAIL in .env (sent as key/email query params).

  </details>
- **[RainViewer precipitation radar (nowcast)](https://api.rainviewer.com/public)** (`rainviewer`) ![XYZ](https://img.shields.io/badge/XYZ-gold) 🌐 🟡 · ![weather](https://img.shields.io/badge/weather-lightblue) ![hazards](https://img.shields.io/badge/hazards-red)
  Global live precipitation radar with ~2h nowcast, as XYZ tiles (RainViewer).
  <details><summary>Détails</summary>

  Global live precipitation radar with ~2 h nowcast. weather-maps.json lists timestamped frames whose paths build XYZ tiles ({host}{path}/{size}/{z}/{x}/{y}/{color}/{options}.png). The XYZ layer resolves the manifest (frame nearest the requested datetime, else the newest) and mosaics the tiles into a raster; also drapeable client-side as live tiles. Frames are ephemeral — a fetched raster is a snapshot.

  Fetch test: ⏭ xyz: raster tile template, no queryable layer; see Status for reachability

  </details>

## 🌦️ Weather & climate

- **[Copernicus Climate Data Store API](https://cds.climate.copernicus.eu/api)** (`copernicus_cds`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 [🔒](https://cds.climate.copernicus.eu/how-to-api) · ![climate](https://img.shields.io/badge/climate-orange) ![weather](https://img.shields.io/badge/weather-lightblue)
  ERA5 reanalysis and seasonal climate forecasts via the Copernicus Climate Data Store.
  <details><summary>Détails</summary>

  ERA5, seasonal forecasts... (cdsapi client)

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[ECMWF Open Data (prévisions)](https://data.ecmwf.int/forecasts/)** (`ecmwf_opendata`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![weather](https://img.shields.io/badge/weather-lightblue)
  ECMWF's free global weather forecast output (HRES/ENS) as GRIB2 files.
  <details><summary>Détails</summary>

  ECMWF's free, no-key global NWP output (HRES/ENS forecasts) as raw GRIB2 files organised by run date/time under this index — a plain HTTP file listing, not a queryable REST API; download and parse the GRIB files client-side (e.g. with cfgrib/xarray).

  Fetch test: ❌ HTTPError: 404 Client Error: Not Found for url: https://data.ecmwf.int/forecasts//forecasts?bbox=1.4%2C43.55%2C1.5%2C43.65&size=200&format=json

  </details>
- **[NOAA NOMADS (GFS...)](https://nomads.ncep.noaa.gov/)** (`noaa_nomads`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![weather](https://img.shields.io/badge/weather-lightblue) ![climate](https://img.shields.io/badge/climate-orange)
  NOAA's operational forecast model archive: GFS, GEFS ensemble, HRRR, wave model, as GRIB2 files.
  <details><summary>Détails</summary>

  NOAA's operational NWP model output archive — GFS, GEFS ensemble, HRRR (US high-resolution), WW3 wave model, and more, as GRIB2 files (with a GDS subsetting service for some models). A file server, not a REST/OGC API — pick a model/run, then download/subset the GRIB.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Open-Meteo (prévisions & archives météo)](https://api.open-meteo.com/v1)** (`open_meteo`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![weather](https://img.shields.io/badge/weather-lightblue) ![climate](https://img.shields.io/badge/climate-orange)
  Free weather forecasts (16 days) and historical reanalysis since 1940, no key needed (Open-Meteo).
  <details><summary>Détails</summary>

  Free weather API, no key: forecasts (16 days) and, on archive-api.open-meteo.com/v1/archive, historical reanalysis since 1940. Point-based (latitude/longitude params), returns JSON time-series arrays — no bbox and not per-record lon/lat, so registered for discovery; fetch it client-side or via a plugin. `models=` selects a specific NWP instead of the multi-model blend, e.g. Météo-France's meteofrance_arome_france_hd (1.5 km, France) and meteofrance_arpege_europe (Iberia/wider Europe) — a useful pairing for a fine-grained 10 m wind layer, falling back to `best_match` outside AROME/ARPEGE coverage.

  Fetch test: ❌ JSONDecodeError: Expecting value: line 1 column 1 (char 0)

  </details>
- **[NOAA Space Weather Prediction Center](https://services.swpc.noaa.gov)** (`noaa_swpc`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![weather](https://img.shields.io/badge/weather-lightblue)
  Live space-weather feeds: aurora forecast, Kp index, solar wind (NOAA SWPC).
  <details><summary>Détails</summary>

  Live space-weather feeds (aurora forecast, Kp index, solar wind). The aurora grid is a positional [lon, lat, value] array rather than flat lon/lat records, so it needs custom parsing to geolocate.

  Fetch test: ∅ 0 feature(s)

  </details>

## 🌡️ Climate

- **[Köppen-Geiger climate classification (GloH2O)](https://www.gloh2o.org/koppen/)** (`koppen_geiger`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![climate](https://img.shields.io/badge/climate-orange)
  Global Köppen-Geiger climate classification maps, historical and future scenarios (GloH2O).
  <details><summary>Détails</summary>

  Global Köppen-Geiger climate classification maps (Beck et al. 2018 & 2023) as downloadable GeoTIFF rasters at 1 km to 1° resolution, for 1901-1930 through 1991-2020 and future scenarios. Portal/archive download (Zenodo/figshare), no live tile or query API — register for discovery, load the GeoTIFF locally.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>

## 🐾 Biodiversity & ecology

- **[INPN / MNHN biodiversité](https://odata-inpn.mnhn.fr)** (`inpn_odata`) ![REST](https://img.shields.io/badge/REST-orange) 🇫🇷 🟡 · ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  French species occurrence records, taxonomy and protected-area data from INPN/MNHN.
  <details><summary>Détails</summary>

  OData interface to the Inventaire National du Patrimoine Naturel (INPN) — French species occurrence records, taxonomic referential (TAXREF), and protected/inventoried area attributes (ZNIEFF, Natura 2000). No curated bbox-filterable layer here (no `test`/`layers` set); browse the OData service document to find an entity set, then query it directly.

  Fetch test: ⏭ no testable layer (REST without curated layers)

  </details>
- **[Natura 2000 (EEA bio Discomap)](https://bio.discomap.eea.europa.eu/arcgis/rest/services)** (`eea_natura2000`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇪🇺 🟡 · ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  Natura 2000 protected-site boundaries and Habitats/Birds Directive reporting layers (EEA).
  <details><summary>Détails</summary>

  ArcGIS Server root for EEA's "bio" Discomap host — biodiversity-focused folders: N2K_Backbone and ProtectedSites (Natura 2000 site boundaries/attributes), Article17 and Article_12 (Habitats/Birds Directive conservation-status reporting), EUNIS (habitat classification), BioRegions, HNV (High Nature Value farmland), and MAES (ecosystem assessment). Drill into a folder's MapServer/FeatureServer via the ArcGIS REST API.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[GBIF (occurrences d'espèces)](https://api.gbif.org/v1)** (`gbif`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  Species occurrence records worldwide, the largest open biodiversity database (GBIF).
  <details><summary>Détails</summary>

  Species occurrences worldwide. productType = scientific name (e.g. Gypaetus barbatus)

  Fetch test: ⏭ free-form productType ('scientificName') — set test.layer to a real value to verify

  </details>
- **[iNaturalist API](https://api.inaturalist.org/v1)** (`inaturalist`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ✅ · ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  Citizen-science species observations worldwide, with photos (iNaturalist).
  <details><summary>Détails</summary>

  Observations are returned with a per-record `geojson` Point (no flat lon/lat); the bbox uses nelat/nelng/swlat/swlng. taxa/places are queried differently (q=/lat,lng).

  </details>
- **[Protected Planet (WDPA)](https://www.protectedplanet.net/)** (`protected_planet`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 🔒 · ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  World Database on Protected Areas — the global protected/conserved area registry.
  <details><summary>Détails</summary>

  World Database on Protected Areas

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Map of Life API](https://mol.org/api)** (`map_of_life`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  Species range-map aggregator combining GBIF, OBIS, eBird, IUCN and more behind one API.
  <details><summary>Détails</summary>

  Species range-map aggregator (queries GBIF/OBIS/eBird/IUCN and other sources behind one API) — species lookup by name returns known distribution/range polygons. The species endpoint above 404s/empty-bodies on a bare GET (see fetch below); it needs a species-name path segment, not a query string.

  Fetch test: ❌ JSONDecodeError: Expecting value: line 1 column 1 (char 0)

  </details>
- **[Movebank (suivi GPS d'animaux)](https://www.movebank.org/movebank/service)** (`movebank`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ❌ · ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  GPS animal-tracking data by research study, worldwide (Movebank).
  <details><summary>Détails</summary>

  Animal tracking (GPS telemetry). productType = a numeric study id → GPS fixes (points per individual); a non-numeric value → the public study list (one point per study). Most studies need a free Movebank login + licence acceptance; the public API is rate-limited.

  </details>

## 🚧 Infrastructure

- **[EIA (énergie & réseau électrique USA)](https://api.eia.gov/v2)** (`eia`) ![REST](https://img.shields.io/badge/REST-orange) 🇺🇸 🔐 🔒 · ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  US power plants, generation, grid operations and energy prices (EIA).
  <details><summary>Détails</summary>

  US energy data: power plants, generation, grid operations, prices. Free API key required (api_key query param). Tabular; geolocate facilities by plant id.

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[WRI Global Power Plant Database](https://datasets.wri.org/dataset/globalpowerplantdatabase)** (`global_power_plant`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  ~35,000 power plants worldwide with capacity, fuel type and location (WRI).
  <details><summary>Détails</summary>

  About 35 000 power plants worldwide with capacity, fuel and lon/lat. Downloadable CSV (point data) — load directly; not a live API.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Mapillary (photos de rue crowdsourcées)](https://graph.mapillary.com)** (`mapillary`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 ❌ [🔒](https://www.mapillary.com/dashboard/developers) · ![imagery](https://img.shields.io/badge/imagery-blue) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  Crowdsourced street-level imagery and derived map features (Mapillary).
  <details><summary>Détails</summary>

  Street-level imagery coverage and derived map features. Free token (MLY_TOKEN in .env, sent as access_token); responses are JSON {data:[...]} with GeoJSON geometries.

  </details>
- **[OpenCellID (antennes cellulaires)](https://opencellid.org)** (`opencellid`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 [🔒](https://opencellid.org/register.php) · ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  World's largest open database of cell towers (GSM/LTE/5G) — OpenCellID.
  <details><summary>Détails</summary>

  World's largest open database of cell towers (GSM/LTE/5G). Free key (OPENCELLID_KEY in .env); bulk CSV downloads also available per MCC.

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[OpenRailwayMap (rail infrastructure tiles)](https://a.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png)** (`openrailwaymap`) ![XYZ](https://img.shields.io/badge/XYZ-gold) 🌐 🟡 · ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey) ![rail](https://img.shields.io/badge/rail-darkslategray)
  Global railway network overlay tiles: standard, speed, signals, gauge styles (OpenRailwayMap).
  <details><summary>Détails</summary>

  [rail · static infrastructure, no traffic/positions] Global railway network raster tiles from OSM (standard, maxspeed, signals, gauge styles). Overlay tiles (transparent PNG, served at 512 px); a User-Agent header is required (bare requests get HTTP 403). Underlying vector rail data is queryable via Overpass. No live train positions here — see notes elsewhere in this registry if/when a real-time rail source is added.

  Fetch test: ⏭ xyz: raster tile template, no queryable layer; see Status for reachability

  </details>

## 👥 Population & demographics

- **[World Bank Open Data](https://api.worldbank.org/v2)** (`worldbank`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![economy](https://img.shields.io/badge/economy-gold) ![population](https://img.shields.io/badge/population-hotpink)
  Country-level socio-economic indicators: GDP, growth, HDI, life expectancy and more (World Bank).
  <details><summary>Détails</summary>

  Socio-economic and demographic indicators by country and year — this is the source behind IntMap's choropleth indicators: GDP per capita / PIB par habitant, GDP growth, human development index (HDI) / IDH, life expectancy / esperance de vie, infant & under-5 mortality / mortalite infantile, fertility rate, unemployment / chomage, internet penetration / acces internet, urban population, electricity & safe-water access, CO2 per capita / emissions de CO2, inflation (CPI), Gini inequality, poverty / pauvrete, literacy / alphabetisation, and more (indicator codes like SP.POP.TOTL, NY.GDP.PCAP.CD). Tabular by ISO country code (no geometry) — join to admin boundaries (e.g. eurostat_gisco).

  Fetch test: ∅ 0 feature(s)

  </details>
- **[WorldPop (densité de population)](https://api.worldpop.org/v1)** (`worldpop`) ![REST](https://img.shields.io/badge/REST-orange) 🌐 🟡 · ![population](https://img.shields.io/badge/population-hotpink)
  Gridded population totals for any bbox, worldwide (WorldPop).
  <details><summary>Détails</summary>

  Gridded population via WorldPop's async stats API: submits a bbox and returns one feature (bbox polygon + total_population). productType = dataset (default wpgppop). Rasters also download from hub.worldpop.org.

  Fetch test: ❌ JSONDecodeError: Extra data: line 10 column 2 (char 254)

  </details>
- **[US Census Bureau API (ACS)](https://api.census.gov/data)** (`census_acs`) ![REST](https://img.shields.io/badge/REST-orange) 🇺🇸 🟡 🔒 · ![population](https://img.shields.io/badge/population-hotpink) ![economy](https://img.shields.io/badge/economy-gold)
  US demographics and socio-economics by census geography (Census Bureau ACS).
  <details><summary>Détails</summary>

  US demographics and socio-economics by census geography (state/county/tract/block group). Free API key for sustained use (key= param). Returns rows keyed by FIPS — join to TIGER boundaries.

  Fetch test: 🔐 auth=api_key, no creds in env

  </details>
- **[Kontur Population (grille H3)](https://www.kontur.io/portfolio/population-dataset/)** (`kontur_population`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![population](https://img.shields.io/badge/population-hotpink)
  Global population on a uniform H3 hex grid, downloadable per country (Kontur).
  <details><summary>Détails</summary>

  Global population on a 400 m H3 hex grid (GeoPackage/GeoParquet download, CC-BY). Not a live API — download per country or worldwide.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>

## 📚 Meta-catalogues & registries

- **[GEE Community Catalog (datasets communautaires Earth Engine)](https://gee-community-catalog.org)** (`gee_community`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 [🔒](https://code.earthengine.google.com/register) · ![meta](https://img.shields.io/badge/meta-blueviolet) ![forest](https://img.shields.io/badge/forest-forestgreen) ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![elevation](https://img.shields.io/badge/elevation-slategray) ![water](https://img.shields.io/badge/water-dodgerblue) ![ocean](https://img.shields.io/badge/ocean-blue) ![fire](https://img.shields.io/badge/fire-orangered) ![climate](https://img.shields.io/badge/climate-orange) ![weather](https://img.shields.io/badge/weather-lightblue) ![soil](https://img.shields.io/badge/soil-sienna) ![agriculture](https://img.shields.io/badge/agriculture-gold) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen) ![population](https://img.shields.io/badge/population-hotpink) ![infrastructure](https://img.shields.io/badge/infrastructure-darkgrey)
  Community-run Earth Engine catalog (~4400 extra datasets) complementing Google's official one.
  <details><summary>Détails</summary>

  Catalogue communautaire Earth Engine (~4400 datasets hébergés dans EE sous projects/sat-io/..., ex. canopée Meta/WRI 1 m — gee-community-catalog.org/projects/meta_trees), complémentaire du catalogue public Google (source `gee`) qui ne les référence pas. Cherchable par mot-clé dans le dépôt awesome-gee-community-datasets; chaque résultat est un id Earth Engine (ex. projects/sat-io/open-datasets/facebook/meta-canopy-height) à utiliser avec la bibliothèque earthengine-api. Mêmes identifiants que gee.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Géocatalogue national (BRGM)](https://www.geocatalogue.fr/geonetwork/srv/fre/csw)** (`geocatalogue_fr`) ![CSW](https://img.shields.io/badge/CSW-grey) 🇫🇷 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet)
  France's national geographic metadata catalogue (datasets + WMS/WFS), operated by BRGM.
  <details><summary>Détails</summary>

  French national geographic metadata catalogue, operated by BRGM (datasets + WMS/WFS services)

  Fetch test: ⏭ csw: metadata catalogue — use `discover`, not fetch; see Status for reachability

  </details>
- **[EEA Discomap (ArcGIS REST)](https://image.discomap.eea.europa.eu/arcgis/rest/services)** (`eea_discomap`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇪🇺 🟡 · ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![water](https://img.shields.io/badge/water-dodgerblue) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen) ![meta](https://img.shields.io/badge/meta-blueviolet)
  EEA's ArcGIS host for Corine Land Cover, CLC+, river networks and other environmental layers.
  <details><summary>Détails</summary>

  ArcGIS Server root for EEA's "image" Discomap host — its folders are individual data services, notably Corine (Corine Land Cover, see also the curated eea_clc2018 WMS), CLC_plus (CLC+ Backbone), EUHydro (river network/drainage), Elevation, Forest_Snapshot (forêt), LUCAS (in-situ land-use survey points), Natura2000, Noise, RiparianZones, SoilSealing, and UrbanAtlas. Each folder is its own MapServer/FeatureServer — drill in with the ArcGIS REST API (?f=json) rather than treating this endpoint itself as one service.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[USGS The National Map (services)](https://apps.nationalmap.gov/services/)** (`usgs_tnm`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇺🇸 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet) ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![elevation](https://img.shields.io/badge/elevation-slategray) ![water](https://img.shields.io/badge/water-dodgerblue)
  USGS's index of live US map services: elevation, hydrography, watersheds, transportation.
  <details><summary>Détails</summary>

  USGS's index of The National Map's live services — 3DEP elevation (see usgs_3dep), NHD (National Hydrography Dataset, rivers/lakes), WBD (Watershed Boundary Dataset), topo basemaps, transportation, structures and governmental units. A portal linking to each dataset's own WMS/WFS/ImageServer, not a single API itself.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[EPA environmental services](https://geopub.epa.gov/arcgis/rest/services)** (`epa_geoservices`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇺🇸 🟡 · ![water](https://img.shields.io/badge/water-dodgerblue) ![hazards](https://img.shields.io/badge/hazards-red) ![meta](https://img.shields.io/badge/meta-blueviolet)
  US EPA environmental services: watersheds, Superfund sites, pesticide use, radiation monitoring.
  <details><summary>Détails</summary>

  EPA's ArcGIS Server index — environmental-compliance and monitoring folders: watersheds/impaired waters (WOUS, OW, OWOWM), Superfund/enforcement sites (OECA, OLEM, OLEM_FedFac), pesticide use (pesticide, OPP), radiation monitoring (RadMap), and screening tools (NEPAssist, monitor). Each folder is its own MapServer/FeatureServer, browsed via the ArcGIS REST API.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Pays-Bas — PDOK](https://service.pdok.nl/)** (`pdok_nl`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇳🇱 ❌ · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![admin](https://img.shields.io/badge/admin-grey) ![meta](https://img.shields.io/badge/meta-blueviolet)
  Netherlands' national geo-data platform — a rich set of per-dataset WMS/WFS/WMTS services (PDOK).
  <details><summary>Détails</summary>

  Very rich (WMS/WFS/WMTS/OGC API per dataset)

  </details>
- **[Belgique — Geopunt Vlaanderen](https://geo.api.vlaanderen.be/)** (`vlaanderen_be`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇧🇪 🟡 · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![admin](https://img.shields.io/badge/admin-grey) ![meta](https://img.shields.io/badge/meta-blueviolet)
  Flanders' (Belgium) geoportal — basemaps, admin boundaries and topo/orthophoto services (Geopunt).
  <details><summary>Détails</summary>

  Flanders' (Belgium) geoportal (Geopunt) — reference basemaps, administrative boundaries, and topographic/orthophoto services, each dataset exposed as its own WMS/WFS/WMTS under geo.api.vlaanderen.be; browse the portal to find the specific service.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Australie — Geoscience Australia](https://services.ga.gov.au/)** (`ga_australia`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇦🇺 🟡 · ![geology](https://img.shields.io/badge/geology-brown) ![elevation](https://img.shields.io/badge/elevation-slategray) ![meta](https://img.shields.io/badge/meta-blueviolet)
  Geoscience Australia's services portal — per-dataset WMS/WFS.
  <details><summary>Détails</summary>

  Geoscience Australia services portal (WMS/WFS per dataset)

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[Nouvelle-Zélande — LINZ Data Service](https://data.linz.govt.nz/)** (`linz_nz`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇳🇿 🟡 🔒 · ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![admin](https://img.shields.io/badge/admin-grey) ![meta](https://img.shields.io/badge/meta-blueviolet)
  New Zealand's national geo-data platform — WMS/WMTS/WFS services (LINZ Data Service).
  <details><summary>Détails</summary>

  LINZ Data Service — WMS/WMTS/WFS with free API key

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[OneGeology (portail mondial)](https://portal.onegeology.org/)** (`onegeology`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![geology](https://img.shields.io/badge/geology-brown) ![meta](https://img.shields.io/badge/meta-blueviolet)
  Federated portal aggregating ~120 national geological-survey WMS services worldwide.
  <details><summary>Détails</summary>

  Global geological-survey federation portal — aggregates each participating country's own geology WMS (~120 national surveys) behind one viewer/CSW, rather than hosting the map data itself. Searchable via its CSW to find the WMS a given country actually exposes.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[STAC Index (registre des API STAC)](https://stacindex.org/api/catalogs)** (`stac_index`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet)
  Directory of every public STAC catalog — a starting point to find EO data sources.
  <details><summary>Détails</summary>

  JSON list of all public STAC catalogs — ideal starting point

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[GeoSeer (moteur de recherche OGC)](https://www.geoseer.net/)** (`geoseer`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet)
  Search engine indexing millions of public WMS/WFS/WCS/WMTS layers.
  <details><summary>Détails</summary>

  Search engine over millions of WMS/WFS/WCS/WMTS layers

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[INSPIRE Geoportal (catalogue UE)](https://inspire-geoportal.ec.europa.eu/srv/eng/csw)** (`inspire_geoportal`) ![CSW](https://img.shields.io/badge/CSW-grey) 🇪🇺 ❌ · ![meta](https://img.shields.io/badge/meta-blueviolet)
  Pan-European INSPIRE metadata catalogue (~380k dataset/service records).
  <details><summary>Détails</summary>

  Pan-European INSPIRE metadata catalogue (~380k records: datasets + services across all EU)

  Fetch test: ⏭ csw: metadata catalogue — use `discover`, not fetch; see Status for reachability

  </details>
- **[EEA SDI catalogue (CSW)](https://sdi.eea.europa.eu/catalogue/srv/eng/csw)** (`eea_sdi`) ![CSW](https://img.shields.io/badge/CSW-grey) 🇪🇺 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet) ![landcover](https://img.shields.io/badge/landcover-yellowgreen) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen) ![water](https://img.shields.io/badge/water-dodgerblue) ![climate](https://img.shields.io/badge/climate-orange)
  Searchable EU environmental metadata catalogue (EEA Spatial Data Infrastructure).
  <details><summary>Détails</summary>

  Searchable EU environmental metadata catalogue (datasets + services)

  Fetch test: ⏭ csw: metadata catalogue — use `discover`, not fetch; see Status for reachability

  </details>
- **[Ifremer Sextant (CSW)](https://sextant.ifremer.fr/geonetwork/srv/fre/csw)** (`sextant`) ![CSW](https://img.shields.io/badge/CSW-grey) 🇪🇺 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet) ![ocean](https://img.shields.io/badge/ocean-blue) ![biodiversity](https://img.shields.io/badge/biodiversity-limegreen)
  French marine and coastal metadata catalogue (Ifremer Sextant).
  <details><summary>Détails</summary>

  Marine and coastal metadata catalogue (Ifremer Sextant)

  Fetch test: ⏭ csw: metadata catalogue — use `discover`, not fetch; see Status for reachability

  </details>
- **[IGN Géoplateforme catalogue (CSW)](https://data.geopf.fr/csw)** (`geoplateforme_csw`) ![CSW](https://img.shields.io/badge/CSW-grey) 🇫🇷 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet) ![basemap](https://img.shields.io/badge/basemap-lightgrey) ![admin](https://img.shields.io/badge/admin-grey)
  Metadata catalogue of France's national IGN Géoplateforme.
  <details><summary>Détails</summary>

  Metadata catalogue of the French Géoplateforme (IGN national platform)

  Fetch test: ⏭ csw: metadata catalogue — use `discover`, not fetch; see Status for reachability

  </details>
- **[data.gouv.fr (portail open data France)](https://www.data.gouv.fr/api/1)** (`datagouv_fr`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇫🇷 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet)
  France's national open-data portal, ~50k datasets (data.gouv.fr).
  <details><summary>Détails</summary>

  French national open-data portal (~50k datasets: DFCI, risques, PLU, réseaux...), keyword-searchable via its dataset API; records carry the dataset page and resource URLs (many are WMS/WFS/Atom feeds from geo-ide, shapefiles, GeoJSON).

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[data.europa.eu (portail open data européen)](https://data.europa.eu/api/hub/search/ckan)** (`data_europa_eu`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🇪🇺 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet)
  Official EU open-data portal, ~1.9M datasets from every member state (data.europa.eu).
  <details><summary>Détails</summary>

  Official EU open-data portal (~1.9M datasets harvested from every member state, CKAN-compatible search API), keyword-searchable via package_search; records carry distributions (downloads, some WMS/WFS).

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[OpenDataSoft (réseau data.opendatasoft.com)](https://data.opendatasoft.com/api/explore/v2.1)** (`opendatasoft_explore`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet)
  Federated search across every public OpenDataSoft portal, ~35k datasets.
  <details><summary>Détails</summary>

  Federated catalogue of every public OpenDataSoft portal (~35k datasets — many French collectivités: régions, départements, métropoles), keyword-searchable via the Explore API; every record gets a ready GeoJSON export URL.

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[ArcGIS Hub (open data ArcGIS Online)](https://hub.arcgis.com/api/v3)** (`arcgis_hub`) ![REGISTRY](https://img.shields.io/badge/REGISTRY-lightgrey) 🌐 🟡 · ![meta](https://img.shields.io/badge/meta-blueviolet)
  Search across millions of public ArcGIS Online / ArcGIS Hub layers.
  <details><summary>Détails</summary>

  Search API over all public ArcGIS Online / ArcGIS Hub open data (millions of layers, incl. many French services). Records point straight at the ArcGIS REST FeatureServer layer (query with f=geojson).

  Fetch test: ⏭ registry: portal/download dataset, no live layer API (e.g. GeoParquet via CLI); see Status for reachability

  </details>
- **[data.gov catalogue (CKAN)](https://catalog.data.gov/api/3/action/package_search)** (`datagov_us`) ![REST](https://img.shields.io/badge/REST-orange) 🇺🇸 ❌ · ![meta](https://img.shields.io/badge/meta-blueviolet)
  US federal open-data catalogue (CKAN), full-text search over ~300k datasets.
  <details><summary>Détails</summary>

  US federal open-data catalogue (CKAN) — full-text search over ~300k government datasets (not all geospatial); each result's `resources` may include a WMS/WFS/Shapefile/GeoJSON link to follow, this endpoint itself only returns dataset metadata. The 404 below suggests the catalog.data.gov CKAN API path has moved — verify against data.gov's current API docs before relying on it.

  </details>
