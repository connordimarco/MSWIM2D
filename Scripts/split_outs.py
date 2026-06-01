#!/usr/bin/env python3
"""Pre-split BATSRUS .outs files into per-snapshot binary blobs + manifest.json.

Usage:
    python3 split_outs.py                      # process all .outs files
    python3 split_outs.py 199601               # only the 199601 month
    python3 split_outs.py 199601 199602        # specific months
    python3 split_outs.py --coarse 199601      # also emit a decimated grid
    python3 split_outs.py --coarse=8 199601    # decimation factor 8 (default 4)
    python3 split_outs.py --coarse-only 199601 # ONLY the decimated grid (no full-res)

The month label for a file is derived from its `_eYYYYMMDD-...` start stamp
(e.g. z=0_var_1_e19960101-000000-000_19960201-000000-000.outs -> 199601), so the
snapshot directory names, manifest keys, and the server endpoint's results all
agree. Legacy `<YYYYMM>.outs` names fall back to the bare stem.

`--coarse` additionally writes a stride-decimated copy of every snapshot to a
parallel `snapshots_coarse/` tree (+ its own manifest). The full-resolution
trajectory interpolation is done server-side by INTERPOLATE.exe; the coarse grid
exists only to render the lightweight field-movie in the browser. Because the
full-res `.bin` files no longer have a consumer, `--coarse-only` skips them
entirely (the website build uses this).
"""
import struct, os, sys, json, math, re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Data root may be set via the MSWIM2D_DATA_NEW env var so this script can live
# in Scripts/ and operate on a separate build tree; otherwise it falls back to a
# MSWIM2D_Data_New/ dir beside the script (the website-repo layout).
DATA_ROOT = os.environ.get(
    'MSWIM2D_DATA_NEW', os.path.join(SCRIPT_DIR, 'MSWIM2D_Data_New'))
OUTS_DIR = os.path.join(DATA_ROOT, 'Output_flat')
OUT_DIR = os.path.join(DATA_ROOT, 'snapshots')
COARSE_DIR = os.path.join(DATA_ROOT, 'snapshots_coarse')

_EPOCH_RE = re.compile(r'_e(\d{4})(\d{2})\d{2}-')


def month_of(path):
    """Month label 'YYYYMM' from a filename's _e start stamp, or its bare stem."""
    m = _EPOCH_RE.search(os.path.basename(path))
    if m:
        return m.group(1) + m.group(2)
    return os.path.splitext(os.path.basename(path))[0]


def read_record(f):
    hdr = f.read(4)
    if len(hdr) < 4:
        return None
    n = struct.unpack('<i', hdr)[0]
    data = f.read(n)
    f.read(4)
    return data


def _decimate(var_recs, n1, n2, nVar, factor):
    """Stride-decimate each variable block by `factor` in both grid axes.
    Returns (bytes, n1c, n2c). Uses numpy if available, else pure Python.
    Layout in/out is d[i + j*n1] with i in [0,n1), j in [0,n2)."""
    iis = list(range(0, n1, factor))
    jjs = list(range(0, n2, factor))
    n1c, n2c = len(iis), len(jjs)
    try:
        import numpy as np
        out = bytearray()
        for rec in var_recs:
            a = np.frombuffer(rec, dtype='<f4').reshape(n2, n1)
            out += np.ascontiguousarray(a[::factor, ::factor]).astype('<f4').tobytes()
        return bytes(out), n1c, n2c
    except ImportError:
        out = bytearray()
        for rec in var_recs:
            a = struct.unpack('<%df' % (n1 * n2), rec)
            vals = [a[i + j * n1] for j in jjs for i in iis]
            out += struct.pack('<%df' % len(vals), *vals)
        return bytes(out), n1c, n2c


def _coarsen_grid(grid, n1c, n2c, factor):
    """Coarse grid metadata. Bounds come from the actually-sampled nodes; because
    the log-radius and phi grids are uniformly spaced, striding keeps them
    uniform so the client's linear index->coord mapping stays exact at nodes."""
    cg = dict(grid)
    cg['n1'], cg['n2'] = n1c, n2c
    cg['coarseFactor'] = factor
    # radMin/phiMin are at index 0 (unchanged); radMax/phiMax shift to the last
    # sampled node = first + (count-1)*step*factor.
    dr = (grid['radMax'] - grid['radMin']) / (grid['_n1full'] - 1)
    dphi = (grid['phiMax'] - grid['phiMin']) / (grid['_n2full'] - 1)
    cg['radMax'] = round(grid['radMin'] + (n1c - 1) * factor * dr, 6)
    cg['phiMax'] = round(grid['phiMin'] + (n2c - 1) * factor * dphi, 4)
    cg['rMax'] = round(math.exp(cg['radMax']), 4)
    cg.pop('_n1full', None)
    cg.pop('_n2full', None)
    return cg


def process_file(path, out_dir, coarse_factor=None, coarse_out_dir=None,
                 write_full=True):
    name = month_of(path)
    month_dir = os.path.join(out_dir, name)
    if write_full:
        os.makedirs(month_dir, exist_ok=True)
    coarse_month_dir = None
    if coarse_factor:
        coarse_month_dir = os.path.join(coarse_out_dir, name)
        os.makedirs(coarse_month_dir, exist_ok=True)

    timestamps = []
    grid = None
    coarse_grid = None

    with open(path, 'rb') as f:
        idx = 0
        while True:
            rec = read_record(f)
            if rec is None:
                break
            ts = rec[:19].decode('ascii', errors='replace')

            rec = read_record(f)
            nDim = struct.unpack_from('<i', rec, 8)[0]
            nParam = struct.unpack_from('<i', rec, 12)[0]
            nVar = struct.unpack_from('<i', rec, 16)[0]

            rec = read_record(f)
            nd = abs(nDim)
            dims = struct.unpack('<' + 'i' * nd, rec)
            n1, n2 = dims[0], dims[1]

            rec = read_record(f)
            params = struct.unpack('<' + 'f' * nParam, rec) if nParam > 0 else ()

            rec = read_record(f)
            varnames = rec.decode('ascii', errors='replace').strip()

            rec = read_record(f)

            if grid is None:
                coords = struct.unpack('<' + 'f' * (n1 * n2 * nd), rec)
                x = coords[:n1 * n2]
                y = coords[n1 * n2:2 * n1 * n2]
                lnr = [math.log(math.sqrt(x[i] ** 2 + y[i] ** 2)) for i in range(n1)]
                phi_raw = [math.atan2(y[j * n1], x[j * n1]) * 180.0 / math.pi for j in range(n2)]
                phi = [p if p >= phi_raw[0] else p + 360 for p in phi_raw]
                names = varnames.split()
                grid = {
                    'n1': n1, 'n2': n2,
                    'nDim': nDim, 'nVar': nVar, 'nParam': nParam,
                    'varNames': names[nd:nd + nVar],
                    'paramNames': names[nd + nVar:],
                    'params': [round(p, 6) for p in params],
                    'radMin': round(lnr[0], 6),
                    'radMax': round(lnr[-1], 6),
                    'phiMin': round(phi[0], 4),
                    'phiMax': round(phi[-1], 4),
                    'rMin': round(math.exp(lnr[0]), 4),
                    'rMax': round(math.exp(lnr[-1]), 4),
                    # carried only to compute coarse bounds; stripped before write.
                    '_n1full': n1, '_n2full': n2,
                }

            var_recs = [read_record(f) for _ in range(nVar)]

            if write_full:
                with open(os.path.join(month_dir, '%04d.bin' % idx), 'wb') as out:
                    for r in var_recs:
                        out.write(r)

            if coarse_factor:
                cbytes, n1c, n2c = _decimate(var_recs, n1, n2, nVar, coarse_factor)
                with open(os.path.join(coarse_month_dir, '%04d.bin' % idx), 'wb') as out:
                    out.write(cbytes)
                if coarse_grid is None:
                    coarse_grid = _coarsen_grid(grid, n1c, n2c, coarse_factor)

            timestamps.append(ts)
            idx += 1
            if idx % 100 == 0:
                sys.stdout.write('\r  %s: %d snapshots' % (name, idx))
                sys.stdout.flush()

    # Strip the internal helper keys from the full-res grid before returning.
    if grid is not None:
        grid.pop('_n1full', None)
        grid.pop('_n2full', None)
    sys.stdout.write('\r  %s: %d snapshots\n' % (name, idx))
    return name, timestamps, grid, coarse_grid


def _merge_manifest(out_dir, results, grid_key='grid'):
    """Load/merge a snapshots manifest at out_dir from a list of (name, ts, grid)."""
    manifest_path = os.path.join(out_dir, 'manifest.json')
    manifest = {grid_key: None, 'months': {}}
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)
    for name, timestamps, grid in results:
        if manifest[grid_key] is None and grid is not None:
            manifest[grid_key] = grid
        manifest['months'][name] = timestamps
    manifest['months'] = dict(sorted(manifest['months'].items()))
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f)
    return manifest


def main():
    argv = sys.argv[1:]
    coarse_factor = None
    write_full = True
    targets = []
    for a in argv:
        if a == '--coarse':
            coarse_factor = coarse_factor or 4
        elif a.startswith('--coarse='):
            coarse_factor = int(a.split('=', 1)[1])
        elif a == '--coarse-only':
            coarse_factor = coarse_factor or 4
            write_full = False
        elif a.startswith('--coarse-only='):
            coarse_factor = int(a.split('=', 1)[1])
            write_full = False
        else:
            targets.append(a)

    if write_full:
        os.makedirs(OUT_DIR, exist_ok=True)
    if coarse_factor:
        os.makedirs(COARSE_DIR, exist_ok=True)

    files = sorted(f for f in os.listdir(OUTS_DIR) if f.endswith('.outs'))
    if targets:
        files = [f for f in files if month_of(f) in targets]

    if not files:
        print('No matching .outs files found in %s' % OUTS_DIR)
        return

    fine, coarse = [], []
    for fname in files:
        print('Processing %s...' % fname)
        name, timestamps, grid, coarse_grid = process_file(
            os.path.join(OUTS_DIR, fname), OUT_DIR,
            coarse_factor=coarse_factor, coarse_out_dir=COARSE_DIR,
            write_full=write_full)
        fine.append((name, timestamps, grid))
        if coarse_factor:
            coarse.append((name, timestamps, coarse_grid))

    if write_full:
        manifest = _merge_manifest(OUT_DIR, fine)
    else:
        manifest = {'grid': fine[0][2], 'months': {n: t for n, t, _ in fine}}
    if coarse_factor:
        _merge_manifest(COARSE_DIR, coarse)

    total = sum(len(v) for v in manifest['months'].values())
    g = manifest['grid']
    if write_full:
        sz = total * g['nVar'] * g['n1'] * g['n2'] * 4
        print('Done: %d months, %d snapshots' % (len(manifest['months']), total))
        print('Full-res disk usage: %.1f GB' % (sz / 1e9))
    else:
        print('Done: %d months, %d snapshots (coarse-only)' % (
            len(manifest['months']), total))
    if coarse_factor:
        cg = _merge_manifest(COARSE_DIR, [])['grid']
        csz = total * cg['nVar'] * cg['n1'] * cg['n2'] * 4
        print('Coarse (factor %d): %dx%d grid, %.2f GB' % (
            coarse_factor, cg['n1'], cg['n2'], csz / 1e9))


if __name__ == '__main__':
    main()
