"""Step 1: Query Planetary Computer STAC for MODIS 13Q1 over the study area.

Saves data/composites.json: one entry per composite date with the NDVI and
pixel-reliability COG URLs for each intersecting tile.
"""
import json
import re
from collections import defaultdict

import pystac_client

from config import STAC_URL, COLLECTION, DATE_START, DATE_END, DATA

SEARCH_BBOX = [-2.5, 8.5, 0.5, 11.0]  # generous box around northern Ghana


def main():
    cat = pystac_client.Client.open(STAC_URL)
    search = cat.search(
        collections=[COLLECTION],
        bbox=SEARCH_BBOX,
        datetime=f"{DATE_START}/{DATE_END}",
    )
    items = list(search.items())
    print(f"STAC returned {len(items)} items")

    by_date = defaultdict(dict)
    for it in items:
        start = it.properties.get("start_datetime")
        if not start or start < DATE_START + "T00:00:00Z":
            continue
        m = re.search(r"\.(h\d+v\d+)\.", it.id)
        tile = m.group(1)
        date = start[:10]
        by_date[date][tile] = {
            "ndvi": it.assets["250m_16_days_NDVI"].href,
            "reliability": it.assets["250m_16_days_pixel_reliability"].href,
            "item_id": it.id,
        }

    composites = [
        {"date": d, "tiles": by_date[d]} for d in sorted(by_date)
    ]
    out = DATA / "composites.json"
    out.write_text(json.dumps(composites, indent=1))
    n_tiles = {len(c["tiles"]) for c in composites}
    print(f"Saved {len(composites)} composite dates -> {out}")
    print(f"Tiles per date (set): {sorted(n_tiles)}")
    print(f"Date range: {composites[0]['date']} .. {composites[-1]['date']}")


if __name__ == "__main__":
    main()
