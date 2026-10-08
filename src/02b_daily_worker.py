"""Daily-mosaic worker: processes a slice of composite dates (sys.argv: start, end).

Writes data/daily/YYYY-MM-DD.npy only. Monthly aggregation is a separate step.
"""
import json
import sys

import numpy as np
import requests

from config import DATA
from importlib.util import spec_from_file_location, module_from_spec

spec = spec_from_file_location("m02", str(DATA.parent / "src" / "02_monthly.py"))
m02 = module_from_spec(spec)
spec.loader.exec_module(m02)


def main():
    d0, d1 = sys.argv[1], sys.argv[2]
    daily_dir = DATA / "daily"
    daily_dir.mkdir(exist_ok=True)

    sel = m02.load_districts()
    bounds, width, height, transform = m02.build_master_grid(sel)
    master_origin = (bounds[0], bounds[3])
    session = requests.Session()
    signer = m02.Signer(session)

    composites = json.loads((DATA / "composites.json").read_text())
    chunk = [c for c in composites if d0 <= c["date"] <= d1]
    print(f"worker [{d0}..{d1}]: {len(chunk)} dates", flush=True)
    failed = []
    for entry in chunk:
        try:
            ndvi, cached = m02.process_date(entry, bounds, width, height, transform,
                                            master_origin, daily_dir, signer, session)
        except Exception as e:
            print(f"  {entry['date']}: FAILED ({type(e).__name__}: {str(e)[:100]})",
                  flush=True)
            failed.append(entry["date"])
            continue
        frac = float(np.isfinite(ndvi).mean())
        print(f"  {entry['date']}: valid={frac:.3f} {'(cached)' if cached else ''}",
              flush=True)
    print(f"worker done; failed dates: {failed}", flush=True)


if __name__ == "__main__":
    main()
