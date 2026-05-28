#!/usr/bin/env python3.12
"""
Download L1 solar wind data from MIDL and create lookup tables for MSWIM2D.

Two-step process:
  1. Run fetch_earth_ephemeris.py (python3.8) to get Earth HGI longitudes
  2. Run this script (python3.12) to download MIDL data and write lookup tables

Usage:
    python3.12 Scripts/create_midl_l1.py --start 1998 --end 2025
"""

import os
import sys
import gzip
import shutil
import argparse
import datetime as dt
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '.pylibs'))
import midl

MSWIM2D_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..')
L1_DIR = os.path.join(MSWIM2D_DIR, 'data', 'L1')
EPHEM_DIR = os.path.join(MSWIM2D_DIR, 'data', 'earth_ephemeris')
EPOCH = dt.datetime(1965, 1, 1)
V_EARTH = 30  # km/s, Earth's orbital velocity


def load_ephemeris(start_date, end_date):
    """Load Earth HGI longitude from pre-fetched ephemeris CSV files."""
    ephem_times = []
    ephem_lon = []

    year = start_date.year
    while year <= end_date.year:
        csv_path = os.path.join(EPHEM_DIR, f'earth_hgi_{year}.csv')
        if not os.path.exists(csv_path):
            print(f'  WARNING: ephemeris file {csv_path} not found, skipping year {year}')
            year += 1
            continue
        with open(csv_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(',')
                t = dt.datetime.fromisoformat(parts[0])
                lon = float(parts[1])
                if start_date <= t <= end_date:
                    ephem_times.append(t)
                    ephem_lon.append(lon)
        year += 1

    return ephem_times, ephem_lon


def get_earth_phi(timestamp, ephem_times, ephem_lon):
    """Look up Earth HGI longitude for a given timestamp (nearest hour)."""
    rounded = timestamp.replace(minute=0, second=0, microsecond=0)
    for i, t in enumerate(ephem_times):
        if t == rounded:
            return ephem_lon[i]
    # Linear interpolation fallback
    for i in range(len(ephem_times) - 1):
        if ephem_times[i] <= rounded <= ephem_times[i + 1]:
            dt_total = (ephem_times[i + 1] - ephem_times[i]).total_seconds()
            dt_frac = (rounded - ephem_times[i]).total_seconds()
            return ephem_lon[i] + (ephem_lon[i + 1] - ephem_lon[i]) * dt_frac / dt_total
    return np.nan


def create_lookup_table(year):
    """Download MIDL L1 data and write an MSWIM2D lookup table for one year."""
    if year == 1998:
        start_date = dt.datetime(1998, 2, 1)
    else:
        start_date = dt.datetime(year - 1, 11, 1)
    end_date = dt.datetime(year + 1, 1, 31)

    print(f'  Loading Earth ephemeris for {start_date.date()} to {end_date.date()}...')
    ephem_times, ephem_lon = load_ephemeris(start_date, end_date)
    if not ephem_times:
        print(f'  ERROR: no ephemeris data found. Run fetch_earth_ephemeris.py first.')
        return False

    print(f'  Downloading MIDL L1 data...')
    os.environ.setdefault('XDG_CACHE_HOME', os.path.join(
        os.path.dirname(MSWIM2D_DIR), '.cache'))
    ds = midl.load(start_date.strftime('%Y-%m-%d'),
                   end_date.strftime('%Y-%m-%d'), 'l1')
    df = ds.to_dataframe()

    # Hourly averaging
    print(f'  Hourly averaging {len(df)} 1-min records...')
    hourly = df[['Bx', 'By', 'Bz', 'Ux', 'Uy', 'Uz', 'rho', 'T']].resample('1h').mean()
    hourly = hourly.dropna(subset=['Bx', 'Ux', 'rho', 'T'])
    print(f'  {len(hourly)} valid hourly records.')

    if len(hourly) == 0:
        print(f'  WARNING: no valid data for year {year}')
        return False

    # Convert GSM -> HGI (same approximate convention as create_imf.py)
    rows = []
    for timestamp, row in hourly.iterrows():
        ts = timestamp.to_pydatetime()
        phi = get_earth_phi(ts, ephem_times, ephem_lon)
        if np.isnan(phi):
            continue

        seconds = (ts - EPOCH).total_seconds()
        br = -row['Bx']
        blat = -row['By']
        blon = row['Bz']
        ur = -row['Ux']
        ulat = -row['Uy'] + V_EARTH
        ulon = row['Uz']
        n = row['rho']
        temp = row['T']

        if np.isnan([br, blat, blon, ur, ulat, ulon, n, temp]).any():
            continue

        rows.append((seconds, phi, br, blat, blon, ur, ulat, ulon, n, temp))

    print(f'  Writing {len(rows)} rows to lookup table...')

    dat_path = os.path.join(L1_DIR, f'l1_{year}.dat')
    with open(dat_path, 'w') as f:
        f.write(f'MIDL hourly solar wind data for period beginning '
                f'{start_date.year}{start_date.month:2d}{start_date.day:2d}.\n')
        f.write('0 0.0 1 0 9\n')
        f.write(f'{len(rows)}\n')
        f.write('Seconds SatPhi Br Blat Blon Ur Ulat Ulon n T')
        for r in rows:
            f.write(f'\n{r[0]} {r[1]:.3f} {r[2]:.3f} {r[3]:.3f} {r[4]:.3f} '
                    f'{r[5]:.3f} {r[6]:.3f} {r[7]:.3f} {r[8]:.3f} {r[9]:.3f}')

    with open(dat_path, 'rb') as f_in:
        with gzip.open(dat_path + '.gz', 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(dat_path)

    print(f'  l1_{year}.dat.gz created.')
    return True


def main():
    parser = argparse.ArgumentParser(description='Create MSWIM2D L1 lookup tables from MIDL')
    parser.add_argument('--start', type=int, default=1998)
    parser.add_argument('--end', type=int, default=2025)
    args = parser.parse_args()

    os.makedirs(L1_DIR, exist_ok=True)

    for y in range(args.start, args.end + 1):
        print(f'=== Year {y} ===')
        create_lookup_table(y)
        print()


if __name__ == '__main__':
    main()
