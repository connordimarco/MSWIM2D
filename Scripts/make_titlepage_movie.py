#!/usr/bin/env python3
"""Pre-render Bx field frames for the title page.

Reads the pre-split per-snapshot .bin files, regrids the Bx variable from the
polar (ln r, phi) grid onto a square cartesian window using the same bilinear
math as interpolate.js (sampleVarsAt), colours each frame with the diverging
blue-white-red scale, and writes one PNG per frame into a `movie/` folder.
No satellites, trajectory, axes or lines -- just the field. Assemble into a
video locally, e.g.:

    ffmpeg -framerate 100 -i movie/frame_%05d.png -c:v libx264 \\
           -pix_fmt yuv420p bx.mp4

(100 fps = "one hour per 10 ms".)

Usage:
    python3 make_titlepage_movie.py [target] [boxAU] [N] [stride]

Defaults: target=201101  boxAU=4.5  N=600  stride=1
  target  YYYY for a whole year, YYYYMM for one month, or
          YYYYMM-YYYYMM for an inclusive range of months
  boxAU   half-width of the window in AU (window spans [-boxAU, boxAU])
  N       cartesian resolution per axis (higher = crisper)
  stride  frame step in snapshots (1 = every hourly snapshot)
"""

import sys, os, json
import numpy as np
from PIL import Image
from matplotlib.colors import LinearSegmentedColormap

VAR_BX = 4  # index of Bx in the snapshot variable arrays

DATA = 'MSWIM2D_Data_New/snapshots'
OUTDIR = 'movie'

# Diverging scale matching SCALE_DIVERGING in interpolate.js:
# blue (negative) -> white (zero) -> red (positive).
CMAP = LinearSegmentedColormap.from_list('bwr_mswim', [
    (33/255, 102/255, 172/255),
    (247/255, 247/255, 247/255),
    (178/255, 24/255, 43/255),
])


def build_geometry(grid, box, N):
    """Precompute, once, the polar-grid gather indices/weights + validity mask
    for the fixed cartesian sample window. Returns arrays shaped (N, N)."""
    n1, n2 = grid['n1'], grid['n2']
    radMin, radMax = grid['radMin'], grid['radMax']
    phiMin, phiMax = grid['phiMin'], grid['phiMax']

    ax = np.linspace(-box, box, N)
    XX, YY = np.meshgrid(ax, ax)            # YY rows, XX cols
    r = np.sqrt(XX * XX + YY * YY)
    valid = r > 0
    r = np.where(valid, r, 1.0)
    lnR = np.log(r)
    valid &= (lnR >= radMin) & (lnR <= radMax)

    phi = np.degrees(np.arctan2(YY, XX))
    dPhi = (phiMax - phiMin) / (n2 - 1)
    phiMinG, phiMaxG = phiMin - dPhi, phiMax + dPhi
    phiQ = phi.copy()
    phiQ = np.where(phiQ < phiMinG, phiQ + 360, phiQ)
    phiQ = np.where(phiQ > phiMaxG, phiQ - 360, phiQ)
    valid &= (phiQ >= phiMinG) & (phiQ <= phiMaxG)

    iNorm = (lnR - radMin) * (n1 - 1) / (radMax - radMin)
    jNorm = (phiQ - phiMinG) * (n2 + 1) / (phiMaxG - phiMinG)

    i1 = np.floor(iNorm).astype(np.int64)
    j1 = np.floor(jNorm).astype(np.int64)
    i2 = i1 + 1
    j2 = j1 + 1
    dx = iNorm - i1
    dy = jNorm - j1
    valid &= (i1 >= 0) & (i2 < n1)

    def ghost(j):
        return np.where(j <= 0, n2 - 1, np.where(j > n2, 0, j - 1))
    jd1 = ghost(j1)
    jd2 = ghost(j2)

    # Clamp indices so gathers stay in-bounds where invalid (masked out later).
    i1c = np.clip(i1, 0, n1 - 1); i2c = np.clip(i2, 0, n1 - 1)
    jd1c = np.clip(jd1, 0, n2 - 1); jd2c = np.clip(jd2, 0, n2 - 1)

    return dict(valid=valid, dx=dx, dy=dy,
                i1=i1c, i2=i2c, jd1=jd1c, jd2=jd2c, n1=n1, n2=n2)


def sample_frame(bx_flat, geom):
    """Bilinear-regrid one Bx snapshot (flat n1*n2) onto the cartesian grid."""
    A = bx_flat.reshape(geom['n2'], geom['n1'])   # A[j, i] = flat[i + j*n1]
    i1, i2, jd1, jd2 = geom['i1'], geom['i2'], geom['jd1'], geom['jd2']
    dx, dy = geom['dx'], geom['dy']
    v11 = A[jd1, i1]; v21 = A[jd1, i2]
    v12 = A[jd2, i1]; v22 = A[jd2, i2]
    out = (1 - dy) * ((1 - dx) * v11 + dx * v21) + dy * ((1 - dx) * v12 + dx * v22)
    return np.where(geom['valid'], out, np.nan)


def read_bx(path, stride_cells):
    buf = np.fromfile(path, dtype='<f4', count=stride_cells,
                      offset=VAR_BX * stride_cells * 4)
    return buf


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else '201101'
    box    = float(sys.argv[2]) if len(sys.argv) > 2 else 4.5
    N      = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    stride = int(sys.argv[4]) if len(sys.argv) > 4 else 1

    manifest = json.load(open(os.path.join(DATA, 'manifest.json')))
    grid = manifest['grid']
    stride_cells = grid['n1'] * grid['n2']
    geom = build_geometry(grid, box, N)

    if '-' in target:
        lo, hi = target.split('-', 1)
        months = sorted(m for m in manifest['months'] if lo <= m <= hi)
    elif len(target) == 6:
        months = [target] if target in manifest['months'] else []
    else:
        months = sorted(m for m in manifest['months'] if m.startswith(target))
    if not months:
        sys.exit('No snapshots for ' + target)

    # Enumerate every snapshot file for the year, in chronological order.
    files = []
    for mo in months:
        n = len(manifest['months'][mo])
        for idx in range(0, n, stride):
            files.append(os.path.join(DATA, mo, '%04d.bin' % idx))
    print('%d frames (%s, stride %d) over %d month(s), N=%d' %
          (len(files), target, stride, len(months), N))

    # Pass 1: robust symmetric colour range from a subsample of frames.
    sample_every = max(1, len(files) // 200)
    vals = []
    for k in range(0, len(files), sample_every):
        f = sample_frame(read_bx(files[k], stride_cells), geom)
        vals.append(f[np.isfinite(f)])
    allv = np.abs(np.concatenate(vals))
    M = float(np.percentile(allv, 99.5))
    print('symmetric colour range +/- %.4g nT (99.5th pct of |Bx|)' % M)

    # Pass 2: render every frame straight to its own PNG (no accumulation).
    os.makedirs(OUTDIR, exist_ok=True)
    for old in os.listdir(OUTDIR):                # clear stale frames
        if old.endswith('.png'):
            os.remove(os.path.join(OUTDIR, old))

    cmap = CMAP
    for k, path in enumerate(files):
        f = sample_frame(read_bx(path, stride_cells), geom)
        bad = ~np.isfinite(f)                          # sun / inner boundary
        norm = np.where(bad, 0.0, np.clip((f + M) / (2 * M), 0.0, 1.0))
        rgba = (cmap(norm) * 255).astype(np.uint8)     # (N, N, 4)
        rgba[bad] = (255, 255, 255, 255)               # white, opaque Sun
        rgba[..., 3] = 255                             # fully opaque everywhere
        Image.fromarray(rgba, 'RGBA').save(
            os.path.join(OUTDIR, 'frame_%05d.png' % k))
        if k % 250 == 0:
            print('  wrote %d/%d' % (k, len(files)))

    print('wrote %d PNG frames (%dx%d) to %s/' % (len(files), N, N, OUTDIR))


if __name__ == '__main__':
    main()
