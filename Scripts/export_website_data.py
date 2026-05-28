"""Export input data for the MSWIM2D website.

Reads the MIDL .dat.gz files (propagated solar wind) and, for Solar Orbiter,
joins with the raw CSVs to recover original spacecraft position.

Outputs one CSV per source into Website_data/:
  l1.csv        — datetime, lon, Br, Blat, Blon, Ur, Ulat, Ulon, n, T
  stereoA.csv   — datetime, lon, Br, Blat, Blon, Ur, Ulat, Ulon, n, T
  stereoB.csv   — datetime, lon, Br, Blat, Blon, Ur, Ulat, Ulon, n, T
  solo.csv      — datetime, lon, orig_r_au, orig_lon, Br, Blat, Blon, Ur, Ulat, Ulon, n, T
"""
import gzip
import glob
import os

import numpy as np
import pandas as pd

MSWIM2D_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..')
DATA_DIR = os.path.join(MSWIM2D_DIR, 'data')
OUT_DIR = os.path.join(MSWIM2D_DIR, 'Website_data')

EPOCH = pd.Timestamp('1965-01-01', tz='UTC')

MIDL_COLS = ['seconds', 'lon', 'Br', 'Blat', 'Blon', 'Ur', 'Ulat', 'Ulon', 'n', 'T']


def read_midl(path):
    """Read a MIDL .dat.gz file into a DataFrame with ISO datetimes."""
    with gzip.open(path, 'rt') as f:
        lines = f.readlines()
    data_lines = lines[4:]
    rows = []
    for line in data_lines:
        parts = line.split()
        if len(parts) == len(MIDL_COLS):
            rows.append([float(x) for x in parts])
    df = pd.DataFrame(rows, columns=MIDL_COLS)
    df['datetime'] = pd.to_timedelta(df['seconds'], unit='s') + EPOCH
    df['datetime'] = df['datetime'].dt.strftime('%Y-%m-%dT%H:%M:%S')
    df = df.drop(columns=['seconds'])
    return df


def export_midl_source(name, pattern, out_name):
    """Export a MIDL-based source (L1, STEREO-A, STEREO-B)."""
    files = sorted(glob.glob(os.path.join(DATA_DIR, pattern)))
    if not files:
        print(f"  {name}: no files found for {pattern}")
        return
    dfs = [read_midl(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    cols = ['datetime', 'lon', 'Br', 'Blat', 'Blon', 'Ur', 'Ulat', 'Ulon', 'n', 'T']
    df = df[cols].sort_values('datetime').drop_duplicates(subset='datetime')
    out_path = os.path.join(OUT_DIR, out_name)
    df.to_csv(out_path, index=False)
    print(f"  {name}: {len(df)} rows -> {out_path}")


def export_solo():
    """Export Solar Orbiter with original position from raw CSVs."""
    midl_files = sorted(glob.glob(os.path.join(DATA_DIR, 'SolarOrbiter', 'SolarOrbiter_*.dat.gz')))
    raw_files = sorted(glob.glob(os.path.join(DATA_DIR, 'SolarOrbiter', 'SolarOrbiter_*_raw.csv')))

    if not midl_files:
        print("  Solar Orbiter: no MIDL files found")
        return

    midl_dfs = [read_midl(f) for f in midl_files]
    midl = pd.concat(midl_dfs, ignore_index=True)

    raw_dfs = []
    for f in raw_files:
        df = pd.read_csv(f)
        df = df.rename(columns={'distance_au': 'orig_r_au', 'hgi_lon': 'orig_lon'})
        df['datetime'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%dT%H:%M:%S')
        raw_dfs.append(df[['datetime', 'orig_r_au', 'orig_lon']])
    if raw_dfs:
        raw = pd.concat(raw_dfs, ignore_index=True).drop_duplicates(subset='datetime')
        midl = midl.merge(raw, on='datetime', how='left')
    else:
        midl['orig_r_au'] = np.nan
        midl['orig_lon'] = np.nan

    cols = ['datetime', 'lon', 'orig_r_au', 'orig_lon',
            'Br', 'Blat', 'Blon', 'Ur', 'Ulat', 'Ulon', 'n', 'T']
    midl = midl[cols].sort_values('datetime').drop_duplicates(subset='datetime')
    out_path = os.path.join(OUT_DIR, 'solo.csv')
    midl.to_csv(out_path, index=False)
    print(f"  Solar Orbiter: {len(midl)} rows -> {out_path}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("Exporting website data...")
    export_midl_source('L1', 'L1/l1_*.dat.gz', 'l1.csv')
    export_midl_source('STEREO-A', 'STEREOA/STEREOA_*.dat.gz', 'stereoA.csv')
    export_midl_source('STEREO-B', 'STEREOB/STEREOB_*.dat.gz', 'stereoB.csv')
    export_solo()
    print("Done.")


if __name__ == '__main__':
    main()
