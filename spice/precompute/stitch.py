#!/usr/bin/env python3
"""Stitch per-month INTERP_OUTPUT.exe traces into per-body/year chunks.

Each spice/interp_out/<Body>/<YYYYMM>.dat row has a `t` column = seconds since the
epoch stamped in that month's .outs filename (`_eYYYYMMDD-HHMMSS`). We recover the
absolute UTC time = epoch + t, concatenate all of a body's months in time order,
dedupe on timestamp (adjacent months can share a boundary snapshot), and write
<out>/<Body>/<YYYY>.csv plus a manifest.json the website dropdown reads.

Data rows are 21 fixed-width es13.6 fields with NO separators (adjacent negatives
glue), so we parse with a scientific-notation regex, not a whitespace split.
Column order (header lies — cols 2,3 are r,phi not x,y):
  t  r[AU]  phi[deg]  + 18 model vars (VARS below)

  python stitch.py --interp-out spice/interp_out --outs-map spice/month_outs.map \
         --out spice/chunks [--plasma-only]
"""
import os
import re
import json
import argparse
import datetime as dt

VARS = ['rho', 'ux', 'uy', 'uz', 'bx', 'by', 'bz', 'p',
        'neurho', 'neuux', 'neuuy', 'neuuz', 'neup',
        'ne4rho', 'ne4ux', 'ne4uy', 'ne4uz', 'ne4p']
PLASMA = ['rho', 'ux', 'uy', 'uz', 'bx', 'by', 'bz', 'p']

NUM = re.compile(r'[+-]?\d\.\d{6}[eE][+-]\d{2}')
EPOCH_RE = re.compile(r'_e(\d{8})-(\d{6})')


def load_epochs(outs_map):
    """YYYYMM -> epoch datetime, parsed from the .outs basename in the map."""
    epochs = {}
    with open(outs_map) as f:
        for line in f:
            ym, _src, path = line.rstrip('\n').split('\t')
            m = EPOCH_RE.search(os.path.basename(path))
            if not m:
                raise SystemExit('no epoch in filename: %s' % path)
            d, t = m.group(1), m.group(2)
            epochs[ym] = dt.datetime(int(d[:4]), int(d[4:6]), int(d[6:8]),
                                     int(t[:2]), int(t[2:4]), int(t[4:6]))
    return epochs


def parse_month(path, epoch):
    """Yield (abs_time, r, phi, [18 vars]) for each data row of one month file."""
    with open(path) as f:
        next(f, None)  # header line
        for line in f:
            nums = NUM.findall(line)
            if len(nums) != 21:
                continue  # malformed / blank
            vals = [float(x) for x in nums]
            t, r, phi = vals[0], vals[1], vals[2]
            when = epoch + dt.timedelta(seconds=t)
            yield when, r, phi, vals[3:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interp-out', required=True)
    ap.add_argument('--outs-map', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--plasma-only', action='store_true',
                    help='write only the 8 plasma vars (half the size)')
    a = ap.parse_args()

    epochs = load_epochs(a.outs_map)
    keep = PLASMA if a.plasma_only else VARS
    keep_idx = [VARS.index(v) for v in keep]
    os.makedirs(a.out, exist_ok=True)

    bodies = sorted(d for d in os.listdir(a.interp_out)
                    if os.path.isdir(os.path.join(a.interp_out, d)))
    manifest = {}

    for body in bodies:
        bdir = os.path.join(a.interp_out, body)
        months = sorted(f[:-4] for f in os.listdir(bdir) if f.endswith('.dat'))
        seen = set()
        rows = []  # (when, r, phi, kept_vals)
        for ym in months:
            if ym not in epochs:
                print('  WARN %s/%s: no epoch in map, skipped' % (body, ym))
                continue
            for when, r, phi, allvars in parse_month(
                    os.path.join(bdir, ym + '.dat'), epochs[ym]):
                if when in seen:
                    continue
                seen.add(when)
                rows.append((when, r, phi, [allvars[i] for i in keep_idx]))
        rows.sort(key=lambda x: x[0])

        # split per calendar year
        outbody = os.path.join(a.out, body)
        os.makedirs(outbody, exist_ok=True)
        years = {}
        for when, r, phi, kv in rows:
            years.setdefault(when.year, []).append((when, r, phi, kv))
        header = 'datetime,r_AU,phi_deg,' + ','.join(keep) + '\n'
        for yr, yrows in sorted(years.items()):
            with open(os.path.join(outbody, '%d.csv' % yr), 'w') as f:
                f.write(header)
                for when, r, phi, kv in yrows:
                    f.write('%s,%.6e,%.6e,%s\n' % (
                        when.strftime('%Y-%m-%dT%H:%M:%S'), r, phi,
                        ','.join('%.6e' % v for v in kv)))
        manifest[body] = {
            'years': sorted(years),
            'n_points': len(rows),
            'start': rows[0][0].strftime('%Y-%m-%dT%H:%M:%S') if rows else None,
            'end': rows[-1][0].strftime('%Y-%m-%dT%H:%M:%S') if rows else None,
        }
        print('  %-13s %6d pts  %s..%s  years %d-%d' % (
            body, len(rows),
            manifest[body]['start'], manifest[body]['end'],
            min(years) if years else 0, max(years) if years else 0))

    with open(os.path.join(a.out, 'manifest.json'), 'w') as f:
        json.dump({'vars': ['r_AU', 'phi_deg'] + keep, 'bodies': manifest},
                  f, indent=2)
    print('wrote manifest.json (%d bodies)' % len(manifest))


if __name__ == '__main__':
    main()
