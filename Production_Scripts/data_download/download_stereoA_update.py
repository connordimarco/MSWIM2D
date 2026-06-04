#!/usr/bin/env python3
import os
import sys
import datetime as dt

script_dir = os.path.dirname(os.path.realpath(__file__))
create_imf_path = os.path.join(script_dir, 'create_imf.py')

with open(create_imf_path) as f:
    source = f.read()

# Extract only the function definitions (stop before the top-level loops
# that require swmfpy)
lines = source.split('\n')
func_end = None
for i, line in enumerate(lines):
    if line.startswith('if __name__'):
        func_end = i
        break

if func_end is None:
    print("ERROR: could not find end of function definitions")
    sys.exit(1)

func_source = '\n'.join(lines[:func_end])
namespace = {'__file__': create_imf_path}
exec(compile(func_source, create_imf_path, 'exec'), namespace)
get_stereo_lookup_table = namespace['get_stereo_lookup_table']

import argparse

parser = argparse.ArgumentParser(
    description='Build STEREO-A lookup tables (data/STEREOA/STEREOA_YYYY.dat.gz) '
                'for a range of years. Each year-file spans Nov(Y-1) -> Jan(Y+1); '
                'the current year is capped at today since later months are not '
                'yet posted by SPDF.')
parser.add_argument('--start', type=int, default=2020, help='first year (default 2020)')
parser.add_argument('--end', type=int, default=dt.datetime.now().year,
                    help='last year (default: current year)')
args = parser.parse_args()

now = dt.datetime.now()
for y in range(args.start, args.end + 1):
    start = dt.datetime(year=y-1, month=11, day=1)
    # Don't ask SPDF for months past "now" (+2-day cushion); the underlying
    # download loop skips any month it 404s on, so fetching slightly ahead of
    # the posted data is harmless, but capping keeps the request range sane.
    end = min(dt.datetime(year=y+1, month=1, day=31), now + dt.timedelta(days=2))
    if end <= start:
        print(f"Skipping STEREOA_{y}: nothing in range yet.")
        continue
    print(f"Generating STEREOA_{y} (range {start.date()} to {end.date()})...")
    try:
        get_stereo_lookup_table(start, end, sc='A')
        print(f"########## STEREOA_{y} complete. ##########")
    except Exception as e:
        print(f"ERROR on STEREOA_{y}: {e}")
        import traceback
        traceback.print_exc()
