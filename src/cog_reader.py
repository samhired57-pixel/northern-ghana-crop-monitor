"""Windowed reads of Cloud-Optimized GeoTIFFs over HTTP Range requests.

Works around a sandbox where GDAL's TLS stack cannot verify the egress
proxy chain: uses `requests` (which verifies fine) to fetch only the byte
ranges needed, then parses the TIFF structure with tifffile and reads
pixel windows through a zarr view (chunk-aligned to TIFF tiles).
"""
import io
import math
import time

import requests
import tifffile
import numpy as np


class AuthError(RuntimeError):
    pass


def http_get(session, url, headers=None, timeout=120, tries=4):
    """GET with exponential-backoff retries on transient network errors."""
    last = None
    for attempt in range(tries):
        try:
            r = session.get(url, headers=headers, timeout=timeout)
            if r.status_code in (401, 403):
                raise AuthError(f"HTTP {r.status_code} for {url[:80]}...")
            r.raise_for_status()
            return r
        except AuthError:
            raise
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            wait = 5 * (2 ** attempt)
            print(f"    transient {type(e).__name__}, retry in {wait}s "
                  f"(attempt {attempt + 1}/{tries})", flush=True)
            time.sleep(wait)
    raise last


class RangeFile(io.RawIOBase):
    """Seekable read-only file backed by HTTP Range requests with a chunk cache."""

    def __init__(self, url, session=None, chunk_size=1 << 20):
        self.url = url
        self.session = session or requests.Session()
        self.chunk_size = chunk_size
        r = http_get(self.session, url, headers={"Range": "bytes=0-0"})
        cr = r.headers.get("Content-Range", "")
        try:
            self.size = int(cr.split("/")[-1])
        except (ValueError, IndexError):
            raise RuntimeError(f"server did not return Content-Range for {url[:80]}")
        self._cache = {}
        self._pos = 0

    def seekable(self):
        return True

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self.size + offset
        else:
            raise ValueError(whence)
        return self._pos

    def tell(self):
        return self._pos

    def _get_chunk(self, idx):
        if idx not in self._cache:
            start = idx * self.chunk_size
            end = min(self.size, start + self.chunk_size) - 1
            r = http_get(self.session, self.url,
                         headers={"Range": f"bytes={start}-{end}"})
            self._cache[idx] = r.content
            # keep cache bounded
            if len(self._cache) > 64:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[idx]

    def read(self, n=-1):
        if self._pos >= self.size:
            return b""
        if n is None or n < 0:
            n = self.size - self._pos
        n = min(n, self.size - self._pos)
        out = bytearray()
        while n > 0:
            idx = self._pos // self.chunk_size
            off = self._pos % self.chunk_size
            chunk = self._get_chunk(idx)
            take = min(n, len(chunk) - off)
            out += chunk[off:off + take]
            self._pos += take
            n -= take
        return bytes(out)

    def readinto(self, b):
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)


def _geotiff_origin(page):
    """Top-left origin (ox, oy) and pixel size from GeoTIFF tags."""
    tags = page.tags
    scale = tags["ModelPixelScaleTag"].value  # (sx, sy, sz)
    tie = tags["ModelTiepointTag"].value      # (i, j, k, x, y, z)
    sx, sy = float(scale[0]), float(scale[1])
    i, j, x, y = float(tie[0]), float(tie[1]), float(tie[3]), float(tie[4])
    ox = x - i * sx
    oy = y + j * sy  # tie y is top edge
    return ox, oy, sx, sy


def read_cog_window(url, session, bounds, pixel_size=250.0):
    """Read the raster window intersecting `bounds` (minx, miny, maxx, maxy,
    same CRS as the COG). Returns (array float32, (xoff, yoff) in COG pixels)
    or (None, None) if no overlap."""
    minx, miny, maxx, maxy = bounds
    f = RangeFile(url, session)
    with tifffile.TiffFile(f) as tif:
        page = tif.pages[0]
        ox, oy, sx, sy = _geotiff_origin(page)
        assert abs(sx - pixel_size) < 0.01 and abs(sy - pixel_size) < 0.01, \
            f"unexpected pixel size {(sx, sy)}"
        W, H = page.imagewidth, page.imagelength
        xoff = int(math.floor((minx - ox) / sx))
        yoff = int(math.floor((oy - maxy) / sy))
        x1 = int(math.ceil((maxx - ox) / sx))
        y1 = int(math.ceil((oy - miny) / sy))
        xoff_c, yoff_c = max(0, xoff), max(0, yoff)
        x1_c, y1_c = min(W, x1), min(H, y1)
        if x1_c <= xoff_c or y1_c <= yoff_c:
            return None, None, None
        store = tif.aszarr()
        import zarr
        z = zarr.open_group(store, mode="r")["0"]  # full-res level
        arr = z[yoff_c:y1_c, xoff_c:x1_c]
        arr = np.asarray(arr)
    geo = (ox, oy, sx, (xoff_c, yoff_c))
    return arr, (xoff_c, yoff_c), geo
