#!/usr/bin/env python3
"""Generate the website's products.json - the model-OUTPUT confidence tiers.

The MSWIM2D production run is one continuous BATSRUS simulation split into three
output trees by data confidence:

  final        fully data-constrained reanalysis (all assimilated sources present)
  preliminary  thinner spacecraft data; provisional, will be revised
  prediction   persistence forecast past the data frontier; no new data

The two seam dates are NOT hard-coded here: they come from data/DATA_MANIFEST.txt
(written by satellite_data_status.py), where

  DATA_SAFE      = last hour all active assimilated sources are present  -> end of FINAL
  LAST_POSSIBLE  = furthest any single active source reaches             -> end of PRELIMINARY

This script reads those two records, decides which confidence tier each available
month belongs to, and writes website_data/MSWIM2D_Data_New/products.json - the
small file the Data page reads to draw its tier cards, timeline, and warnings.

Two tiering modes, chosen automatically:

  * source-tree (authoritative): if any of Output_final / Output_preliminary /
    Output_prediction exist, a month's tier = the tree it came from. Months that
    are served (in Output_flat) but in no tree - e.g. pre-200407 Tim-reference
    months - are FINAL.
  * boundary (today's fallback): with no tiered trees, enumerate the served months
    from Output_flat and tier each by comparing its YYYYMM to the two seam months.

Either way the boundary datetimes in products.json come straight from the manifest,
so the frontend can flag a window past LAST_POSSIBLE as prediction even before a
prediction tree is built.

Usage:
  build_products_manifest.py [--data-new DIR] [--manifest FILE] [--root DIR] [--out FILE]
"""

import os
import re
import sys
import glob
import json
import argparse
import datetime as dt

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..'))

# Tiered source trees, in confidence order (most-confident first wins a month).
TIER_TREES = [
    ('final',       'Output_final'),
    ('preliminary', 'Output_preliminary'),
    ('prediction',  'Output_prediction'),
]
TIER_ORDER = ['final', 'preliminary', 'prediction']

TIER_NOTE = {
    'final':       'All assimilated solar-wind sources present.',
    'preliminary': 'Thinner spacecraft data; provisional, will be revised.',
    'prediction':  'Persistence forecast past the data frontier; no new data.',
}
TIER_LABEL = {'final': 'Final', 'preliminary': 'Preliminary', 'prediction': 'Prediction'}

MONTH_RE = re.compile(r'^\d{6}$')
# YYYYMM from the _e start stamp in a flattened .outs filename, e.g.
# z=0_var_1_e20250501-010000-000_20250601-000000-000.outs -> 202505
OUTS_MONTH_RE = re.compile(r'_e(\d{4})(\d{2})\d{2}-')


def parse_manifest(path):
    """Return (data_safe_iso, safe_key, last_possible_iso, last_key) from DATA_MANIFEST.txt.

    Data lines are '|'-delimited; '#' lines are human comments. Missing -> None.
    """
    safe = safe_key = last = last_key = None
    if not os.path.exists(path):
        return safe, safe_key, last, last_key
    with open(path) as f:
        for line in f:
            if line.lstrip().startswith('#'):
                continue
            parts = [p.strip() for p in line.split('|')]
            if not parts:
                continue
            if parts[0] == 'DATA_SAFE' and len(parts) >= 2:
                safe = None if parts[1] == 'NA' else parts[1]
                safe_key = parts[2] if len(parts) >= 3 else None
            elif parts[0] == 'LAST_POSSIBLE' and len(parts) >= 2:
                last = None if parts[1] == 'NA' else parts[1]
                last_key = parts[2] if len(parts) >= 3 else None
    return safe, safe_key, last, last_key


def iso_to_month(iso):
    """'2025-06-30T23:00:00' -> '202506' (the seam month). None passes through."""
    return None if not iso else iso[0:4] + iso[5:7]


def months_in_tree(tree_dir):
    """Sorted YYYYMM dirs directly under a tier tree (only those holding an OH/*.outs)."""
    out = []
    for d in sorted(glob.glob(os.path.join(tree_dir, '[0-9]' * 6))):
        ym = os.path.basename(d)
        if MONTH_RE.match(ym) and glob.glob(os.path.join(d, 'OH', '*.outs')):
            out.append(ym)
    return out


def months_in_flat(flat_dir):
    """YYYYMM set actually served - parsed from the flattened Output_flat/*.outs names."""
    months = set()
    for p in glob.glob(os.path.join(flat_dir, '*.outs')):
        m = OUTS_MONTH_RE.search(os.path.basename(p))
        if m:
            months.add(m.group(1) + m.group(2))
    return months


def tier_by_boundary(ym, safe_month, last_month):
    """Tier a month by comparing YYYYMM strings to the two seam months.

    No boundaries known -> everything is final (safest default).
    """
    if last_month and ym > last_month:
        return 'prediction'
    if safe_month and ym > safe_month:
        return 'preliminary'
    return 'final'


def build(data_new, manifest_path, root):
    safe_iso, safe_key, last_iso, last_key = parse_manifest(manifest_path)
    safe_month, last_month = iso_to_month(safe_iso), iso_to_month(last_iso)

    flat_dir = os.path.join(data_new, 'Output_flat')
    served = months_in_flat(flat_dir)

    # tier_of[ym] -> tier name
    tier_of = {}

    tree_dirs = [(t, os.path.join(root, d)) for t, d in TIER_TREES]
    have_trees = [td for t, td in tree_dirs if os.path.isdir(td)]

    if have_trees:
        # Source-tree mode: most-confident tree wins each month.
        for tier, tdir in tree_dirs:
            if not os.path.isdir(tdir):
                continue
            for ym in months_in_tree(tdir):
                tier_of.setdefault(ym, tier)
        # Served months absent from every tree: classify by the seam dates, not a
        # blanket 'final'. Pre-200407 Tim-reference months are < DATA_SAFE so still
        # land in final, but stale post-frontier orphans left in Output_flat (before
        # their preliminary/prediction trees rsync in) correctly read as
        # preliminary/prediction instead of being mislabeled final.
        for ym in served:
            tier_of.setdefault(ym, tier_by_boundary(ym, safe_month, last_month))
        mode = 'source-tree'
    else:
        # Boundary mode: tier each served month by the seam dates.
        for ym in served:
            tier_of[ym] = tier_by_boundary(ym, safe_month, last_month)
        mode = 'boundary'

    # Per-tier display range = min/max month present.
    by_tier = {t: [] for t in TIER_ORDER}
    for ym, tier in tier_of.items():
        by_tier.setdefault(tier, []).append(ym)

    limited_by = {'final': safe_key, 'preliminary': last_key, 'prediction': None}
    tiers = {}
    for t in TIER_ORDER:
        ms = sorted(by_tier.get(t, []))
        tiers[t] = {
            'label': TIER_LABEL[t],
            'start': ms[0] if ms else None,
            'end':   ms[-1] if ms else None,
            'months': len(ms),
            'limited_by': limited_by[t] if ms else None,   # no constraint to report on an empty tier
            'note': TIER_NOTE[t],
        }

    return {
        'generated': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'mode': mode,
        'boundaries': {'data_safe': safe_iso, 'last_possible': last_iso},
        'tiers': tiers,
    }


def main():
    ap = argparse.ArgumentParser(description='Write the website products.json (output confidence tiers).')
    ap.add_argument('--root', default=ROOT, help='repo root (default: parent of Production_Scripts/)')
    ap.add_argument('--data-new', default=None,
                    help='MSWIM2D_Data_New dir (default: <root>/website_data/MSWIM2D_Data_New)')
    ap.add_argument('--manifest', default=None,
                    help='DATA_MANIFEST.txt (default: <root>/data/DATA_MANIFEST.txt)')
    ap.add_argument('--out', default=None, help='output path (default: <data-new>/products.json)')
    args = ap.parse_args()

    root = os.path.normpath(args.root)
    data_new = args.data_new or os.path.join(root, 'website_data', 'MSWIM2D_Data_New')
    manifest_path = args.manifest or os.path.join(root, 'data', 'DATA_MANIFEST.txt')
    out = args.out or os.path.join(data_new, 'products.json')

    products = build(data_new, manifest_path, root)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(products, f, indent=2)
        f.write('\n')
    try:
        os.chmod(out, 0o644)  # apache serves it over NFS -> needs other-read
    except OSError:
        pass

    json.dump(products, sys.stdout, indent=2)
    sys.stdout.write('\nWrote {} ({} mode)\n'.format(
        os.path.relpath(out, root), products['mode']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
