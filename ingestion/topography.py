"""Per-parcel topography from the county's elevation model.

Slope is the difference between "half an acre" and "half an acre you can build
on". A 12,000 sqft lot that falls 20 feet across its width needs retaining walls
or a walk-out foundation, and loses real buildable area. Until this module the
only topography signal was Cincinnati's hillside-overlay flag, which is zero
everywhere outside the city — so Blue Ash, Montgomery, Symmes and every fringe
parcel had no topography read at all.

Method
------
1. Pull the CAGIS elevation raster (`CAGIS_Elevation_Current`, a 2-ft float
   elevation ImageServer) for the target markets, in Ohio State Plane South
   (EPSG:3735) so the pixel grid is already in FEET — slope is then a plain
   rise/run with no projection math.
2. Slope percent per pixel from the elevation gradient.
3. For each parcel, rasterize its polygon into a mask (PIL — exact, and avoids a
   shapely/rasterio dependency the project doesn't carry) and take the slope
   statistics of the pixels actually inside the lot.

Resolution is deliberately coarser than the source 2 ft: at ~10 ft/px a typical
suburban lot is still 50-400 samples, which is plenty for "how steep is this
lot", and it keeps the whole 5-market mosaic to a few hundred MB.
"""
from __future__ import annotations

import io
import math

import numpy as np

_ELEV = ("https://cagisonline.hamilton-co.org/arcgis/rest/services"
         "/CAGIS_Elevation_Current/ImageServer/exportImage")
_SR = 3735               # Ohio State Plane South (feet)
_PX_FT = 10.0            # ground resolution of our sample grid, in feet
# The service advertises maxImageHeight/Width of 4100 x 15000, but a 4000x4000
# F32 request (64 MB) comes back as an HTML error page — the real limit is on
# response SIZE. 2000x2000 (~16 MB) is reliably served.
_MAX_PX = 2000
_BUILDABLE_GRADE = 15.0  # percent grade at/below which ground is normal to build on
_TARGETS = ("Montgomery", "Blue Ash", "Symmes Township", "Hyde Park", "Oakley")


def _fetch_tile(xmin: float, ymin: float, xmax: float, ymax: float,
                w: int, h: int, attempts: int = 4) -> np.ndarray:
    """One elevation tile as a float array (feet). NoData/0 becomes NaN.

    These are ~16 MB responses and the server occasionally cuts one short, so
    retry rather than losing the whole mosaic to a single truncated transfer."""
    import time

    import requests
    from PIL import Image

    last = None
    for attempt in range(attempts):
        try:
            r = requests.get(_ELEV, params={
                "bbox": f"{xmin},{ymin},{xmax},{ymax}", "bboxSR": _SR, "imageSR": _SR,
                "size": f"{w},{h}", "format": "tiff", "pixelType": "F32",
                "interpolation": "RSP_BilinearInterpolation", "f": "image",
            }, timeout=300)
            r.raise_for_status()
            # oversized/invalid requests come back HTTP 200 with an HTML error
            # page, so check the type rather than letting PIL fail obscurely
            if "image" not in r.headers.get("content-type", ""):
                raise RuntimeError(f"server returned {r.headers.get('content-type')}")
            arr = np.array(Image.open(io.BytesIO(r.content))).astype("float32")
            if arr.shape[:2] != (h, w):
                raise RuntimeError(f"expected {h}x{w}, got {arr.shape[:2]}")
            arr[arr <= 0] = np.nan      # the service returns 0 outside coverage
            return arr
        except Exception as exc:       # truncated transfer, timeout, 5xx...
            last = exc
            print(f"    tile retry {attempt + 1}/{attempts}: {exc}", flush=True)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"elevation tile {w}x{h} at {xmin},{ymin} failed: {last}")


def _mosaic(bounds: tuple[float, float, float, float]) -> np.ndarray:
    """Elevation grid covering `bounds` at _PX_FT, assembled from server tiles."""
    xmin, ymin, xmax, ymax = bounds
    ncols = int(math.ceil((xmax - xmin) / _PX_FT))
    nrows = int(math.ceil((ymax - ymin) / _PX_FT))
    out = np.full((nrows, ncols), np.nan, dtype="float32")
    # Balanced tiling: stepping by _MAX_PX leaves a sliver remainder (a 41-px-wide
    # request comes back as a malformed TIFF), so split into equal-ish tiles.
    n_c = max(1, math.ceil(ncols / _MAX_PX))
    n_r = max(1, math.ceil(nrows / _MAX_PX))
    step_c = math.ceil(ncols / n_c)
    step_r = math.ceil(nrows / n_r)
    for r0 in range(0, nrows, step_r):
        for c0 in range(0, ncols, step_c):
            h = min(step_r, nrows - r0)
            w = min(step_c, ncols - c0)
            # rows run north->south in the image, so flip y when building the bbox
            tx0 = xmin + c0 * _PX_FT
            ty1 = ymax - r0 * _PX_FT
            try:
                tile = _fetch_tile(tx0, ty1 - h * _PX_FT, tx0 + w * _PX_FT, ty1, w, h)
            except RuntimeError as exc:
                # one unrecoverable tile shouldn't discard the whole mosaic; that
                # patch just yields no slope, and those parcels stay NULL
                print(f"  !! skipping tile r{r0} c{c0}: {exc}", flush=True)
                continue
            out[r0:r0 + h, c0:c0 + w] = tile[:h, :w]
            print(f"  elevation tile r{r0} c{c0} ({w}x{h})", flush=True)
    return out


def _slope_pct(elev: np.ndarray) -> np.ndarray:
    """Percent grade per pixel from the elevation gradient (rise/run x 100)."""
    filled = np.where(np.isnan(elev), np.nanmean(elev), elev)
    dy, dx = np.gradient(filled, _PX_FT)
    slope = np.hypot(dx, dy) * 100.0
    slope[np.isnan(elev)] = np.nan
    # At the edge of elevation coverage the gradient can blow up to inf, which
    # then poisons a parcel's mean while its percentile stays sane. Anything past
    # a cliff is noise, so drop non-finite and absurd grades rather than keep them.
    slope[~np.isfinite(slope)] = np.nan
    slope[slope > 300.0] = np.nan
    return slope.astype("float32")


def _parcel_stats(slope: np.ndarray, bounds, rings, area_sqft):
    """Slope statistics for one parcel: rasterize its rings into a mask and read
    the pixels inside. Returns (avg, max, buildable_pct, buildable_sqft)."""
    from PIL import Image, ImageDraw

    xmin, _, _, ymax = bounds
    nrows, ncols = slope.shape
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    c0 = max(0, int((min(xs) - xmin) / _PX_FT) - 1)
    c1 = min(ncols, int((max(xs) - xmin) / _PX_FT) + 2)
    r0 = max(0, int((ymax - max(ys)) / _PX_FT) - 1)
    r1 = min(nrows, int((ymax - min(ys)) / _PX_FT) + 2)
    if c1 <= c0 or r1 <= r0:
        return None
    mask = Image.new("1", (c1 - c0, r1 - r0), 0)
    draw = ImageDraw.Draw(mask)
    for ring in rings:
        draw.polygon([((x - xmin) / _PX_FT - c0, (ymax - y) / _PX_FT - r0)
                      for x, y in ring], fill=1)
    m = np.array(mask, dtype=bool)
    vals = slope[r0:r1, c0:c1][m]
    vals = vals[~np.isnan(vals)]
    if vals.size == 0:
        return None
    buildable = float((vals <= _BUILDABLE_GRADE).mean())
    # area can be NaN/None upstream; round(nan) raises, so guard before converting
    area = float(area_sqft) if area_sqft not in (None, "") else 0.0
    if math.isnan(area):
        area = 0.0
    return (round(float(vals.mean()), 1),
            round(float(np.percentile(vals, 95)), 1),   # p95, not the raw max:
            round(buildable * 100, 1),                  # one bad pixel isn't a cliff
            int(round(area * buildable)) or None)


def build(neighborhoods: tuple[str, ...] = _TARGETS) -> dict:
    from sqlalchemy import text

    from ingestion.load import _engine

    eng = _engine()
    with eng.begin() as conn:
        row = conn.execute(text("""
            SELECT ST_XMin(e) x0, ST_YMin(e) y0, ST_XMax(e) x1, ST_YMax(e) y1
            FROM (SELECT ST_Extent(ST_Transform(geom, 3735)) e FROM parcel_master
                  WHERE neighborhood = ANY(:nb) AND geom IS NOT NULL) q
        """), {"nb": list(neighborhoods)}).one()
    pad = 200.0
    bounds = (row.x0 - pad, row.y0 - pad, row.x1 + pad, row.y1 + pad)
    print(f"elevation extent (ft, EPSG:3735): {bounds}", flush=True)

    # cache the derived slope grid: the mosaic is ~250 MB of downloads, and the
    # per-parcel pass is what actually gets iterated on
    import os
    cache = os.path.join(os.path.dirname(__file__), "..", "data", "slope_grid.npy")
    cache = os.path.abspath(cache)
    if os.path.exists(cache):
        slope = np.load(cache)
        print(f"slope grid loaded from cache {cache}", flush=True)
    else:
        slope = _slope_pct(_mosaic(bounds))
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        np.save(cache, slope)
    # also sanitize a cached grid written before this guard existed
    slope[~np.isfinite(slope)] = np.nan
    slope[slope > 300.0] = np.nan
    print(f"slope grid {slope.shape}, median {np.nanmedian(slope):.1f}%", flush=True)

    import json
    updates: list[dict] = []
    with eng.begin() as conn:
        rows = conn.execute(text("""
            SELECT parcel_id, area_sqft,
                   ST_AsGeoJSON(ST_Transform(geom, 3735)) AS gj
            FROM parcel_master
            WHERE neighborhood = ANY(:nb) AND geom IS NOT NULL
        """), {"nb": list(neighborhoods)})
        for pid, area, gj in rows:
            geom = json.loads(gj)
            polys = (geom["coordinates"] if geom["type"] == "MultiPolygon"
                     else [geom["coordinates"]])
            rings = [ring for poly in polys for ring in poly]
            stats = _parcel_stats(slope, bounds, rings, float(area or 0))
            if stats:
                updates.append({"p": pid, "a": stats[0], "m": stats[1],
                                "b": stats[2], "s": stats[3]})
    print(f"computed slope for {len(updates)} parcels", flush=True)

    with eng.begin() as conn:
        for i in range(0, len(updates), 5000):
            conn.execute(text("""
                UPDATE parcel_master SET avg_slope_pct = :a, max_slope_pct = :m,
                       buildable_pct = :b, buildable_sqft = :s
                WHERE parcel_id = :p
            """), updates[i:i + 5000])
    return {"parcels": len(updates)}


if __name__ == "__main__":
    build()
