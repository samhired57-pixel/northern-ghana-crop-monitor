"""Step 2: Build masked NDVI mosaics per composite date, then monthly medians.

Data path: Planetary Computer STAC -> SAS-signed COG URLs -> windowed reads
via HTTP Range requests (see cog_reader.py; works around sandbox TLS limits
that break GDAL's HTTPS stack).

- Mosaics the 4 intersecting MODIS tiles onto one sinusoidal master grid
  (true 250 m-product pixel size: 231.656 m).
- Masks pixels with pixel_reliability > 1 (snow/ice, cloud); applies 0.0001 scale.
- Resumable: skips dates/months already on disk.

Outputs:
  data/daily/YYYY-MM-DD.npy        masked NDVI mosaic (float32, NaN = no data)
  data/monthly/YYYY-MM.tif         monthly median NDVI (GeoTIFF, sinusoidal)
  data/district_mask.tif           rasterized district labels (uint8)
  data/districts.json              label -> district name mapping
"""
import json
from collections import defaultdict

import numpy as np
import rasterio
import requests
from rasterio.features import rasterize
import geopandas as gpd

from config import (DATA, DISTRICTS, MODIS_SRS, PIXEL_M, NDVI_SCALE, NDVI_FILL,
                    RELIABILITY_OK)
from cog_reader import read_cog_window, AuthError

SAS_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/modiseuwest/modis-061-cogs"


def get_token(session):
    r = session.get(SAS_URL, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


class Signer:
    """Appends a SAS token to asset URLs; refreshes it on auth failures."""

    def __init__(self, session):
        self.session = session
        self.token = get_token(session)

    def sign(self, url):
        return url + "?" + self.token

    def refresh(self):
        self.token = get_token(self.session)


def load_districts():
    gdf = gpd.read_file(DATA / "gadm" / "gadm41_GHA_2.shp")
    sel = gdf[gdf["NAME_2"].isin(DISTRICTS)].copy()
    missing = set(DISTRICTS) - set(sel["NAME_2"])
    assert not missing, f"districts not found in GADM: {missing}"
    return sel.to_crs(MODIS_SRS)


def build_master_grid(sel):
    minx, miny, maxx, maxy = sel.total_bounds
    minx = np.floor(minx / PIXEL_M) * PIXEL_M
    miny = np.floor(miny / PIXEL_M) * PIXEL_M
    maxx = np.ceil(maxx / PIXEL_M) * PIXEL_M
    maxy = np.ceil(maxy / PIXEL_M) * PIXEL_M
    width = int(round((maxx - minx) / PIXEL_M))
    height = int(round((maxy - miny) / PIXEL_M))
    transform = rasterio.transform.from_origin(minx, maxy, PIXEL_M, PIXEL_M)
    return (minx, miny, maxx, maxy), width, height, transform


def place_into_master(master, arr, geo, master_origin):
    """Paste a tile window into the master grid, clipping to its extent.
    Returns the master slice and the corresponding array slice."""
    ox, oy, sx, (xoff_c, yoff_c) = geo
    mx0, my_top = master_origin
    col0 = int(round((ox + xoff_c * sx - mx0) / PIXEL_M))
    row0 = int(round((my_top - (oy - yoff_c * sx)) / PIXEL_M))
    h, w = arr.shape
    r1, c1 = row0 + h, col0 + w
    # clip to master extent (1-px rounding guard from grid snapping)
    mr0, mr1 = max(0, row0), min(master.shape[0], r1)
    mc0, mc1 = max(0, col0), min(master.shape[1], c1)
    ar0, ar1 = mr0 - row0, mr1 - row0
    ac0, ac1 = mc0 - col0, mc1 - col0
    return (slice(mr0, mr1), slice(mc0, mc1)), (slice(ar0, ar1), slice(ac0, ac1))


def process_date(entry, bounds, width, height, transform, master_origin,
                 daily_dir, signer, session):
    out_path = daily_dir / f"{entry['date']}.npy"
    if out_path.exists():
        return np.load(out_path), True

    def _run():
        ndvi = np.full((height, width), np.nan, dtype=np.float32)
        for tile, urls in entry["tiles"].items():
            rel_raw, _, rel_geo = read_cog_window(
                signer.sign(urls["reliability"]), session, bounds, PIXEL_M)
            if rel_raw is None:
                continue
            good = np.isin(rel_raw, RELIABILITY_OK)
            if not good.any():
                continue
            ndvi_raw, _, ndvi_geo = read_cog_window(
                signer.sign(urls["ndvi"]), session, bounds, PIXEL_M)
            assert ndvi_geo[:3] == rel_geo[:3] and ndvi_geo[3] == rel_geo[3], \
                "NDVI/reliability window mismatch"
            arr = np.where(ndvi_raw == NDVI_FILL, np.nan,
                           ndvi_raw).astype(np.float32) * NDVI_SCALE
            arr[~good] = np.nan
            sl, asl = place_into_master(ndvi, arr, ndvi_geo, master_origin)
            target = ndvi[sl]
            src = arr[asl]
            fill = np.isnan(target) & ~np.isnan(src)
            target[fill] = src[fill]
        return ndvi

    try:
        ndvi = _run()
    except AuthError:
        signer.refresh()
        ndvi = _run()
    np.save(out_path, ndvi)
    return ndvi, False


def main():
    daily_dir = DATA / "daily"
    monthly_dir = DATA / "monthly"
    daily_dir.mkdir(exist_ok=True)
    monthly_dir.mkdir(exist_ok=True)

    sel = load_districts()
    bounds, width, height, transform = build_master_grid(sel)
    master_origin = (bounds[0], bounds[3])
    print(f"master grid: {width}x{height} px @ {PIXEL_M:.3f} m")

    shapes = [(geom, i + 1) for i, geom in
              enumerate(sel.set_index("NAME_2").loc[DISTRICTS]["geometry"])]
    mask = rasterize(shapes, out_shape=(height, width), transform=transform,
                     fill=0, dtype="uint8")
    profile = dict(driver="GTiff", height=height, width=width, count=1,
                   dtype="uint8", crs=MODIS_SRS, transform=transform,
                   compress="DEFLATE")
    with rasterio.open(DATA / "district_mask.tif", "w", **profile) as dst:
        dst.write(mask, 1)
    (DATA / "districts.json").write_text(json.dumps(
        {str(i + 1): d for i, d in enumerate(DISTRICTS)}, indent=1))
    counts = dict(zip(*np.unique(mask, return_counts=True)))
    print("district mask written; pixel counts:", {int(k): int(v) for k, v in counts.items()})

    composites = json.loads((DATA / "composites.json").read_text())
    by_month = defaultdict(list)
    for c in composites:
        by_month[c["date"][:7]].append(c)

    session = requests.Session()
    signer = Signer(session)

    for ym in sorted(by_month):
        mpath = monthly_dir / f"{ym}.tif"
        if mpath.exists():
            print(f"{ym}: monthly exists, skip")
            continue
        stack = []
        for entry in sorted(by_month[ym], key=lambda e: e["date"]):
            ndvi, cached = process_date(entry, bounds, width, height, transform,
                                        master_origin, daily_dir, signer, session)
            frac = float(np.isfinite(ndvi).mean())
            print(f"  {entry['date']}: valid={frac:.3f} {'(cached)' if cached else ''}",
                  flush=True)
            if frac > 0.001:
                stack.append(ndvi)
        if not stack:
            print(f"{ym}: NO valid data, skipping")
            continue
        med = np.nanmedian(np.stack(stack), axis=0).astype(np.float32)
        mprofile = dict(driver="GTiff", height=height, width=width, count=1,
                        dtype="float32", crs=MODIS_SRS, transform=transform,
                        compress="DEFLATE")
        with rasterio.open(mpath, "w", **mprofile) as dst:
            dst.write(med, 1)
            dst.update_tags(month=ym, source="MODIS MOD13Q1/MYD13Q1 v061 via Planetary Computer STAC")
            dst.nodata = float("nan")
        print(f"{ym}: monthly median from {len(stack)} composites -> {mpath.name}", flush=True)


if __name__ == "__main__":
    main()
