#!/usr/bin/env python3
"""Status / manifest helper for MSWIM2D satellite input lookup tables.

One job: read the *.dat.gz lookup tables BATSRUS actually consumes and answer
two questions for the data-refresh button (update_satellite_data.sh):

  check <file> --start <iso> --end <iso>
      Does this one year-file cover every hour of [start, end]?  Used to decide
      whether to re-pull + rebuild a source/year.  Exit 0 = complete (leave it),
      exit 3 = missing hours or non-finite values (rebuild it).  A missing file
      also exits 3.

  manifest [--out data/DATA_MANIFEST.txt]
      Scan the newest data for every source, write a human-readable manifest,
      and print the headline: the date the model can be run through reliably
      (= the earliest last-data day among the *active* assimilated sources).

The .dat.gz format (shared by L1 / SolarOrbiter / STEREO writers) is 4 header
lines then whitespace rows; column 0 is seconds since 1965-01-01, and the data
section starts at the line beginning 'Seconds ...'.
"""

import os
import sys
import gzip
import glob
import math
import argparse
import datetime as dt

EPOCH = dt.datetime(1965, 1, 1)
MSWIM2D_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..')
DATA_DIR = os.path.join(MSWIM2D_DIR, 'data')

# Sources, in report order.  `active` = currently transmitting and assimilated
# by the model, so it counts toward the run-reliably cutoff.  STEREO-B stopped
# transmitting in 2014, so it is reported for completeness but never gates a run.
SOURCES = [
    {'key': 'L1',           'label': 'L1 (OMNI/MIDL)', 'dir': 'L1',
     'glob': 'l1_*.dat.gz',                 'active': True},
    {'key': 'SolarOrbiter', 'label': 'Solar Orbiter',  'dir': 'SolarOrbiter',
     'glob': 'SolarOrbiter_*.dat.gz',       'active': True},
    {'key': 'STEREOA',      'label': 'STEREO-A',        'dir': 'STEREOA',
     'glob': 'STEREOA_*.dat.gz',            'active': True},
    {'key': 'STEREOB',      'label': 'STEREO-B',        'dir': 'STEREOB',
     'glob': 'STEREOB_*.dat.gz',            'active': False},
]


def scan_datgz(path):
    """Return (timestamps_set, first_dt, last_dt, nrows, has_nonfinite).

    timestamps_set holds every hour (truncated to the hour) present in the file.
    """
    hours = set()
    first_dt = last_dt = None
    nrows = 0
    has_nonfinite = False
    in_data = False
    with gzip.open(path, 'rt') as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if not in_data:
                # Data section begins right after the 'Seconds ...' column header.
                if s.startswith('Seconds'):
                    in_data = True
                continue
            parts = s.split()
            try:
                secs = float(parts[0])
            except (ValueError, IndexError):
                continue
            # Cheap non-finite guard on the physical columns.
            for tok in parts[1:]:
                lt = tok.lower()
                if 'nan' in lt or 'inf' in lt:
                    has_nonfinite = True
                    break
            ts = EPOCH + dt.timedelta(seconds=secs)
            hours.add(ts.replace(minute=0, second=0, microsecond=0))
            if first_dt is None or ts < first_dt:
                first_dt = ts
            if last_dt is None or ts > last_dt:
                last_dt = ts
            nrows += 1
    return hours, first_dt, last_dt, nrows, has_nonfinite


def newest_file(src):
    files = sorted(glob.glob(os.path.join(DATA_DIR, src['dir'], src['glob'])))
    return files[-1] if files else None


def scan_source(src):
    """Scan ALL year-files for a source; return merged coverage + newest file."""
    files = sorted(glob.glob(os.path.join(DATA_DIR, src['dir'], src['glob'])))
    if not files:
        return None
    all_hours = set()
    first_dt = last_dt = None
    nonfinite = False
    for p in files:
        hours, fd, ld, _, nf = scan_datgz(p)
        all_hours |= hours
        nonfinite = nonfinite or nf
        if fd and (first_dt is None or fd < first_dt):
            first_dt = fd
        if ld and (last_dt is None or ld > last_dt):
            last_dt = ld
    return {
        'files': files, 'newest': files[-1], 'hours': all_hours,
        'first': first_dt, 'last': last_dt, 'nonfinite': nonfinite,
    }


def recent_coverage_pct(hours, last_dt, days=30):
    """Fraction of hours present in the `days` window ending at last_dt."""
    if last_dt is None:
        return None
    start = last_dt - dt.timedelta(days=days)
    expected = 0
    present = 0
    t = start.replace(minute=0, second=0, microsecond=0)
    while t <= last_dt:
        expected += 1
        if t in hours:
            present += 1
        t += dt.timedelta(hours=1)
    return 100.0 * present / expected if expected else None


def cmd_check(args):
    start = dt.datetime.fromisoformat(args.start)
    end = dt.datetime.fromisoformat(args.end)
    if not os.path.exists(args.file):
        print('REBUILD: file missing')
        return 3
    hours, first_dt, last_dt, nrows, nonfinite = scan_datgz(args.file)
    if nonfinite:
        print('REBUILD: non-finite (nan/inf) value present')
        return 3
    # Count hours in [start, end] absent from the file.
    missing = 0
    t = start.replace(minute=0, second=0, microsecond=0)
    while t <= end:
        if t not in hours:
            missing += 1
        t += dt.timedelta(hours=1)
    if missing > 0:
        print('REBUILD: {} missing hour(s) in {}..{}'.format(
            missing, start.date(), end.date()))
        return 3
    print('OK: complete through {}'.format(last_dt))
    return 0


def cmd_manifest(args):
    now = dt.datetime.utcnow()
    rows = []
    # data-safe = EARLIEST last day among active sources (all sources present);
    # last-possible = LATEST last day among active sources (furthest any single
    # source reaches). STEREO-B (inactive) gates neither.
    safe = None
    safe_key = None
    safe_label = None
    latest = None
    latest_key = None
    latest_label = None
    for src in SOURCES:
        info = scan_source(src)
        if info is None:
            rows.append((src, None))
            continue
        rows.append((src, info))
        if src['active'] and info['last'] is not None:
            if safe is None or info['last'] < safe:
                safe = info['last']
                safe_key = src['key']
                safe_label = src['label']
            if latest is None or info['last'] > latest:
                latest = info['last']
                latest_key = src['key']
                latest_label = src['label']

    def iso(d):
        return d.strftime('%Y-%m-%dT%H:%M:%S') if d else 'NA'

    # Dual-format manifest: '#' lines are human comments; data lines are
    # '|'-delimited records with a fixed schema. Fields are space-padded so it
    # reads as a table, but a parser splits on '|' and strips each field.
    L = []
    C = L.append
    BAR = '# ' + '=' * 76
    # Row format shared by the (commented) column header and the SOURCE records,
    # so the header lines up over the data. The record-type cell is 6 wide:
    # 'SOURCE' for data, '#TYPE ' for the header (keeps the leading '#').
    ROW = '{:<6} | {:<12} | {:<14} | {:<6} | {:<19} | {:>9} | {}'

    C(BAR)
    C('# MSWIM2D Satellite Input Data Manifest')
    C('# Generated: {} UTC'.format(now.strftime('%Y-%m-%dT%H:%M:%S')))
    C('#')
    C('# This file is human-readable AND script-parseable:')
    C('#   - Ignore every line beginning with "#" (comments / human summary).')
    C('#   - Data lines are "|"-delimited; split on "|" and strip each field.')
    C('#   - Timestamps are ISO-8601 UTC; "NA" means none. No field contains "|".')
    C('#')
    C('# Record schema (first field is the record type):')
    C('#   SOURCE | key | label | active | last_utc | recent30d_pct | newest_file')
    C('#   DATA_SAFE     | last_utc | limited_by_key  # all active sources present through here')
    C('#   LAST_POSSIBLE | last_utc | source_key      # furthest any single active source reaches')
    C('#')
    C('# last_utc = last hour carrying non-fill solar-wind PLASMA (speed+density),')
    C('# i.e. usable model input, NOT raw record span. active=false never gates a run.')
    C(BAR)
    # Commented column header aligned over the SOURCE rows. '#TYPE' is 5 chars,
    # padded to the 6-wide type cell so it lines up with 'SOURCE' and stays a
    # comment (leading '#').
    C(ROW.format('#TYPE', 'key', 'label', 'active', 'last_utc',
                 'recent30d', 'newest_file'))
    for src, info in rows:
        if info is None:
            C(ROW.format('SOURCE', src['key'], src['label'],
                         str(src['active']).lower(), 'NA', 'NA', 'NONE'))
            continue
        cov = recent_coverage_pct(info['hours'], info['last'])
        cov_s = '{:.1f}'.format(cov) if cov is not None else 'NA'
        rel = os.path.relpath(info['newest'], MSWIM2D_DIR)
        C(ROW.format('SOURCE', src['key'], src['label'],
                     str(src['active']).lower(), iso(info['last']), cov_s, rel))
        if info['nonfinite']:
            C('#   ^^ WARNING: non-finite (nan/inf) values present in {}'.format(rel))
    C('DATA_SAFE | {} | {}'.format(iso(safe), safe_key or 'NA'))
    C('LAST_POSSIBLE | {} | {}'.format(iso(latest), latest_key or 'NA'))
    C(BAR)
    if safe is not None:
        C('# DATA-SAFE (all active sources present) THROUGH: {} UTC  (limited by {})'.format(
            safe.strftime('%Y-%m-%d %H:%M'), safe_label))
        C('# LAST POSSIBLE DAY WITH DATA:                    {} UTC  (from {})'.format(
            latest.strftime('%Y-%m-%d %H:%M'), latest_label))
    else:
        C('# DATA-SAFE THROUGH: UNKNOWN (no active source data found)')
    C('#')
    C('# Notes:')
    C('#   - last_utc is the last hour with non-fill plasma the model propagates;')
    C('#     fill/NaN-plasma hours are dropped at build time (usable input, not span).')
    C('#   - A spacecraft COHO/CDAWeb record can extend later than last_utc when only')
    C('#     its magnetometer keeps reporting. Mid-2025 STEREO-A is the binding case:')
    C('#     PLASTIC plasma stops 2025-06-30 while MAG continues into autumn, so the')
    C('#     dataset "spans to 2025-12-31" but is plasma-empty after June.')
    C('#   - STEREO-B retired 2014 and never gates a run.')
    C(BAR)

    text = '\n'.join(L) + '\n'
    out = args.out or os.path.join(DATA_DIR, 'DATA_MANIFEST.txt')
    with open(out, 'w') as f:
        f.write(text)
    sys.stdout.write(text)
    sys.stdout.write('\nWrote {}\n'.format(os.path.relpath(out, MSWIM2D_DIR)))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd')

    c = sub.add_parser('check', help='check one year-file covers [start,end]')
    c.add_argument('file')
    c.add_argument('--start', required=True, help='ISO datetime, e.g. 2025-01-01')
    c.add_argument('--end', required=True, help='ISO datetime')
    c.set_defaults(func=cmd_check)

    m = sub.add_parser('manifest', help='write data/DATA_MANIFEST.txt (data-safe + last-possible)')
    m.add_argument('--out', default=None)
    m.set_defaults(func=cmd_manifest)

    args = p.parse_args()
    if not getattr(args, 'func', None):
        p.print_help()
        return 1
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
