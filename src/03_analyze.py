"""Step 3: Zonal statistics, climatology, anomalies, and growing-season metrics.

NOTE on a real upstream data gap: the Planetary Computer modis-13Q1-061
collection contains no composites starting 2025-07 through 2025-10
(verified collection-wide at access time). 2024 is therefore treated as
the last complete growing season; 2025 metrics are partial (onset only).

Inputs:  data/monthly/YYYY-MM.tif, data/district_mask.tif, data/districts.json
Outputs: outputs/ndvi_timeseries.csv
         outputs/climatology.csv
         outputs/anomalies_2024.csv
         outputs/metrics.json
"""
import json
from datetime import date

import numpy as np
import pandas as pd
import rasterio

from config import DATA, OUTPUTS, DISTRICTS, SEASON_MONTHS

OUTPUTS.mkdir(exist_ok=True)


def zonal_means():
    labels = json.loads((DATA / "districts.json").read_text())
    with rasterio.open(DATA / "district_mask.tif") as src:
        mask = src.read(1)
    rows = []
    for f in sorted((DATA / "monthly").glob("*.tif")):
        ym = f.stem
        year, month = int(ym[:4]), int(ym[5:7])
        with rasterio.open(f) as src:
            arr = src.read(1).astype(np.float64)
        for lab, name in labels.items():
            sel = arr[mask == int(lab)]
            sel = sel[np.isfinite(sel)]
            rows.append({
                "district": name, "year": year, "month": month,
                "ndvi_mean": float(sel.mean()) if sel.size else float("nan"),
                "ndvi_std": float(sel.std()) if sel.size else float("nan"),
                "n_pixels": int(sel.size),
                "valid_frac": float(sel.size / (mask == int(lab)).sum()),
            })
    df = pd.DataFrame(rows).sort_values(["district", "year", "month"])
    df.to_csv(OUTPUTS / "ndvi_timeseries.csv", index=False)
    print(f"timeseries: {len(df)} rows -> ndvi_timeseries.csv")
    return df


def climatology(df):
    clim = (df.groupby(["district", "month"])["ndvi_mean"]
              .agg(["mean", "std", "count"]).reset_index()
              .rename(columns={"mean": "clim_mean", "std": "clim_std",
                               "count": "n_years"}))
    clim.to_csv(OUTPUTS / "climatology.csv", index=False)
    return clim


def anomalies(df, clim, year=2024):
    """Z-score anomalies for the last complete growing-season year."""
    dy = df[df["year"] == year].merge(clim, on=["district", "month"], how="left")
    dy["z"] = (dy["ndvi_mean"] - dy["clim_mean"]) / dy["clim_std"].replace(0, np.nan)
    dy["anomaly_flag"] = dy["z"].abs() > 1.0
    dy[["district", "month", "ndvi_mean", "clim_mean", "clim_std",
        "z", "anomaly_flag"]].to_csv(OUTPUTS / f"anomalies_{year}.csv", index=False)
    return dy


def season_metrics(df):
    """Per district-year growing-season metrics from the monthly series.

    Metrics are computed, not eyeballed. Partial years (e.g. 2025, whose
    Jul-Oct composites are missing upstream) yield onset but null peak/decay.
    """
    out = {}
    for (district, year), g in df.groupby(["district", "year"]):
        s = g.set_index("month")["ndvi_mean"].reindex(range(1, 13))
        n_valid = int(s.notna().sum())
        if n_valid < 6:
            continue
        vals = s.values.astype(float)
        months = s.index.values
        finite = np.isfinite(vals)
        vmin = float(np.nanmin(vals))
        vmax = float(np.nanmax(vals))
        amp = vmax - vmin
        thr = vmin + 0.25 * amp
        fin = lambda v: np.isfinite(v)
        # onset: first Mar-Jul month above threshold on a rising limb
        onset = None
        for i, (m, v) in enumerate(zip(months, vals)):
            vp = vals[i - 1] if i > 0 else np.nan
            if 3 <= m <= 7 and fin(v) and v > thr and (not fin(vp) or v >= vp):
                onset = int(m)
                break
        # peak/decay only when the core season (Jul-Sep) was actually observed
        core_observed = all(fin(v) for m, v in zip(months, vals) if m in (7, 8, 9))
        peak_m = peak_v = decay = None
        if core_observed:
            cand = [(m, v) for m, v in zip(months, vals)
                    if 5 <= m <= 11 and fin(v)]
            if cand:
                peak_m, peak_v = max(cand, key=lambda t: t[1])
            for m, v in zip(months[::-1], vals[::-1]):
                if 7 <= m <= 12 and fin(v) and v > thr:
                    decay = int(m)
                    break
        season_sum = float(np.nansum([v for m, v in zip(months, vals)
                                      if m in SEASON_MONTHS]))
        integral = None
        if onset and decay:
            integral = round(float(np.nansum(
                [max(0.0, v - vmin) for m, v in zip(months, vals)
                 if onset <= m <= decay])), 4)
        out.setdefault(district, {})[str(year)] = {
            "n_months_valid": n_valid,
            "season_complete": bool(core_observed),
            "onset_month": onset,
            "peak_month": int(peak_m) if peak_m else None,
            "peak_ndvi": round(float(peak_v), 4) if peak_v else None,
            "decay_month": decay,
            "season_length_months": (decay - onset + 1) if onset and decay else None,
            "may_oct_ndvi_sum": round(season_sum, 4),
            "season_integral": integral,
            "annual_min": round(float(vmin), 4),
            "annual_max": round(float(vmax), 4),
        }
    return out


def main():
    df = zonal_means()
    print("months present:", sorted(df["month"].unique()))
    print("years present:", sorted(df["year"].unique()))
    print("districts:", df["district"].nunique())
    clim = climatology(df)
    anom = anomalies(df, clim, year=2024)
    n_flag = int(anom["anomaly_flag"].sum())
    print(f"2024 anomalies |z|>1: {n_flag} district-months")
    metrics = {
        "provenance": {
            "source": "MODIS MOD13Q1/MYD13Q1 Collection 6.1 (250 m, 16-day VI), "
                      "Microsoft Planetary Computer STAC",
            "stac_collection": "modis-13Q1-061",
            "period": "2023-01 through 2025-12",
            "data_gap": "No composites with start dates 2025-07 through 2025-10 "
                        "exist in the collection (verified collection-wide at "
                        "access time); 2024 is the last complete growing season, "
                        "2025 metrics are partial (onset only).",
            "anomaly_reference_year": 2024,
            "access_date": date.today().isoformat(),
            "districts": DISTRICTS,
            "n_districts": len(DISTRICTS),
            "masking": "pixel_reliability in {0,1} (good/marginal); scale 0.0001",
            "compositing": "monthly median of 16-day composites",
            "climatology_note": "3-year baseline (2023-2025); short but documented",
        },
        "season_metrics": season_metrics(df),
    }
    (OUTPUTS / "metrics.json").write_text(json.dumps(metrics, indent=1))
    print("metrics.json written")


if __name__ == "__main__":
    main()
