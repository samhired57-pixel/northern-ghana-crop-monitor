"""Step 4: Publication-quality figures.

- outputs/ndvi_seasonal_profiles.png : faceted seasonal profiles per district
  (2023/2024/2025 lines, 3-year climatology band, 2024 anomalies marked,
  Jul-Oct 2025 upstream data gap shaded)
- outputs/ndvi_growing_season_map.png : mean Jun-Sep NDVI 2023-2024 map
  (last complete growing seasons)
- outputs/ndvi_growing_season_map.tif : same map as GeoTIFF
"""
import json

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import rasterio
from rasterio.plot import show

from config import DATA, OUTPUTS, DISTRICTS, MODIS_SRS

MONTH_ABBR = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linestyle": "--",
})


def seasonal_profiles():
    df = pd.read_csv(OUTPUTS / "ndvi_timeseries.csv")
    clim = pd.read_csv(OUTPUTS / "climatology.csv")
    anom = pd.read_csv(OUTPUTS / "anomalies_2024.csv")

    fig, axes = plt.subplots(2, 4, figsize=(16, 8), sharex=True, sharey=True)
    colors = {2023: "#9db8a4", 2024: "#2e7d4f", 2025: "#1f5c3d"}
    for ax, district in zip(axes.flat, DISTRICTS):
        g = df[df["district"] == district]
        c = clim[clim["district"] == district].set_index("month")
        for year in (2023, 2024, 2025):
            gy = g[g["year"] == year].set_index("month").reindex(range(1, 13))
            ax.plot(range(1, 13), gy["ndvi_mean"], color=colors[year],
                    lw=2.4 if year == 2024 else 1.4,
                    label=str(year) if district == DISTRICTS[0] else None,
                    zorder=3 if year == 2024 else 2)
        ax.fill_between(range(1, 13), c["clim_mean"] - c["clim_std"],
                        c["clim_mean"] + c["clim_std"],
                        color="#1f5c3d", alpha=0.12, label="climatology ±1σ"
                        if district == DISTRICTS[0] else None, zorder=1)
        a = anom[(anom["district"] == district) & (anom["anomaly_flag"])]
        if len(a):
            gy24 = g[g["year"] == 2024].set_index("month")
            ax.scatter(a["month"], [gy24.loc[m, "ndvi_mean"] for m in a["month"]],
                       color="#c0392b", s=42, zorder=4, edgecolor="white",
                       linewidth=0.8,
                       label="2024 anomaly (|z|>1)" if district == DISTRICTS[0] else None)
        # the 2025 line simply stops at June: no composites exist upstream
        # for Jul-Oct 2025 (noted in the caption)
        ax.set_title(district, fontsize=11, fontweight="bold")
        ax.set_xticks(range(1, 13))
        ax.set_xticklabels(MONTH_ABBR, fontsize=8)
        ax.set_ylim(0.15, 0.95)
        ax.tick_params(labelsize=8)
    for ax in axes[:, 0]:
        ax.set_ylabel("NDVI (monthly median)", fontsize=10)
    fig.suptitle("Growing-season NDVI profiles — northern Ghana districts\n"
                 "MODIS MOD13Q1/MYD13Q1 v6.1 (250 m), monthly medians, 2023–2025",
                 fontsize=13, fontweight="bold", y=0.99)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, fontsize=10,
               bbox_to_anchor=(0.5, -0.01), frameon=False)
    fig.text(0.5, -0.045,
             "Shaded band: 3-year monthly climatology (2023–2025) ± one std. "
             "Red dots: 2024 months deviating >1σ from climatology. "
             "The 2025 line ends in June — no MODIS composites exist upstream "
             "for Jul–Oct 2025 (Planetary Computer ingest gap, verified "
             "collection-wide).",
             ha="center", fontsize=9, style="italic", color="#555")
    fig.tight_layout(rect=[0, 0.04, 1, 0.94])
    out = OUTPUTS / "ndvi_seasonal_profiles.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print("wrote", out)


def growing_season_map():
    # last complete growing seasons only (2025 Jul-Oct missing upstream)
    files = sorted((DATA / "monthly").glob("*.tif"))
    sel_files = [f for f in files
                 if int(f.stem[:4]) in (2023, 2024) and int(f.stem[5:7]) in (6, 7, 8, 9)]
    print(f"map: averaging {len(sel_files)} Jun-Sep monthly grids (2023-2024)")
    stack = []
    for f in sel_files:
        with rasterio.open(f) as src:
            stack.append(src.read(1).astype(np.float32))
            profile = src.profile
    # silence the all-NaN-pixel warning (persistent cloud / off-district cells)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(np.stack(stack), axis=0)
    print(f"map stats: mean {np.nanmean(mean):.3f}, p5 {np.nanpercentile(mean,5):.3f}, "
          f"p95 {np.nanpercentile(mean,95):.3f}")

    tpath = OUTPUTS / "ndvi_growing_season_map.tif"
    profile.update(dtype="float32", compress="DEFLATE", nodata=np.nan)
    with rasterio.open(tpath, "w", **profile) as dst:
        dst.write(mean, 1)
        dst.update_tags(description="Mean Jun-Sep NDVI, 2023-2024, MODIS 13Q1 v6.1 "
                                    "(last complete growing seasons)")
    print("wrote", tpath)

    districts = gpd.read_file(DATA / "gadm" / "gadm41_GHA_2.shp")
    districts = districts[districts["NAME_2"].isin(DISTRICTS)].to_crs(MODIS_SRS)

    fig, ax = plt.subplots(figsize=(9, 9))
    show(mean, transform=profile["transform"], ax=ax, cmap="YlGn",
         vmin=0.25, vmax=0.85)
    districts.boundary.plot(ax=ax, color="black", linewidth=0.7)
    for _, r in districts.iterrows():
        c = r.geometry.representative_point()
        ax.text(c.x, c.y, r["NAME_2"], fontsize=8, ha="center", va="center",
                color="black", weight="bold",
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.7))
    sm = plt.cm.ScalarMappable(cmap="YlGn", norm=plt.Normalize(0.25, 0.85))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.75, pad=0.02)
    cbar.set_label("Mean NDVI (Jun–Sep, 2023–2024)", fontsize=10)
    ax.set_title("Mean growing-season NDVI — northern Ghana\n"
                 "MODIS MOD13Q1/MYD13Q1 v6.1, 250 m (Jun–Sep, 2023–2024)",
                 fontsize=12, fontweight="bold")
    ax.set_xlabel("Sinusoidal easting (m)")
    ax.set_ylabel("Sinusoidal northing (m)")
    ax.ticklabel_format(style="plain")
    out = OUTPUTS / "ndvi_growing_season_map.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print("wrote", out)


if __name__ == "__main__":
    seasonal_profiles()
    growing_season_map()
