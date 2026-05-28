#!/usr/bin/env python3
"""
Fetch Earth heliographic longitude from CDAWeb COHO ephemeris files.
Saves yearly CSV files to data/earth_ephemeris/ for use by create_midl_l1.py.

Requires python3.8 with spacepy (pycdf).

Usage:
    python3.8 Scripts/fetch_earth_ephemeris.py --start 1997 --end 2026
"""

import os
import sys
import argparse
import datetime as dt
import urllib.request
from dateutil import rrule
from spacepy import pycdf

MSWIM2D_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..')
EPHEM_DIR = os.path.join(MSWIM2D_DIR, 'data', 'earth_ephemeris')
DATA_URL = 'https://cdaweb.gsfc.nasa.gov/pub/data/omni/omni_cdaweb/coho1hr_magplasma/'


def fetch_year(year):
    """Download COHO Earth ephemeris for one year, save as CSV."""
    start = dt.datetime(year, 1, 1)
    end = dt.datetime(year, 12, 31)

    csv_path = os.path.join(EPHEM_DIR, f'earth_hgi_{year}.csv')

    print(f'  Fetching {year}...')
    epochs = []
    lons = []

    for date in rrule.rrule(rrule.MONTHLY, dtstart=start, until=end):
        filename = f'{date.year}/omni_coho1hr_merged_mag_plasma_{date.year}{date.month:02d}01_v01.cdf'
        local_cdf = os.path.join(MSWIM2D_DIR, 'earth_ephemeris.cdf')
        try:
            urllib.request.urlretrieve(DATA_URL + filename, local_cdf)
        except Exception as e:
            print(f'    WARNING: could not download {filename}: {e}')
            continue
        try:
            cdf = pycdf.CDF(local_cdf)
            for i, epoch in enumerate(cdf['Epoch']):
                epochs.append(epoch)
                lons.append(cdf['heliographicLongitude'][i])
            cdf.close()
        except Exception as e:
            print(f'    WARNING: could not read {filename}: {e}')
        finally:
            if os.path.exists(local_cdf):
                os.remove(local_cdf)

    with open(csv_path, 'w') as f:
        f.write('# time,heliographicLongitude\n')
        for i, epoch in enumerate(epochs):
            t = epoch if isinstance(epoch, dt.datetime) else dt.datetime(*epoch.timetuple()[:6])
            f.write(f'{t.isoformat()},{lons[i]:.3f}\n')

    print(f'  earth_hgi_{year}.csv: {len(epochs)} records.')


def main():
    parser = argparse.ArgumentParser(description='Fetch Earth HGI ephemeris from CDAWeb')
    parser.add_argument('--start', type=int, default=1997)
    parser.add_argument('--end', type=int, default=2026)
    args = parser.parse_args()

    os.makedirs(EPHEM_DIR, exist_ok=True)

    for year in range(args.start, args.end + 1):
        csv_path = os.path.join(EPHEM_DIR, f'earth_hgi_{year}.csv')
        if os.path.exists(csv_path):
            print(f'  earth_hgi_{year}.csv already exists, skipping.')
            continue
        try:
            fetch_year(year)
        except Exception as e:
            print(f'  ERROR on {year}: {e}')


if __name__ == '__main__':
    main()
