"""Step 0: Fetch GADM v4.1 Ghana boundaries (levels 0-2)."""
import urllib.request
import zipfile

from config import DATA

URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/shp/gadm41_GHA_shp.zip"


def main():
    outdir = DATA / "gadm"
    outdir.mkdir(parents=True, exist_ok=True)
    zpath = outdir / "gadm41_GHA_shp.zip"
    if not zpath.exists():
        print(f"downloading {URL} ...")
        urllib.request.urlretrieve(URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(outdir)
    print("GADM ready in", outdir)


if __name__ == "__main__":
    main()
