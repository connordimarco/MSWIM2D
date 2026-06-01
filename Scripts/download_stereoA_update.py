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

for y in range(2020, 2026):
    start = dt.datetime(year=y-1, month=11, day=1)
    end = dt.datetime(year=y+1, month=1, day=31) if y < 2025 else dt.datetime(year=2025, month=12, day=31)
    print(f"Generating STEREOA_{y} (range {start.date()} to {end.date()})...")
    try:
        get_stereo_lookup_table(start, end, sc='A')
        print(f"########## STEREOA_{y} complete. ##########")
    except Exception as e:
        print(f"ERROR on STEREOA_{y}: {e}")
        import traceback
        traceback.print_exc()
