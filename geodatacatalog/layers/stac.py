"""STAC provider → list[pystac.Item] (band mapping + cloud filtering, lazy loading)."""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import logging
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import geopandas as gpd
from shapely.geometry import box

from pystac_client.exceptions import APIError as _StacAPIError

from ..base import BaseLayer, LayerKind, LayerResult, retry_on_network_error
from ..request import GeoRequest
from ..utils import _normalise_legend

log = logging.getLogger(__name__)


def _expand_datetime(dt: str, pad_days: int = 15) -> str:
    """A single date → a ±``pad_days`` window; an explicit range (with ``/``) kept.

    A STAC item search at one instant usually returns nothing, so a lone date
    (e.g. the date a user picks in the UI) is widened to
    ``[date-pad_days, date+pad_days]``. Unparseable input is returned untouched.
    """
    if not dt or "/" in dt:
        return dt
    from datetime import date, timedelta
    try:
        d = date.fromisoformat(dt.strip().split("T")[0])
    except ValueError:
        return dt
    return f"{(d - timedelta(days=pad_days)).isoformat()}/{(d + timedelta(days=pad_days)).isoformat()}"


def _datetime_slice_bounds(dt: str | None) -> tuple[str | None, str | None]:
    """Split a ``GeoRequest.datetime`` string into ``(start, end)`` xarray ``.sel`` bounds.

    An explicit ``"start/end"`` range is split as-is; a lone date/datetime is used
    as both bounds, which relies on pandas' partial-string indexing to match every
    timestamp within that day/hour (``.sel(time=slice("2024-06-15", "2024-06-15"))``
    keeps the whole day, not just midnight). ``None``/empty input → ``(None, None)``
    (no bound, i.e. keep everything on that dimension).
    """
    if not dt:
        return None, None
    if "/" in dt:
        start, end = dt.split("/", 1)
        return (start.strip() or None), (end.strip() or None)
    return dt, dt


def _rgb_band_default(items: list) -> list[str] | None:
    """The red/green/blue asset keys of a collection, if discoverable.

    Prefers the ``eo:bands`` ``common_name`` mapping (so 'B04'→red etc. resolve to
    the real asset keys), then plain ``red``/``green``/``blue`` asset names. Used
    as the default when the caller asked for no specific bands, so a true-colour
    view loads instead of every band.
    """
    if not items:
        return None
    assets = getattr(items[0], "assets", {}) or {}
    common: dict[str, str] = {}
    for key, asset in assets.items():
        for b in (getattr(asset, "extra_fields", {}) or {}).get("eo:bands") or []:
            cn = (b.get("common_name") or "").lower()
            if cn in ("red", "green", "blue"):
                common.setdefault(cn, key)
    if all(c in common for c in ("red", "green", "blue")):
        return [common["red"], common["green"], common["blue"]]
    if all(c in assets for c in ("red", "green", "blue")):
        return ["red", "green", "blue"]
    return None


# Media types odc.stac / rasterio cannot read as a plain georeferenced raster.
_NONGEOREF_MEDIA = {
    "application/x-netcdf": "NetCDF",
    "application/netcdf": "NetCDF",
    "application/x-hdf": "HDF",
    "application/x-hdf5": "HDF5",
    "application/vnd.zarr": "Zarr",
    "application/vnd+zarr": "Zarr",
    "application/x-parquet": "tabular (GeoParquet)",
    "application/parquet": "tabular (GeoParquet)",
    "application/vnd.apache.parquet": "tabular (GeoParquet)",
    "application/geoparquet": "tabular (GeoParquet)",
    "application/geo+json": "vector (GeoJSON)",
    "application/json": "vector (GeoJSON)",
}

# Asset roles that mark a helper (not the collection's real data): a tilejson
# endpoint, a rendered preview, a browse/thumbnail image, or a sidecar metadata
# file. These must never drive format detection nor be read as the raster — e.g.
# a `tilejson` is application/json and a `browse` is a plain JPEG (no CRS), while
# the collection's actual data is a sibling COG (or a protected archive).
_HELPER_ROLES = frozenset({"tiles", "overview", "thumbnail", "browse", "metadata"})

# File-extension → non-georeferenced format label, for assets whose ``media_type``
# is absent (NASA CMR-STAC items leave it null). Mirrors ``_NONGEOREF_MEDIA`` and
# adds container formats that aren't rasters at all (a zipped .hgt, a tarball).
_NONGEOREF_EXT = {
    ".nc": "NetCDF", ".nc4": "NetCDF", ".cdf": "NetCDF",
    ".hdf": "HDF", ".hdf5": "HDF5", ".h5": "HDF5", ".he5": "HDF5",
    ".zarr": "Zarr",
    ".parquet": "tabular (GeoParquet)", ".pq": "tabular (GeoParquet)",
    ".geojson": "vector (GeoJSON)",
    ".zip": "archived (.zip)", ".tar": "archived (.tar)",
    ".tgz": "archived (.tar.gz)", ".gz": "archived (.gz)",
}


def _asset_mt(asset) -> str:
    """Bare media type of a STAC asset (parameters stripped, lower-cased)."""
    return (getattr(asset, "media_type", "") or "").split(";")[0].strip().lower()


def _asset_ext(asset) -> str:
    """Lower-cased file extension of an asset's href (query string stripped)."""
    from os.path import splitext
    from urllib.parse import urlsplit
    return splitext(urlsplit(getattr(asset, "href", "") or "").path)[1].lower()


def _nongeoref_label(asset) -> str | None:
    """Non-georef format label from an asset's media type, or its href extension
    when the media type is absent (NASA CMR items carry null types)."""
    mt = _asset_mt(asset)
    if mt:
        return _NONGEOREF_MEDIA.get(mt)
    return _NONGEOREF_EXT.get(_asset_ext(asset))


def _is_helper_asset(asset) -> bool:
    """True for tilejson / preview / browse / thumbnail / metadata sidecar assets."""
    return bool(set(getattr(asset, "roles", None) or []) & _HELPER_ROLES)


def _cog_data_bands(items: list) -> list[str]:
    """Data-asset keys that are COG/GeoTIFF (readable by odc.stac), helpers excluded.

    Used as the default band set when nothing else selects bands, so a collection
    that ships an unreadable sibling asset (e.g. MODIS' ``.hdf`` next to per-band
    COGs) still loads through the raster path instead of tripping on the HDF.
    """
    if not items:
        return []
    assets = getattr(items[0], "assets", {}) or {}
    out = []
    for key, a in assets.items():
        roles = set(getattr(a, "roles", None) or [])
        if _is_helper_asset(a):
            continue
        if "tiff" in _asset_mt(a) and ("data" in roles or not roles):
            out.append(key)
    return out


def _asset_hrefs(items: list, bands: list[str] | None) -> list[str]:
    """The href(s) odc.stac would have tried to read for this item/band selection.

    Used to name the actual URL in a raster-load error, instead of leaving the
    user to guess which asset (of possibly many) failed.
    """
    if not items:
        return []
    assets = getattr(items[0], "assets", {}) or {}
    if bands:
        keys = [k for k in bands if k in assets]
    else:  # prefer the real data assets over browse/thumbnail helpers
        keys = [k for k, a in assets.items() if not _is_helper_asset(a)] or list(assets)
    return [assets[k].href for k in keys if k in assets]


def _nongeoref_format(items: list, bands: list[str] | None) -> str | None:
    """Name the non-georeferenced data format of a collection, if that's the cause.

    Returns a human label (e.g. ``"NetCDF"``) when the asset(s) odc.stac would read
    carry a media type the COG/raster path can't open, so the caller can report the
    real reason instead of a misleading auth error; ``None`` when no such asset is found.

    A collection that also ships a readable COG data asset is NOT flagged: MODIS,
    for one, exposes an ``.hdf`` alongside per-band COGs, and helper assets (a
    ``tilejson``, a rendered preview) are ignored entirely so they can't be
    mistaken for the data (a tilejson is ``application/json``, not vector data).

    Assets are merged across all ``items``, not just the first: a collection can
    split its variables across sibling items (era5-pds: accumulated/forecast
    variables only live on the ``fc`` item, instantaneous ones on ``an``), and
    looking at a single item would miss a requested band that happens to sit on
    a later item — falling through to the raster path and failing there with an
    opaque odc.stac "No such band/alias" instead of routing to the Zarr/NetCDF
    reader.
    """
    if not items:
        return None
    assets: dict = {}
    for it in items:
        for k, a in (getattr(it, "assets", {}) or {}).items():
            assets.setdefault(k, a)
    if bands:
        keys = [b for b in bands if b in assets]
    else:
        keys = [k for k in assets if not any(
            s in k.lower() for s in ("thumbnail", "metadata", "index", "mtd_"))]
    cand = [assets[k] for k in keys if not _is_helper_asset(assets[k])]
    # A readable COG/GeoTIFF is present → the raster path can open it.
    if any("tiff" in _asset_mt(a) for a in cand):
        return None
    for a in cand:
        label = _nongeoref_label(a)
        if label:
            return label
    return None


_ZARR_MEDIA = ("application/vnd.zarr", "application/vnd+zarr")


def _is_zarr_asset(asset) -> bool:
    mt = (getattr(asset, "media_type", "") or "").split(";")[0].strip().lower()
    return mt in _ZARR_MEDIA


def zarr_store_url(asset) -> str:
    """Plain-https URL for a Zarr STAC asset (abfs/s3/gs → https).

    A Zarr store is read over plain HTTP, but STAC assets often point at object
    storage with a cloud-native scheme (``abfs://`` on Planetary Computer,
    ``s3://``, ``gs://``). Rewrite those to the public https endpoint so the store
    is loadable over HTTP. Authentication (the SAS token PC mints via
    ``planetary_computer.sign``) is intentionally NOT added here — that comes in a
    later step; open data stores work as-is.
    """
    from urllib.parse import urlsplit

    href = asset.href
    if href.lower().startswith(("http://", "https://")):
        return href
    parts = urlsplit(href)
    scheme, container, path = parts.scheme.lower(), parts.netloc, parts.path.lstrip("/")
    if scheme in ("abfs", "abfss", "az"):
        so = (getattr(asset, "extra_fields", {}) or {}).get("xarray:open_kwargs", {})
        account = (so.get("storage_options") or {}).get("account_name")
        if account:
            return f"https://{account}.blob.core.windows.net/{container}/{path}"
    elif scheme == "s3":
        return f"https://{container}.s3.amazonaws.com/{path}"
    elif scheme in ("gs", "gcs"):
        return f"https://storage.googleapis.com/{container}/{path}"
    return href  # unknown scheme → hand back the raw href


def zarr_passthrough(items: list, bands: list[str] | None,
                     bbox: tuple | None = None) -> dict | None:
    """Describe a Zarr STAC collection for client-side rendering, or ``None``.

    Collections whose data assets are Zarr (e.g. Planetary Computer's ``era5-pds``,
    one asset per climate variable) can't go through the odc.stac/rasterio raster
    path. Instead of loading them, return a passthrough payload pointing at
    a single variable's Zarr store so a client loads it directly.
    The variable is chosen from ``bands`` (first match) else the first Zarr asset.
    """
    if not items:
        return None

    def _zarr_keys(it) -> list[str]:
        return [k for k, a in (getattr(it, "assets", {}) or {}).items() if _is_zarr_asset(a)]

    # A collection can split its variables across sibling items (era5-pds: an/fc),
    # so resolve the requested variable against whichever item actually carries it.
    item = key = None
    for b in (bands or []):
        item = next((it for it in items if b in _zarr_keys(it)), None)
        if item is not None:
            key = b
            break
    if item is None:                     # no band match → first item with Zarr assets
        item = next((it for it in items if _zarr_keys(it)), None)
        if item is None:
            return None
        key = _zarr_keys(item)[0]
    assets = item.assets
    zarr_keys = _zarr_keys(item)
    chosen = key
    asset = assets[chosen]
    open_kwargs = (getattr(asset, "extra_fields", {}) or {}).get("xarray:open_kwargs", {})
    props = getattr(item, "properties", {}) or {}
    out = {
        "kind": "zarr",
        "url": zarr_store_url(asset),
        "variable": chosen,
        "variables": zarr_keys,          # so the UI can switch variable
        "consolidated": bool(open_kwargs.get("consolidated", False)),
        "item": getattr(item, "id", None),
        "start_datetime": props.get("start_datetime"),
        "end_datetime": props.get("end_datetime"),
    }
    if bbox is not None:
        out["bbox"] = list(bbox)
    return out


def _netcdf_crs(ds, default: str = "EPSG:4326") -> str:
    """Read the CRS from a NetCDF dataset's CF grid-mapping variable.

    Inspects the conventional grid-mapping variable (``grid_mapping_name`` attr, or
    a var named ``crs``/``spatial_ref``/``projection``) for ``spatial_ref`` /
    ``crs_wkt`` / ``epsg_code`` / ``proj4_params``. Falls back to ``default`` (WGS84)
    when no georeferencing is described — most gridded climate/flood NetCDFs are 4326.
    """
    import pyproj
    for name, var in ds.variables.items():
        attrs = var.attrs
        is_gm = ("grid_mapping_name" in attrs
                 or name.lower() in ("crs", "spatial_ref", "projection", "lambert_conformal_conic"))
        if not is_gm:
            continue
        for key in ("spatial_ref", "crs_wkt", "epsg_code", "EPSG_code", "proj4", "proj4_params"):
            val = attrs.get(key)
            if val:
                try:
                    return pyproj.CRS(val).to_string()
                except Exception:
                    pass
    return default


def _netcdf_spatial_dims(ds) -> tuple[str, str]:
    """Return the (y, x) dimension names of a NetCDF dataset (lat/lon, y/x...)."""
    dims = {str(d).lower(): str(d) for d in ds.dims}
    yname = next((dims[k] for k in ("lat", "latitude", "y", "northing") if k in dims), None)
    xname = next((dims[k] for k in ("lon", "longitude", "x", "easting") if k in dims), None)
    if yname is None or xname is None:
        raise RuntimeError(
            f"could not identify spatial dimensions in NetCDF (dims={list(ds.dims)})")
    return yname, xname


@contextlib.contextmanager
def _http_asset_stream(url: str, headers: dict):
    """A readable binary stream for a plain http(s) asset, following redirects.

    Uses ``requests`` rather than fsspec's aiohttp-based HTTPFileSystem: several
    cloud-hosted DAACs (e.g. NASA's LP DAAC "Thin Egress App") answer an
    authenticated request with a redirect to a temporary, pre-signed S3/CloudFront
    URL — and that URL rejects the request with 403 Forbidden if the original
    ``Authorization`` header is echoed back to it (two conflicting auth mechanisms).
    ``requests`` already drops the header on a cross-host redirect by default;
    fsspec's session-level headers don't, so downloads of these sources silently
    403'd. ``raise_for_status`` also turns a bad response into a clear error
    naming the real HTTP status instead of an opaque stream failure downstream.
    """
    import requests

    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        r.raw.decode_content = True
        yield r.raw


class STACLayer(BaseLayer):
    """
    STAC catalogue accessed through pystac-client + odc-stac.
    Search returns raw items; raster loading happens on demand via ``.load()``.

    Example::
        STACLayer(
            layer_id="sentinel2",
            url="https://earth-search.aws.element84.com/v1",
            collection="sentinel-2-l2a",
            bands=["red", "green", "blue", "nir"],
            cloud_cover=20,
            band_mapping={"aot": "aerosol", "blue": "blue", "red": "red"},
        )
    """

    kind = LayerKind.STAC_ITEMS

    def __init__(self, layer_id, url, collection,
                 bands: list[str] | None = None,
                 cloud_cover: int | None = None,
                 cloud_cover_aoi: int | None = None,
                 image_coverage_threshold: int = 0,
                 band_mapping: dict[str, str] | None = None,
                 legend: dict | None = None,
                 sort_by: Literal["cloud_cover", "date", "date_asc"] | None = None,
                 static: bool = False,
                 headers: dict[str, str] | None = None,
                 aws_config: dict | None = None,
                 no_sign_request: bool = False,
                 **kwargs):
        super().__init__(layer_id, url, **kwargs)
        self.search = SimpleNamespace(
            static=static,
            sort_by=sort_by,
        )
        self.auth = SimpleNamespace(
            headers=headers or {},
            aws_config=aws_config,
            no_sign_request=no_sign_request,
        )
        self.product = SimpleNamespace(
            collection=collection,
            bands=bands,
            band_mapping=band_mapping or {},
            legend=legend or {},
            cloud_cover=cloud_cover,
            cloud_cover_aoi=cloud_cover_aoi,
            image_coverage_threshold=image_coverage_threshold,
        )

    async def fetch_async(self, request, _cache, _session, parallel=True):
        from urllib.parse import urlparse
        import time
        domain = urlparse(self.url).netloc or self.url
        log.info(f"[{self.layer_id}] \U0001f310 requesting {domain} (STAC search) …")
        t0 = time.monotonic()
        loop = asyncio.get_event_loop()
        items = await loop.run_in_executor(None, self._stac_search, request)
        log.info(f"[{self.layer_id}] ✓ {domain} responded in {time.monotonic() - t0:.1f}s")

        if not items:
            log.warning(f"[{self.layer_id}] No STAC item found")
            return LayerResult(self.layer_id, self.kind, [], request.crs,
                               request.bbox, self.url)

        log.info(f"[{self.layer_id}] {len(items)} STAC items")

        if self.product.cloud_cover_aoi is not None or self.product.image_coverage_threshold > 0:
            items = await loop.run_in_executor(
                None, self._validate_items, items, request
            )
            if not items:
                log.warning(f"[{self.layer_id}] No item left after cloud/coverage filtering")
                return LayerResult(self.layer_id, self.kind, [], request.crs,
                                   request.bbox, self.url)

        for item in items:
            item.properties["geodatacatalog:layer_id"] = self.layer_id

        cloud_cov = float(items[0].properties.get("eo:cloud_cover", float("nan")))
        return LayerResult(self.layer_id, self.kind, items, request.crs,
                           request.bbox, self.url,
                           cloud_coverage=cloud_cov if not math.isnan(cloud_cov) else None)

    def load(
        self,
        items: list,
        request: GeoRequest,
        bands: list[str] | None = None,
    ):
        """Load the selected STAC items via odc.stac.load.

        Band precedence: explicit ``bands`` arg → ``request.bands`` (set by the
        caller) → ``product.bands`` → band_mapping → RGB-if-available.
        """
        import math
        import odc.stac
        from odc.geo import GeoBox
        import pyproj

        assert request.bbox is not None, "GeoRequest.bbox is required to load STAC items"
        bbox_4326 = request.bbox
        res_m = request.resolution

        # Native CRS advertised by the items (proj:epsg / proj:code), if any.
        native_crs = None
        for item in items:
            epsg = item.properties.get("proj:epsg")
            if epsg:
                native_crs = f"EPSG:{epsg}"
                break
            code = item.properties.get("proj:code")
            if code:
                native_crs = code
                break

        # crs=None → load in the items' native CRS (no on-the-fly warp)
        target_crs = request.crs or native_crs or "EPSG:4326"
        if request.crs is None:
            log.info(f"[{self.layer_id}] crs=None → native CRS: {target_crs}")

        def _geobox_for(crs: str) -> GeoBox:
            crs_obj = pyproj.CRS(crs)
            # request.bbox is always EPSG:4326 — reproject it when the CRS is metric
            if crs_obj.is_geographic:
                bb = bbox_4326
                lat_c = (bbox_4326[1] + bbox_4326[3]) / 2
                res = res_m / (111320.0 * math.cos(math.radians(lat_c))) if res_m else res_m
            else:
                from pyproj import Transformer
                t = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
                x0, y0 = t.transform(bbox_4326[0], bbox_4326[1])
                x1, y1 = t.transform(bbox_4326[2], bbox_4326[3])
                bb = (x0, y0, x1, y1)
                res = res_m  # already metres, metric CRS
            return GeoBox.from_bbox(bb, crs=crs, resolution=res)

        geobox = _geobox_for(target_crs)

        effective_bands = bands or request.bands or self.product.bands
        load_kwargs: dict[str, Any] = dict(geobox=geobox)
        if effective_bands:
            load_kwargs["bands"] = effective_bands
        elif self.product.band_mapping:
            load_kwargs["bands"] = [
                v for v in self.product.band_mapping.values()
                if not any(s in v for s in ("MTD_", "thumbnail", "metadata"))
            ]
        else:
            rgb = _rgb_band_default(items)  # no bands asked → true-colour, not every band
            if rgb:
                load_kwargs["bands"] = rgb
                log.info(f"[{self.layer_id}] no bands requested → RGB default {rgb}")
            else:
                # No RGB triple: default to the readable COG data assets rather than
                # letting odc.stac auto-include every asset (which would pull in an
                # unreadable sibling, e.g. MODIS' .hdf, and fail the whole read).
                cogs = _cog_data_bands(items)
                if cogs:
                    load_kwargs["bands"] = cogs
                    log.info(f"[{self.layer_id}] no bands requested → COG data bands {cogs}")

        # NetCDF/HDF5 assets can't go through odc.stac/rasterio (no COG) — read them
        # directly with xarray, windowed to the AOI. Detected from the asset media type.
        nc_fmt = _nongeoref_format(items, load_kwargs.get("bands"))
        if nc_fmt in ("NetCDF", "HDF", "HDF5"):
            return self._load_netcdf(items, request, target_crs, bbox_4326,
                                     res_m, load_kwargs.get("bands"))
        # Zarr cubes aren't georeferenced rasters odc.stac can open either, but they
        # can still be read with xarray directly (e.g. Planetary Computer's era5-pds,
        # whose assets are per-variable Zarr).
        if nc_fmt == "Zarr":
            return self._load_zarr(items, request, target_crs, bbox_4326,
                                   res_m, load_kwargs.get("bands"))
        # Other non-georeferenced formats (vector, tabular) can't be read as a raster
        # at all — reporting it here avoids an opaque IndexError deep inside odc.stac.
        if nc_fmt:
            hrefs = _asset_hrefs(items, load_kwargs.get("bands"))
            href_note = f" ({hrefs[0]})" if hrefs else ""
            raise RuntimeError(
                f"[{self.layer_id}] this collection serves {nc_fmt} data{href_note}, not "
                f"cloud-optimized GeoTIFFs — it can't be displayed through the "
                f"raster-image path. Use a COG-based layer instead."
            )

        signer = self._asset_signer()
        if signer is not None:
            load_kwargs["patch_url"] = signer

        log.info(f"[{self.layer_id}] odc.stac.load → {len(items)} items, bands={load_kwargs.get('bands', 'all')}, res={res_m}m, crs={request.crs}")
        with _s3_auth_context(self.auth.aws_config, self.auth.no_sign_request):
            try:
                ds = odc.stac.load(items, **load_kwargs)
            except (AssertionError, IndexError, KeyError) as e:
                # odc.stac couldn't build a georeferenced raster from these items.
                # Tell the common causes apart so the caller gets a real reason
                # instead of a bare error (e.g. "list index out of range"):
                #  - a non-COG data format the raster path can't read (vector/tabular);
                #  - an auth-gated / off-cloud asset (odc-geo asserts the read has a
                #    CRS → AssertionError);
                #  - items that expose no readable raster band at all, so odc's own
                #    band resolution trips an IndexError/KeyError.
                fmt = _nongeoref_format(items, load_kwargs.get("bands"))
                if fmt:
                    hrefs = _asset_hrefs(items, load_kwargs.get("bands"))
                    href_note = f" ({hrefs[0]})" if hrefs else ""
                    raise RuntimeError(
                        f"[{self.layer_id}] this collection serves {fmt} data{href_note}, not "
                        f"cloud-optimized GeoTIFFs — it can't be displayed through the "
                        f"raster-image path. Use a COG-based layer instead."
                    ) from e
                if isinstance(e, AssertionError):
                    hrefs = _asset_hrefs(items, load_kwargs.get("bands"))
                    href_note = f" ({hrefs[0]})" if hrefs else ""
                    raise RuntimeError(
                        f"[{self.layer_id}] the raster asset could not be read as a "
                        f"georeferenced image{href_note} — the source's data files likely "
                        f"require authentication (e.g. a NASA Earthdata login) or are hosted "
                        f"on another cloud not reachable anonymously. Configure the source's "
                        f"credentials, or use a layer whose data is openly readable."
                    ) from e
                bands_asked = load_kwargs.get("bands")
                hrefs = _asset_hrefs(items, bands_asked)
                href_note = f" ({hrefs[0]})" if hrefs else ""
                raise RuntimeError(
                    f"[{self.layer_id}] could not read this collection as a raster image "
                    f"({type(e).__name__}){href_note} — its items expose no cloud-optimized "
                    f"GeoTIFF band{f' matching {bands_asked}' if bands_asked else ''}. It likely "
                    f"serves a non-raster or non-COG asset type; use a COG-based layer instead."
                ) from e
            # odc.stac.load can warp tiled COGs on the fly, but reprojecting a single
            # GLOBE-wide source (e.g. CGLS DMP/FAPAR 300m, one 4326 COG for the whole
            # planet) into a metric geobox silently yields an all-nodata array — while
            # the same read in the source's native CRS returns data (GDAL's own warp
            # handles it fine). Detect that and retry once in the native CRS; the
            # display path reprojects to EPSG:4326 afterwards anyway.
            if (native_crs and native_crs != target_crs
                    and _all_nodata(ds)):
                log.info(f"[{self.layer_id}] all-nodata in {target_crs} → "
                         f"retrying in native CRS {native_crs}")
                load_kwargs["geobox"] = _geobox_for(native_crs)
                ds = odc.stac.load(items, **load_kwargs)
        log.info(f"[{self.layer_id}] odc.stac.load OK — {dict(ds.sizes)}")
        # Propagate _FillValue → rio.nodata on each variable for clip/explore
        for var in ds.data_vars.values():
            if "_FillValue" in var.attrs and var.rio.nodata is None:
                var.rio.write_nodata(var.attrs["_FillValue"], inplace=True)
        # Attach the configured class legend (pixel value → label/color) to every
        # variable, so a caller can reclassify without re-declaring the classes
        # in code (``da.attrs["legend"]``, same shape as the WMS/WMTS path).
        if self.product.legend:
            legend = _normalise_legend(self.product.legend)
            for var in ds.data_vars.values():
                var.attrs["legend"] = legend
            ds.attrs["legend"] = legend
        return ds

    def download(self, items, request: GeoRequest, dest, bands: list[str] | None = None) -> list[Path]:
        """Save STAC asset file(s) to disk as-is, instead of loading them in memory.

        For assets that can't (or shouldn't) go through ``load``'s raster/xarray
        path — e.g. NASA LP DAAC's zipped, auth-gated ``.hgt`` files, which are
        neither COG nor openly readable — the caller often just wants the original
        file. Band precedence mirrors ``load``: explicit ``bands`` → ``request.bands``
        → ``product.bands`` → every non-helper data asset. One file per (item, asset).

        ``bands`` accepts **either** provider asset keys (``"0_B01_WTR"``) **or**
        the pivot/common names from the product's ``bandMapping`` (``"wtr"``), so
        the same provider-agnostic name works through both ``load()`` and
        ``download()``. Both directions are resolved via ``band_mapping``.
        """
        import shutil
        from urllib.parse import urlsplit

        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        effective_bands = bands or request.bands or self.product.bands
        if isinstance(effective_bands, str):  # a lone band name, not a list
            effective_bands = [effective_bands]

        paths = []
        for item in items:
            assets = item.assets
            if effective_bands:
                keys = []
                for name in effective_bands:
                    key = self._resolve_asset_key(name, assets)
                    if key is not None and key not in keys:
                        keys.append(key)
            else:
                keys = [k for k in assets if not _is_helper_asset(assets[k])]
            if not keys:
                raise RuntimeError(f"[{self.layer_id}] no data asset to download on item {item.id}")
            for key in keys:
                asset = assets[key]
                suffix = Path(urlsplit(asset.href).path).suffix
                out = dest / f"{item.id}_{key}{suffix}"
                log.info(f"[{self.layer_id}] downloading {asset.href} → {out}")
                with self._open_asset_stream(asset) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                paths.append(out)
        log.info(f"[{self.layer_id}] downloaded {len(paths)} file(s) → {dest}")
        return paths

    def _resolve_asset_key(self, name: str, assets: dict) -> str | None:
        """Resolve a band ``name`` to an asset key of ``assets``.

        Accepts a provider asset key as-is, or a pivot/common name declared in
        the product's ``bandMapping`` (``{asset: pivot}``) — the same names
        ``load()`` understands, so ``["wtr"]`` works on both paths. Helper
        assets are never returned; an unresolvable name is skipped (``None``).
        """
        if name in assets and not _is_helper_asset(assets[name]):
            return name
        for asset_key, pivot in self.product.band_mapping.items():
            if pivot == name and asset_key in assets and not _is_helper_asset(assets[asset_key]):
                return asset_key
        return None

    def _open_asset_stream(self, asset):
        """A binary file-like object for a STAC asset's href, honouring per-cloud auth.

        Mirrors the schemes ``_zarr_store``/``_load_netcdf`` already handle:
        Azure Blob (``abfs://``, Planetary Computer's SAS signing via ``adlfs``),
        ``s3://`` (this layer's configured/anonymous AWS credentials), and plain
        http(s) (this layer's custom headers — e.g. a NASA Earthdata bearer token
        — plus Planetary Computer's asset signer, when applicable).
        """
        import fsspec
        from urllib.parse import urlsplit

        href = asset.href
        scheme = urlsplit(href).scheme.lower()
        if scheme in ("abfs", "abfss", "az"):
            so = (getattr(asset, "extra_fields", {}) or {}).get("xarray:open_kwargs", {})
            account = (so.get("storage_options") or {}).get("account_name")
            container, path = urlsplit(href).netloc, urlsplit(href).path.lstrip("/")
            if not account:
                raise RuntimeError(
                    f"[{self.layer_id}] Azure Blob asset with no 'account_name' in "
                    f"its xarray:open_kwargs — can't authenticate")
            try:
                import planetary_computer as pc
            except ImportError as e:
                raise RuntimeError(
                    f"[{self.layer_id}] Azure Blob (abfs://) assets need (free, "
                    f"account-less) SAS signing — install it with: "
                    f"pip install planetary-computer adlfs"
                ) from e
            fs = pc.get_adlfs_filesystem(account, container)
            return fs.open(f"{container}/{path}", "rb")
        if scheme == "s3":
            opts = dict(self.auth.aws_config or {})
            if self.auth.no_sign_request:
                opts["anon"] = True
            return fsspec.filesystem("s3", **opts).open(href, "rb")
        signed = self._asset_signer()
        return _http_asset_stream(signed(href) if signed else href, self.auth.headers or {})

    def _netcdf_asset_href(self, item) -> str:
        """The href of an item's NetCDF/HDF data asset."""
        assets = item.assets
        for key, a in assets.items():
            mt = (getattr(a, "media_type", "") or "").split(";")[0].strip().lower()
            if _NONGEOREF_MEDIA.get(mt) in ("NetCDF", "HDF", "HDF5"):
                return a.href
        for a in assets.values():  # fall back to the data-role asset
            if "data" in (getattr(a, "roles", None) or []):
                return a.href
        raise RuntimeError(f"[{self.layer_id}] no NetCDF data asset on item {item.id}")

    def _load_netcdf(self, items, request, target_crs, bbox_4326, res_m, bands):
        """Load a NetCDF/HDF5 STAC asset through xarray (windowed remote read).

        odc.stac/rasterio can't open these as COGs, so the data asset is read directly
        with xarray over an fsspec HTTP file (HDF5 range requests): the AOI window is
        selected *before* pulling any pixels — these files are routinely global and
        multi-GB — the CRS comes from the CF grid-mapping variable, and the result is
        reprojected to the target CRS. Returns a Dataset shaped like the COG path.
        """
        import io
        import fsspec
        import numpy as np
        import pyproj
        import xarray as xr

        # NetCDF items are whole-globe scenario files, not tiles to mosaic — one is enough.
        item = items[0]
        if len(items) > 1:
            log.info(f"[{self.layer_id}] {len(items)} NetCDF items — reading the first ({item.id})")

        href = self._netcdf_asset_href(item)
        signer = self._asset_signer()
        if signer is not None:
            href = signer(href)

        log.info(f"[{self.layer_id}] reading NetCDF asset via xarray (windowed)")
        f = fsspec.open(href).open()
        try:
            try:
                ds = xr.open_dataset(f, engine="h5netcdf")
            except Exception as e_h5:
                # netCDF3 classic (not HDF5) → pull the bytes and let the default engine read it
                log.info(f"[{self.layer_id}] h5netcdf failed ({e_h5}); falling back to in-memory read")
                f.seek(0)
                ds = xr.open_dataset(io.BytesIO(f.read()))

            data_crs = _netcdf_crs(ds)
            crs_obj = pyproj.CRS(data_crs)
            yname, xname = _netcdf_spatial_dims(ds)

            # AOI bbox is EPSG:4326 — transform it into the data CRS when that is metric
            minx, miny, maxx, maxy = bbox_4326
            if not crs_obj.is_geographic:
                tr = pyproj.Transformer.from_crs("EPSG:4326", data_crs, always_xy=True)
                xs, ys = tr.transform([minx, maxx, minx, maxx], [miny, miny, maxy, maxy])
                minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)

            yvals = ds[yname].values
            ascending = yvals[0] < yvals[-1]
            yslice = slice(miny, maxy) if ascending else slice(maxy, miny)
            ds = ds.sel({yname: yslice, xname: slice(minx, maxx)})
            if ds.sizes.get(yname, 0) == 0 or ds.sizes.get(xname, 0) == 0:
                raise RuntimeError(
                    f"[{self.layer_id}] no NetCDF data in this area — the AOI is outside "
                    f"the dataset's grid, or it has no values here. Try a larger AOI.")

            # keep only spatial data variables (drop the scalar grid-mapping var)
            keep = [v for v in ds.data_vars
                    if yname in ds[v].dims and xname in ds[v].dims]
            if bands:
                requested = [v for v in keep if v in set(bands)]
                keep = requested or keep
            ds = ds[keep]

            # reduce any extra dim (time, return-period...) to its most recent/last slice
            for dim in [d for d in ds.dims if d not in (yname, xname)]:
                ds = ds.isel({dim: -1}, drop=True) if ds.sizes[dim] > 1 else ds.squeeze(dim, drop=True)

            # decimate very large windows so display/explore stays responsive
            ny, nx = ds.sizes[yname], ds.sizes[xname]
            factor = max(ny // 2048, nx // 2048, 1)
            if factor > 1:
                log.info(f"[{self.layer_id}] window {ny}x{nx} → coarsening by {factor}")
                ds = ds.coarsen({yname: factor, xname: factor}, boundary="trim").mean()

            ds = ds.rio.set_spatial_dims(x_dim=xname, y_dim=yname).rio.write_crs(data_crs)
            ds.load()  # pull the windowed pixels while the remote file is open
        finally:
            f.close()

        # propagate fill values to rio.nodata, mirroring the COG path
        for var in ds.data_vars.values():
            fill = var.attrs.get("_FillValue", var.attrs.get("missing_value"))
            if fill is not None and var.rio.nodata is None:
                var.rio.write_nodata(fill, inplace=True)

        if pyproj.CRS(target_crs) != crs_obj:
            ds = ds.rio.reproject(target_crs)

        log.info(f"[{self.layer_id}] NetCDF load OK — {dict(ds.sizes)}, vars={list(ds.data_vars)}")
        return ds

    def _zarr_store(self, asset, storage_options: dict):
        """A store xarray/zarr can open for a STAC Zarr asset's href.

        ``abfs://``/``abfss://`` hrefs (Planetary Computer's Azure Blob storage,
        e.g. era5-pds) need per-request Azure auth, not a plain URL: the container
        rejects anonymous reads (``PublicAccessNotPermitted``), and even a signed
        SAS token doesn't survive being pasted into a bare https URL — zarr joins
        the store root with each internal key (``.zmetadata``, ``lat/.zarray``...),
        which mangles a URL that already carries a ``?`` query string (observed as
        a bare ``KeyError: '.zmetadata'``). ``adlfs`` handles that path-joining
        correctly once it holds the SAS token as a credential, which
        ``planetary_computer.get_adlfs_filesystem`` mints for free (no account
        needed). Other schemes (s3/gs, or already-https) are rewritten to their
        public https endpoint, which open data stores serve anonymously as-is.
        """
        from urllib.parse import urlsplit

        href = asset.href
        scheme, container, path = (urlsplit(href).scheme.lower(), urlsplit(href).netloc,
                                    urlsplit(href).path.lstrip("/"))
        if scheme in ("abfs", "abfss", "az"):
            account = storage_options.get("account_name")
            if not account:
                raise RuntimeError(
                    f"[{self.layer_id}] Zarr asset on Azure Blob storage but no "
                    f"'account_name' in its xarray:open_kwargs — can't authenticate")
            try:
                import planetary_computer as pc
            except ImportError as e:
                raise RuntimeError(
                    f"[{self.layer_id}] Zarr assets on Azure Blob (abfs://) need "
                    f"(free, account-less) SAS signing — install it with: "
                    f"pip install planetary-computer adlfs"
                ) from e
            fs = pc.get_adlfs_filesystem(account, container)
            return fs.get_mapper(f"{container}/{path}")
        return zarr_store_url(asset)

    def _load_zarr(self, items, request, target_crs, bbox_4326, res_m, bands):
        """Load a Zarr STAC asset through xarray (windowed remote read).

        Mirrors ``_load_netcdf``: odc.stac/rasterio can't open Zarr cubes as COGs
        (e.g. Planetary Computer's era5-pds, one asset per climate variable), so the
        chosen variable's store is opened directly with xarray (``_zarr_store``
        resolves the right fsspec store for the asset's cloud). The AOI window is
        selected before pulling any pixels, the CRS comes from the CF grid-mapping
        variable (same convention as NetCDF), and the result is reprojected to the
        target CRS.
        """
        import pyproj
        import xarray as xr

        payload = zarr_passthrough(items, bands, None)
        if payload is None:
            raise RuntimeError(f"[{self.layer_id}] no Zarr data asset found")

        item = next(it for it in items if getattr(it, "id", None) == payload["item"])
        asset = item.assets[payload["variable"]]
        open_kwargs = (getattr(asset, "extra_fields", {}) or {}).get("xarray:open_kwargs", {})
        storage_options = open_kwargs.get("storage_options") or {}

        store = self._zarr_store(asset, storage_options)

        log.info(f"[{self.layer_id}] reading Zarr asset {payload['variable']!r} via xarray")
        ds = xr.open_zarr(store, consolidated=open_kwargs.get("consolidated"), chunks="auto")

        data_crs = _netcdf_crs(ds)  # Zarr climate cubes follow the same CF conventions
        crs_obj = pyproj.CRS(data_crs)
        yname, xname = _netcdf_spatial_dims(ds)

        # AOI bbox is EPSG:4326 — transform it into the data CRS when that is metric
        minx, miny, maxx, maxy = bbox_4326
        if not crs_obj.is_geographic:
            tr = pyproj.Transformer.from_crs("EPSG:4326", data_crs, always_xy=True)
            xs, ys = tr.transform([minx, maxx, minx, maxx], [miny, miny, maxy, maxy])
            minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)

        xvals = ds[xname].values
        if xvals.max() > 180 and (minx < 0 or maxx < 0):
            # Some global climate grids (e.g. ERA5) use a 0-360 longitude
            # convention instead of -180/180 — shift the AOI to match.
            minx, maxx = minx % 360, maxx % 360

        yvals = ds[yname].values
        ascending = yvals[0] < yvals[-1]
        yslice = slice(miny, maxy) if ascending else slice(maxy, miny)
        ds = ds.sel({yname: yslice, xname: slice(minx, maxx)})
        if ds.sizes.get(yname, 0) == 0 or ds.sizes.get(xname, 0) == 0:
            raise RuntimeError(
                f"[{self.layer_id}] no Zarr data in this area — the AOI is outside "
                f"the dataset's grid, or it has no values here. Try a larger AOI.")

        # keep only spatial data variables, defaulting to the passthrough's own pick
        keep = [v for v in ds.data_vars if yname in ds[v].dims and xname in ds[v].dims]
        if bands:
            requested = [v for v in keep if v in set(bands)]
            keep = requested or keep
        elif payload["variable"] in keep:
            keep = [payload["variable"]]
        ds = ds[keep]

        # A time-like dim (datetime64 coord, e.g. ERA5's hourly `time`) is windowed to
        # the requested datetime range instead of collapsed, so callers asking for a
        # range keep the full time series. Other extra dims (forecast horizon, bounds...)
        # still reduce to their most recent slice — there's no request field to window them on.
        start_dt, end_dt = _datetime_slice_bounds(getattr(request, "datetime", None))
        for dim in [d for d in ds.dims if d not in (yname, xname)]:
            coord = ds.coords.get(dim)
            is_time = coord is not None and str(coord.dtype).startswith("datetime64")
            if is_time and (start_dt or end_dt):
                windowed = ds.sel({dim: slice(start_dt, end_dt)})
                if windowed.sizes.get(dim, 0) > 0:
                    ds = windowed
                    continue
                log.info(f"[{self.layer_id}] requested datetime range outside this Zarr "
                         f"cube's {dim!r} — keeping its most recent slice instead")
            ds = ds.isel({dim: -1}, drop=True) if ds.sizes[dim] > 1 else ds.squeeze(dim, drop=True)

        # decimate very large windows so display/explore stays responsive
        ny, nx = ds.sizes[yname], ds.sizes[xname]
        factor = max(ny // 2048, nx // 2048, 1)
        if factor > 1:
            log.info(f"[{self.layer_id}] window {ny}x{nx} → coarsening by {factor}")
            ds = ds.coarsen({yname: factor, xname: factor}, boundary="trim").mean()

        ds = ds.rio.set_spatial_dims(x_dim=xname, y_dim=yname).rio.write_crs(data_crs)
        ds.load()

        for var in ds.data_vars.values():
            fill = var.attrs.get("_FillValue", var.attrs.get("missing_value"))
            if fill is not None and var.rio.nodata is None:
                var.rio.write_nodata(fill, inplace=True)

        if pyproj.CRS(target_crs) != crs_obj:
            ds = ds.rio.reproject(target_crs)

        log.info(f"[{self.layer_id}] Zarr load OK — {dict(ds.sizes)}, vars={list(ds.data_vars)}")
        return ds

    def _asset_signer(self):
        """Return a per-asset URL signer for Planetary Computer, else ``None``.

        Planetary Computer asset hrefs point at Azure blobs that reject anonymous
        reads (HTTP 409); they must carry a short-lived SAS token. ``planetary_computer.sign``
        mints those tokens for free, without an account — so PC layers just work
        once the package is installed. Other endpoints are returned unsigned.
        """
        if "planetarycomputer.microsoft.com" not in (self.url or ""):
            return None
        try:
            import planetary_computer
        except ImportError as e:
            raise RuntimeError(
                f"[{self.layer_id}] Planetary Computer assets need (free, account-less) "
                f"URL signing — install it with: pip install planetary-computer"
            ) from e
        return planetary_computer.sign

    @retry_on_network_error(retries=3, delay=5.0, extra_exceptions=(_StacAPIError,))
    def _stac_search(self, request: GeoRequest) -> list:
        import pystac_client
        from shapely.geometry import mapping

        url = self.url.rstrip("/").removesuffix("/search")
        catalog = pystac_client.Client.open(url, headers=self.auth.headers)

        # Candidate pool fetched from the STAC API, independent from the final item
        # count the caller wants (request.max_features): sort_by (cloud_cover, date)
        # needs real candidates to choose among, or it's a no-op on a 1-item list.
        # The final list is truncated to request.max_features below, after sorting.
        base_params: dict[str, Any] = dict(
            collections=[self.product.collection],
            max_items=max(request.max_features or 0, 100),
        )

        if not self.search.static and request.datetime:
            base_params["datetime"] = _expand_datetime(request.datetime)
        elif not self.search.static:
            from datetime import date, timedelta
            today = date.today()
            base_params["datetime"] = f"{(today - timedelta(days=30)).isoformat()}/{today.isoformat()}"

        if request.aoi_gdf is not None:
            import shapely
            geom = request.aoi_gdf.to_crs("EPSG:4326").union_all()
            try:
                # Trims coincident vertices to shrink the GeoJSON sent to the API.
                # Needs GEOS >= 3.11; skip gracefully on older builds.
                geom = shapely.remove_repeated_points(geom)
            except Exception:
                pass
            search_params = {**base_params, "intersects": mapping(geom)}
        else:
            search_params = {**base_params, "bbox": list(request.bbox)}

        log.info(f"[{self.layer_id}] STAC search datetime={base_params.get('datetime', 'static')}")

        def _search(params: dict) -> list:
            try:
                return list(catalog.search(**params).items())
            except _StacAPIError:
                raise  # bubble up to the retry decorator
            except Exception as e:
                if sys.gettrace() is not None:
                    raise
                log.warning(f"[{self.layer_id}] STAC search failed ({e})")
                return []

        items = _search(search_params)
        # Annual/static products (ESA WorldCover, DEA crop_mask, WorldCereal…) have no
        # item inside a narrow date window → retry the full archive so they resolve
        # instead of failing with "no items". Truly temporal collections that have an
        # acquisition in-window are unaffected (the first search already returned them).
        if not items and "datetime" in search_params:
            log.info(f"[{self.layer_id}] no items in window {search_params['datetime']} "
                     f"→ retrying without datetime (annual/static product?)")
            items = _search({k: v for k, v in search_params.items() if k != "datetime"})
        log.info(f"[{self.layer_id}] STAC search → {len(items)} items")

        cloud_cover = request.cloud_cover if request.cloud_cover is not None else self.product.cloud_cover
        if cloud_cover is not None and items:
            items = [
                it for it in items
                if float(it.properties.get("eo:cloud_cover", 0)) <= cloud_cover
            ]
            log.info(f"[{self.layer_id}] After cloud_cover<={cloud_cover}% filter: {len(items)} items")

        if self.product.band_mapping:
            _apply_band_mapping(items, self.product.band_mapping)

        sort = self.search.sort_by or request.sort_by
        if sort == "cloud_cover":
            items.sort(key=lambda it: float(it.properties.get("eo:cloud_cover", float("inf"))))
        elif sort in ("date", "date_asc"):
            from datetime import datetime as _dt, timezone as _tz
            items.sort(key=lambda it: it.datetime or _dt.now(_tz.utc),
                       reverse=(sort == "date"))   # "date" = most recent first; "date_asc" = oldest first

        # Truncate to the caller's requested count now that filtering/sorting picked
        # the best candidates out of the wider search pool.
        if request.max_features:
            items = items[:request.max_features]

        return items

    def _validate_items(self, items: list, request: GeoRequest) -> list:
        from shapely.geometry import shape

        aoi_gdf = request.aoi_gdf or gpd.GeoDataFrame(
            geometry=[box(*request.bbox)], crs="EPSG:4326"
        )
        aoi_m = aoi_gdf.to_crs(3857).union_all()

        valid = []
        for item in items:
            item_geom = shape(item.geometry)
            item_m = gpd.GeoSeries([item_geom], crs="EPSG:4326").to_crs(3857).iloc[0]
            img_cov = min(100.0, item_m.intersection(aoi_m).area / aoi_m.area * 100) if aoi_m.area > 0 else 0.0

            if img_cov < self.product.image_coverage_threshold:
                log.debug(f"[{self.layer_id}] {item.id}: coverage {img_cov:.1f}% < threshold {self.product.image_coverage_threshold}%")
                continue

            if self.product.cloud_cover_aoi is not None:
                cloud_cov = _compute_scl_cloud_coverage(item, request.bbox)
                if cloud_cov > self.product.cloud_cover_aoi:
                    log.debug(f"[{self.layer_id}] {item.id}: SCL clouds {cloud_cov:.1f}% > threshold {self.product.cloud_cover_aoi}%")
                    continue
                log.info(f"[{self.layer_id}] ✓ {item.id} clouds={cloud_cov:.1f}% coverage={img_cov:.1f}%")
            else:
                log.info(f"[{self.layer_id}] ✓ {item.id} coverage={img_cov:.1f}%")

            valid.append(item)

        return valid


# ---------------------------------------------------------------------------
# Internal STAC helpers
# ---------------------------------------------------------------------------

def _compute_scl_cloud_coverage(item, bbox) -> float:
    """Compute the cloud percentage (SCL classes 8/9/10) inside the bbox via odc-stac."""
    try:
        from odc.stac import stac_load
        ds = stac_load([item], bbox=list(bbox), bands=["scl"],
                       chunks={"y": 4096, "x": 4096})
        da = ds.to_array(dim="band").squeeze(drop=True)
        cloud_mask = (da >= 8) & (da <= 10)
        valid_mask = da > 0
        n_cloud = int(cloud_mask.sum().compute())
        n_valid = int(valid_mask.sum().compute())
        return (n_cloud / n_valid * 100) if n_valid > 0 else 0.0
    except Exception as e:
        log.debug(f"SCL cloud coverage impossible ({e}), fallback metadata")
        return float(item.properties.get("eo:cloud_cover", 0))


def _apply_band_mapping(items: list, band_mapping: dict[str, str]):
    """Mutate STAC assets so odc-stac resolves bands through eo:common_name.

    odc-stac only reads the ``eo:bands`` aliases via pystac's ``EOExtension``,
    which refuses to operate unless the ``eo`` extension is declared in the
    item's ``stac_extensions`` (``ExtensionNotImplemented`` otherwise). Many
    collections carry no such declaration even when a ``bandMapping`` is
    configured, so the extension is registered here — without it the pivot
    names (`wtr`, ...) never resolve and ``load(bands=[pivot])`` fails with
    "No such band/alias".
    """
    for item in items:
        touched = False
        for asset_name, asset in item.assets.items():
            if asset_name in band_mapping:
                asset.extra_fields["eo:bands"] = [
                    {"name": asset_name, "common_name": band_mapping[asset_name]}
                ]
                touched = True
            else:
                asset.extra_fields.pop("eo:bands", None)
                asset.extra_fields.pop("bands", None)
        if touched:
            try:
                item.ext.add("eo")
            except Exception as e:  # noqa: BLE001 — older pystac / odd items
                log.debug(f"could not register the 'eo' extension on {item.id}: {e}")


def _all_nodata(ds) -> bool:
    """True if every data variable of an xarray Dataset is entirely nodata/NaN."""
    import numpy as np
    for var in ds.data_vars.values():
        a = np.asarray(var.values)
        finite = np.isfinite(a) if np.issubdtype(a.dtype, np.floating) else np.ones_like(a, bool)
        try:
            nd = var.rio.nodata
        except Exception:
            nd = var.attrs.get("nodata", var.attrs.get("_FillValue"))
        if nd is not None:
            finite &= a != nd
        if finite.any():
            return False
    return True


def _s3_auth_context(aws_config: dict | None = None, no_sign_request: bool = False):
    """Configure rasterio.Env for S3 access (Copernicus credentials, anonymous AWS, ...).

    Signed access (``aws_config``) requires boto3 — rasterio refuses raw AWS
    credentials as GDAL config options and delegates them to boto3 sessions.
    """
    @contextlib.contextmanager
    def _ctx():
        if not aws_config and not no_sign_request:
            yield
            return

        if aws_config and not no_sign_request:
            import importlib.util
            if importlib.util.find_spec("boto3") is None:
                raise ImportError(
                    "Signed S3 access (auth.type: AwsAuth) requires boto3. "
                    "Install it with: pip install 'geodatacatalog[s3]'"
                )

        import rasterio
        import rasterio.session

        aws_opts: dict = {}
        gdal_opts: dict = {
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "GDAL_HTTP_MAX_RETRY": "10",
            "GDAL_HTTP_RETRY_DELAY": "0.5",
        }
        if no_sign_request:
            aws_opts["aws_unsigned"] = True
            # Public buckets in a non-default region (e.g. DEA Africa in af-south-1)
            # still need AWS_REGION set, or GDAL hits the us-east-1 endpoint and fails
            # with "location constraint is incompatible for the region specific endpoint".
            region = (aws_config or {}).get("region_name")
            if region and region != "default":
                aws_opts["region_name"] = region
        elif aws_config:
            # A ${VAR} that wasn't set resolves to "" at config load time. An empty
            # key would be silently dropped here, letting botocore fall back to the
            # ambient AWS credential chain (env vars, ~/.aws) — which a custom S3
            # endpoint rejects with the misleading "Access Key Id ... does not exist".
            # Fail loudly instead, naming the missing variables.
            missing = [k for k in ("aws_access_key_id", "aws_secret_access_key")
                       if not aws_config.get(k)]
            if missing:
                raise ValueError(
                    "signed S3 access (auth.type: AwsAuth) is missing credentials: "
                    + ", ".join(missing)
                    + " — the referenced env var(s) are unset/empty (e.g. "
                    "CDSE_S3_ACCESS_KEY, CDSE_S3_SECRET_KEY). Export them (or load "
                    "your .env) and restart."
                )
            for k in ("aws_access_key_id", "aws_secret_access_key",
                      "aws_session_token", "region_name"):
                if aws_config.get(k):
                    aws_opts[k] = aws_config[k]
            if aws_config.get("s3_endpoint"):
                ep = aws_config["s3_endpoint"].rstrip("/")
                ep = ep.replace("https://", "").replace("http://", "")
                aws_opts["endpoint_url"] = ep
            gdal_opts["AWS_VIRTUAL_HOSTING"] = (
                "TRUE" if aws_config.get("virtual_hosting", False) else "FALSE"
            )
            if aws_config.get("gdal_unsafe_ssl"):
                gdal_opts["GDAL_HTTP_UNSAFESSL"] = "YES"

        # odc.stac.load reads pixels in worker threads that do NOT inherit this
        # thread's rasterio.Env boto3 session. Without the endpoint/credentials those
        # threads fall back to the default AWS resolution and hit the real AWS S3 with
        # the CDSE key → "The AWS Access Key Id you provided does not exist in our
        # records" (intermittently: small single-thread reads pass, large tiled COGs
        # fail). Mirror the config into os.environ so GDAL reaches the custom endpoint
        # from any thread; rasterio.Env stays for the calling thread.
        env_overrides: dict[str, str] = dict(gdal_opts)
        if no_sign_request:
            env_overrides["AWS_NO_SIGN_REQUEST"] = "YES"
            region = (aws_config or {}).get("region_name")
            if region and region != "default":
                env_overrides["AWS_REGION"] = region
                env_overrides["AWS_DEFAULT_REGION"] = region
        elif aws_config:
            env_overrides["AWS_ACCESS_KEY_ID"] = aws_config["aws_access_key_id"]
            env_overrides["AWS_SECRET_ACCESS_KEY"] = aws_config["aws_secret_access_key"]
            if aws_config.get("aws_session_token"):
                env_overrides["AWS_SESSION_TOKEN"] = aws_config["aws_session_token"]
            if aws_opts.get("endpoint_url"):
                env_overrides["AWS_S3_ENDPOINT"] = aws_opts["endpoint_url"]
            if aws_config.get("region_name"):
                env_overrides["AWS_REGION"] = aws_config["region_name"]
                env_overrides["AWS_DEFAULT_REGION"] = aws_config["region_name"]
            env_overrides["AWS_HTTPS"] = "YES" if aws_config.get("https", True) else "NO"

        session = rasterio.session.AWSSession(**aws_opts)
        _MISSING = object()
        saved = {k: os.environ.get(k, _MISSING) for k in env_overrides}
        os.environ.update(env_overrides)
        try:
            with rasterio.Env(session=session, **gdal_opts):
                yield
        finally:
            for k, v in saved.items():
                if v is _MISSING:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    return _ctx()
