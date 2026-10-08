"""Project configuration: northern Ghana NDVI crop-season monitor."""
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
OUTPUTS = BASE / "outputs"
SRC = BASE / "src"

# Area of interest: districts to monitor (GADM level-2 names, Ghana)
DISTRICTS = [
    "Tamale",        # Northern — metro, mixed land use
    "Savelugu",      # Northern — major food-crop basket
    "Tolon",         # Northern — Guinea savanna farming
    "Yendi",         # Northern — eastern corridor
    "West Mamprusi", # North East — yam/maize belt
    "Bawku",         # Upper East — Sudan savanna, millet/sorghum
    "Wa Municipal",  # Upper West — Guinea savanna
    "Bole",          # Savannah — transitional zone
]

# MODIS sinusoidal projection (spherical)
MODIS_SRS = "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 +R=6371007.181 +units=m +no_defs"
# MODIS sinusoidal grid: 172800 columns around the equator -> 40075.016686 km / 172800
# (the "250 m" nominal resolution is 231.656 m in the sinusoidal projection)
PIXEL_M = 231.65635826395834

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "modis-13Q1-061"
DATE_START = "2023-01-01"
DATE_END = "2025-12-31"

NDVI_SCALE = 0.0001
NDVI_FILL = -3000
# MODIS pixel_reliability: 0 good, 1 marginal, 2 snow/ice, 3 cloudy
RELIABILITY_OK = (0, 1)

# Growing-season window for northern Ghana (unimodal rains ~May-Oct)
SEASON_MONTHS = list(range(5, 11))
