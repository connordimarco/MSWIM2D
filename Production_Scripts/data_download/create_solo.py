#!/usr/bin/env python3
"""
Download Solar Orbiter COHO merged hourly data and create intermediate
files (or lookup tables) for MSWIM2D.

Data source: NASA SPDF COHO merged hourly ASCII product.
https://spdf.gsfc.nasa.gov/pub/data/solar-orbiter/coho1hr_magplasma/ascii/

Usage:
    # Download and create intermediate CSVs (preserving radial distance):
    python3 Production_Scripts/create_solo.py --start 2020 --end 2025

    # With optional distance filtering:
    python3 Production_Scripts/create_solo.py --start 2022 --end 2025 --min-distance 0.5

    # Convert propagated data to MSWIM2D lookup table format:
    python3 Production_Scripts/create_solo.py --write-lookup-table --start 2022 --end 2025
"""
import os
import sys
import gzip
import shutil
import argparse
import datetime as dt
import urllib.request

import numpy as np

MSWIM2D_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..')
BASE_URL = 'https://spdf.gsfc.nasa.gov/pub/data/solar-orbiter/coho1hr_magplasma/ascii/'
SOLO_DIR = os.path.join(MSWIM2D_DIR, 'data', 'SolarOrbiter')
RAW_DIR = os.path.join(SOLO_DIR, 'raw')
EPOCH = dt.datetime(1965, 1, 1)

# COHO ASCII column indices (0-based). Format documented in aareadme_solo.
COL_YEAR = 0
COL_DOY  = 1
COL_HOUR = 2
COL_DIST = 3
COL_HGI_LAT = 4
COL_HGI_LON = 5
COL_BR   = 6
COL_BT   = 7
COL_BN   = 8
COL_BMAG = 9
COL_VR   = 10
COL_VT   = 11
COL_VN   = 12
COL_VTOT = 13
COL_VELEV = 14
COL_VAZIM = 15
COL_DENS = 16
COL_TEMP = 17

FILL = {
    COL_DIST:    999.99,
    COL_HGI_LAT: 9999.9,
    COL_HGI_LON: 9999.9,
    COL_BR:      99999.99,
    COL_BT:      99999.99,
    COL_BN:      99999.99,
    COL_BMAG:    99999.99,
    COL_VR:      99999.9,
    COL_VT:      99999.9,
    COL_VN:      99999.9,
    COL_VTOT:    99999.9,
    COL_VELEV:   99999.9,
    COL_VAZIM:   99999.9,
    COL_DENS:    99999.9,
    COL_TEMP:    99999999.0,
}

# Only the variables propagation genuinely cannot work without: heliocentric
# distance, HGI longitude, and bulk speed. B-components, density and temperature
# are NOT required -- a dropout in any of those used to discard the whole hour
# (the MIDL plasma-gap bug: B-only dropouts threw away real distance/speed/density).
# They are now kept as NaN and per-variable linearly interpolated in
# propagate_solo.py (limit_area='inside'), so one missing variable no longer
# blanks the hour. See AGENTS.md "SolO gap fix".
REQUIRED_COLS = [COL_DIST, COL_HGI_LON, COL_VTOT]


def is_fill(value, col):
    return abs(value - FILL[col]) < 0.01


def download_solo_file(year):
    filename = 'solo_merged_hr{}.txt'.format(year)
    url = BASE_URL + filename
    local_path = os.path.join(RAW_DIR, filename)

    if os.path.exists(local_path):
        print('  {} already downloaded, skipping.'.format(filename))
        return local_path

    print('  Downloading {}...'.format(url))
    try:
        urllib.request.urlretrieve(url, local_path)
        print('  Downloaded {}.'.format(filename))
    except Exception as e:
        print('  WARNING: could not download {}: {}'.format(filename, e))
        return None
    return local_path


def parse_solo_ascii(filepath):
    records = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) < 18:
                continue

            vals = [float(x) for x in parts]

            skip = False
            for col in REQUIRED_COLS:
                if is_fill(vals[col], col):
                    skip = True
                    break
            if skip:
                continue

            year = int(vals[COL_YEAR])
            doy  = int(vals[COL_DOY])
            hour = int(vals[COL_HOUR])
            timestamp = dt.datetime(year, 1, 1) + dt.timedelta(days=doy - 1, hours=hour)

            # Vr is used ONLY for the ballistic travel-time calc in
            # propagate_solo.py. When it's a fill value, fall back to the bulk
            # speed (VTOT, required so always present) rather than 0.0 -- 0.0
            # would make Vr>0 drop the row in propagation, needlessly losing an
            # otherwise-good hour. Output velocity is built from speed+angles,
            # not Vr, so this only affects timing.
            vr = vals[COL_VR] if not is_fill(vals[COL_VR], COL_VR) else vals[COL_VTOT]
            velev = vals[COL_VELEV] if not is_fill(vals[COL_VELEV], COL_VELEV) else 0.0
            vazim = vals[COL_VAZIM] if not is_fill(vals[COL_VAZIM], COL_VAZIM) else 0.0
            hgi_lat = vals[COL_HGI_LAT] if not is_fill(vals[COL_HGI_LAT], COL_HGI_LAT) else np.nan

            # B-components, density, temperature: keep as NaN if missing (not a
            # fill sentinel and not a whole-row drop) so per-variable interpolation
            # downstream can fill interior gaps while preserving the real vars.
            br   = vals[COL_BR]   if not is_fill(vals[COL_BR],   COL_BR)   else np.nan
            bt   = vals[COL_BT]   if not is_fill(vals[COL_BT],   COL_BT)   else np.nan
            bn   = vals[COL_BN]   if not is_fill(vals[COL_BN],   COL_BN)   else np.nan
            dens = vals[COL_DENS] if not is_fill(vals[COL_DENS], COL_DENS) else np.nan
            temp = vals[COL_TEMP] if not is_fill(vals[COL_TEMP], COL_TEMP) else np.nan

            records.append({
                'datetime':    timestamp,
                'distance_au': vals[COL_DIST],
                'hgi_lon':     vals[COL_HGI_LON],
                'hgi_lat':     hgi_lat,
                'Br':          br,
                'Bt':          bt,
                'Bn':          bn,
                'speed':       vals[COL_VTOT],
                'Vr':          vr,
                'elevation':   velev,
                'azimuth':     vazim,
                'density':     dens,
                'temperature': temp,
            })
    return records


def write_intermediate(records, year):
    outpath = os.path.join(SOLO_DIR, 'SolarOrbiter_{}_raw.csv'.format(year))
    with open(outpath, 'w') as f:
        f.write('datetime,seconds_since_1965,distance_au,hgi_lon,hgi_lat,'
                'Br,Bt,Bn,speed,Vr,elevation,azimuth,density,temperature\n')
        for r in records:
            seconds = (r['datetime'] - EPOCH).total_seconds()
            f.write('{},{:.1f},{:.4f},{:.2f},{:.2f},'
                    '{:.2f},{:.2f},{:.2f},{:.2f},{:.2f},{:.2f},{:.2f},'
                    '{:.2f},{:.0f}\n'.format(
                        r['datetime'].strftime('%Y-%m-%dT%H:%M:%S'),
                        seconds,
                        r['distance_au'],
                        r['hgi_lon'],
                        r['hgi_lat'] if not np.isnan(r['hgi_lat']) else 9999.9,
                        r['Br'], r['Bt'], r['Bn'],
                        r['speed'], r['Vr'],
                        r['elevation'], r['azimuth'],
                        r['density'], r['temperature']))
    print('  Wrote {} rows to {}'.format(len(records), os.path.basename(outpath)))
    return outpath


def _safe_float(s):
    """float() that maps empty / 'nan' cells (pandas writes NaN as '') to NaN."""
    s = s.strip()
    if s == '' or s.lower() == 'nan':
        return np.nan
    return float(s)


def parse_propagated_csv(filepath):
    records = []
    with open(filepath) as f:
        header = None
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if header is None:
                header = line.split(',')
                continue
            parts = line.split(',')
            row = {h: parts[i] for i, h in enumerate(header)}
            records.append({
                'datetime':    dt.datetime.strptime(row['datetime'], '%Y-%m-%dT%H:%M:%S'),
                'hgi_lon':     _safe_float(row['hgi_lon']),
                'Br':          _safe_float(row['Br']),
                'Bt':          _safe_float(row['Bt']),
                'Bn':          _safe_float(row['Bn']),
                'speed':       _safe_float(row['speed']),
                'elevation':   _safe_float(row['elevation']),
                'azimuth':     _safe_float(row['azimuth']),
                'density':     _safe_float(row['density']),
                'temperature': _safe_float(row['temperature']),
            })
    return records


def write_lookup_table(records, year):
    # Final guard: never emit a row carrying NaN in any physical output var (a
    # true outage, or a leading/trailing edge that interior-only interpolation
    # could not fill). BATSRUS must never read 'nan'. Filter FIRST so the nRows
    # header line matches the number of rows actually written.
    valid = [r for r in records
             if not np.isnan([r['hgi_lon'], r['Br'], r['Bt'], r['Bn'],
                              r['speed'], r['elevation'], r['azimuth'],
                              r['density'], r['temperature']]).any()]

    dat_path = os.path.join(SOLO_DIR, 'SolarOrbiter_{}.dat'.format(year))
    with open(dat_path, 'w') as f:
        f.write('Solar Orbiter hourly solar wind data for period beginning {}.\n'.format(year))
        f.write('0 0.0 1 0 9\n')
        f.write('{}\n'.format(len(valid)))
        f.write('Seconds SatPhi Br Blat Blon Ur Ulat Ulon n T')

        for r in valid:
            seconds = (r['datetime'] - EPOCH).total_seconds()
            lat = np.radians(r['elevation'])
            lon = np.radians(r['azimuth'])
            ur   = r['speed'] * np.cos(lat) * np.cos(lon)
            ulat = r['speed'] * np.cos(lat) * np.sin(lon)
            ulon = r['speed'] * np.cos(lon) * np.sin(lat)
            f.write('\n{} {:.2f} {:.2f} {:.2f} {:.2f} '
                    '{:.2f} {:.2f} {:.2f} '
                    '{:.2f} {:.2f}'.format(
                        seconds, r['hgi_lon'],
                        r['Br'], r['Bt'], r['Bn'],
                        ur, ulat, ulon,
                        r['density'], r['temperature']))
    f.close()

    gz_path = dat_path + '.gz'
    with open(dat_path, 'rb') as f_in:
        with gzip.open(gz_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(dat_path)
    print('  SolarOrbiter_{}.dat.gz created ({} of {} rows kept).'.format(
        year, len(valid), len(records)))


def process_year(year, args):
    raw_path = download_solo_file(year)
    if raw_path is None:
        return False

    print('  Parsing {}...'.format(os.path.basename(raw_path)))
    records = parse_solo_ascii(raw_path)
    print('  Parsed {} valid records.'.format(len(records)))

    if args.min_distance is not None:
        before = len(records)
        records = [r for r in records if r['distance_au'] >= args.min_distance]
        print('  Filtered: removed {} below {} AU, {} remain.'.format(
            before - len(records), args.min_distance, len(records)))

    if args.max_distance is not None:
        before = len(records)
        records = [r for r in records if r['distance_au'] <= args.max_distance]
        print('  Filtered: removed {} above {} AU, {} remain.'.format(
            before - len(records), args.max_distance, len(records)))

    if not records:
        print('  WARNING: no valid records for {}'.format(year))
        return False

    write_intermediate(records, year)
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Download Solar Orbiter COHO data and create '
                    'intermediate files for MSWIM2D.')
    parser.add_argument('--start', type=int, default=2020,
                        help='First year to process (default: 2020)')
    parser.add_argument('--end', type=int, default=2025,
                        help='Last year to process (default: 2025)')
    parser.add_argument('--min-distance', type=float, default=None,
                        help='Minimum heliocentric distance in AU (optional)')
    parser.add_argument('--max-distance', type=float, default=None,
                        help='Maximum heliocentric distance in AU (optional)')
    parser.add_argument('--write-lookup-table', action='store_true',
                        help='Convert propagated CSVs to lookup table format')
    parser.add_argument('--propagated-dir', type=str, default=None,
                        help='Directory with propagated CSVs '
                             '(default: data/SolarOrbiter/)')
    args = parser.parse_args()

    os.makedirs(SOLO_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    if args.write_lookup_table:
        prop_dir = args.propagated_dir or SOLO_DIR
        for y in range(args.start, args.end + 1):
            print('=== Writing lookup table for {} ==='.format(y))
            prop_path = os.path.join(
                prop_dir, 'SolarOrbiter_{}_propagated.csv'.format(y))
            if not os.path.exists(prop_path):
                print('  Propagated file not found: {}'.format(prop_path))
                continue
            records = parse_propagated_csv(prop_path)
            write_lookup_table(records, y)
            print()
    else:
        for y in range(args.start, args.end + 1):
            print('=== Year {} ==='.format(y))
            process_year(y, args)
            print()


if __name__ == '__main__':
    main()
