#!/usr/bin/env python3
"""Pre-split BATSRUS .outs files into per-snapshot binary blobs + manifest.json.

Usage:
    python3 split_outs.py              # process all .outs files
    python3 split_outs.py 199804       # process only 199804.outs
    python3 split_outs.py 199804 199805  # process specific months
"""
import struct, os, sys, json, math

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Data root may be set via the MSWIM2D_DATA_NEW env var so this script can live
# in Scripts/ and operate on a separate build tree; otherwise it falls back to a
# MSWIM2D_Data_New/ dir beside the script (the website-repo layout).
DATA_ROOT = os.environ.get(
    'MSWIM2D_DATA_NEW', os.path.join(SCRIPT_DIR, 'MSWIM2D_Data_New'))
OUTS_DIR = os.path.join(DATA_ROOT, 'Output_flat')
OUT_DIR = os.path.join(DATA_ROOT, 'snapshots')


def read_record(f):
    hdr = f.read(4)
    if len(hdr) < 4:
        return None
    n = struct.unpack('<i', hdr)[0]
    data = f.read(n)
    f.read(4)
    return data


def process_file(path, out_dir):
    name = os.path.splitext(os.path.basename(path))[0]
    month_dir = os.path.join(out_dir, name)
    os.makedirs(month_dir, exist_ok=True)

    timestamps = []
    grid = None

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
                }

            var_data = bytearray()
            for v in range(nVar):
                rec = read_record(f)
                var_data.extend(rec)

            with open(os.path.join(month_dir, '%04d.bin' % idx), 'wb') as out:
                out.write(bytes(var_data))

            timestamps.append(ts)
            idx += 1
            if idx % 100 == 0:
                sys.stdout.write('\r  %s: %d snapshots' % (name, idx))
                sys.stdout.flush()

    sys.stdout.write('\r  %s: %d snapshots\n' % (name, idx))
    return name, timestamps, grid


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    files = sorted(f for f in os.listdir(OUTS_DIR) if f.endswith('.outs'))
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
        files = [f for f in files if any(t in f for t in targets)]

    if not files:
        print('No matching .outs files found in %s' % OUTS_DIR)
        return

    manifest_path = os.path.join(OUT_DIR, 'manifest.json')
    manifest = {'grid': None, 'months': {}}
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)

    for fname in files:
        print('Processing %s...' % fname)
        name, timestamps, grid = process_file(
            os.path.join(OUTS_DIR, fname), OUT_DIR
        )
        if manifest['grid'] is None:
            manifest['grid'] = grid
        manifest['months'][name] = timestamps

    manifest['months'] = dict(sorted(manifest['months'].items()))

    with open(manifest_path, 'w') as f:
        json.dump(manifest, f)

    total = sum(len(v) for v in manifest['months'].values())
    g = manifest['grid']
    sz = total * g['nVar'] * g['n1'] * g['n2'] * 4
    print('Done: %d months, %d snapshots' % (len(manifest['months']), total))
    print('Disk usage: %.1f GB' % (sz / 1e9))


if __name__ == '__main__':
    main()
