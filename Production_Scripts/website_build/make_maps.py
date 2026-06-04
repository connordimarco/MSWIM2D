#!/usr/bin/env python3
"""(Re)generate the trajectory-precompute maps for THIS box.

The precompute (run_month.sh / precompute.sbatch) reads two tab-separated maps:

  month_outs.map   YYYYMM \t src \t /abs/path/to/that/month/.outs
  body_months.map  Body   \t firstYYYYMM \t lastYYYYMM

These used to be hand-built once and committed with Great Lakes absolute paths
(/nfs/turbo/...). This script rebuilds them from the trees actually on this box,
so a monthly refresh picks up new months automatically.

month_outs.map sourcing (matches the operational Tim seam at 200407):
  * months <  200407  -> Tim_MSWIM2D_1hr/ reference run (flat files; the month is
    the TRAILING _YYYYMM, because every Tim file shares one _e epoch);
  * months >= 200407  -> the tier trees Output_final / Output_preliminary /
    Output_prediction (most-confident tier wins; the month is the <YYYYMM> dir).

body_months.map is each spice/trajectories/<Body>.dat's covered span (parsed from
its first/last sample) clamped to the months that actually exist in month_outs.map.

Usage:  make_maps.py [--repo DIR] [--out-dir DIR]
  Defaults: repo = two levels up from this file; out-dir = this script's dir.
"""

import os
import re
import sys
import glob
import argparse

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

MIN_SEAM = '200407'                       # < this -> Tim reference; >= this -> our runs
TIER_TREES = ['Output_final', 'Output_preliminary', 'Output_prediction']
TRAIL_MONTH = re.compile(r'_(\d{6})\.outs$')   # Tim flat files
START_RE = re.compile(r'^\s*#START', re.I)


def build_month_outs(repo):
    """month -> (src, abspath), Tim for the pre-seam tail, tier trees beyond."""
    m = {}
    # Tim reference run (pre-seam months only).
    for p in sorted(glob.glob(os.path.join(repo, 'Tim_MSWIM2D_1hr', '*.outs'))):
        g = TRAIL_MONTH.search(os.path.basename(p))
        if g and g.group(1) < MIN_SEAM:
            m.setdefault(g.group(1), ('Tim', os.path.abspath(p)))
    # Tier trees (operational months); most-confident tree wins a month.
    for tier in TIER_TREES:
        for d in sorted(glob.glob(os.path.join(repo, tier, '[0-9]' * 6))):
            ym = os.path.basename(d)
            if ym < MIN_SEAM:
                continue
            outs = sorted(glob.glob(os.path.join(d, 'OH', '*.outs')))
            if outs:
                m.setdefault(ym, (tier.replace('Output_', ''), os.path.abspath(outs[0])))
    return m


def traj_span(path):
    """First/last YYYYMM sampled in a trajectory .dat (rows: year mo dy hr ... )."""
    first = last = None
    started = False
    with open(path) as f:
        for line in f:
            if not started:
                if START_RE.match(line):
                    started = True
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                ym = '%04d%02d' % (int(parts[0]), int(parts[1]))
            except ValueError:
                continue
            if first is None:
                first = ym
            last = ym
    return first, last


def build_body_months(repo, months):
    """Body -> (first, last) from its trajectory .dat, clamped to available months."""
    if not months:
        return {}
    lo, hi = min(months), max(months)
    out = {}
    for p in sorted(glob.glob(os.path.join(repo, 'spice', 'trajectories', '*.dat'))):
        body = os.path.splitext(os.path.basename(p))[0]
        tf, tl = traj_span(p)
        if not tf:
            continue
        out[body] = (max(tf, lo), min(tl, hi))
    return out


def main():
    ap = argparse.ArgumentParser(description='Rebuild month_outs.map + body_months.map for this box.')
    ap.add_argument('--repo', default=REPO)
    ap.add_argument('--out-dir', default=HERE)
    a = ap.parse_args()

    mo = build_month_outs(a.repo)
    mo_path = os.path.join(a.out_dir, 'month_outs.map')
    with open(mo_path, 'w') as f:
        for ym in sorted(mo):
            src, path = mo[ym]
            f.write('%s\t%s\t%s\n' % (ym, src, path))

    bm = build_body_months(a.repo, set(mo))
    bm_path = os.path.join(a.out_dir, 'body_months.map')
    with open(bm_path, 'w') as f:
        for body in sorted(bm):
            first, last = bm[body]
            f.write('%s\t%s\t%s\n' % (body, first, last))

    span = ('%s..%s' % (min(mo), max(mo))) if mo else 'EMPTY'
    sys.stdout.write('wrote %s (%d months, %s)\n'
                     % (os.path.relpath(mo_path, a.repo), len(mo), span))
    sys.stdout.write('wrote %s (%d bodies)\n'
                     % (os.path.relpath(bm_path, a.repo), len(bm)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
