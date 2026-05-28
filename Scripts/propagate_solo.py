"""
Ballistic solar-wind propagation.

Assumes the solar wind travels radially at its measured speed (frozen-in
approximation).  Enforces causality by discarding parcels that are
overtaken by faster parcels emitted later.

Contains two public entry points:

- ballistic_propagation(): L1 data from GSE X to an inner boundary.
- solo_ballistic_propagation(): Solar Orbiter data from variable
  heliocentric distance to 1 AU.

The __main__ block propagates Solar Orbiter intermediate CSVs produced by
Scripts/create_solo.py and writes propagated CSVs ready for lookup-table
conversion.
"""
import argparse
import os

import numpy as np
import pandas as pd


VX_COL = 'Vx Velocity, km/s, GSE'
AU_KM = 1.496e8

MSWIM2D_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..')
SOLO_DIR = os.path.join(MSWIM2D_DIR, 'data', 'SolarOrbiter')

SOLO_OUTPUT_COLS = [
    'hgi_lon', 'hgi_lat', 'Br', 'Bt', 'Bn',
    'speed', 'elevation', 'azimuth', 'density', 'temperature',
]


def ballistic_propagation(orbit, raw_data, target_x_km=90000):
    """Propagate L1 observations to a target X position along the Sun-Earth line.

    Parameters
    ----------
    orbit : pd.Series
        Must contain 'X_GSE' (spacecraft X in km).
    raw_data : pd.DataFrame
        L1 time series with column 'Vx Velocity, km/s, GSE' and a
        DatetimeIndex.
    target_x_km : float
        Target boundary X position in km (positive Sunward, e.g.
        14*6371 ≈ 89 194 km for 14 Re).

    Returns
    -------
    pd.DataFrame
        Propagated data on a complete 1-minute grid matching the input
        time range.  Arrival times are computed exactly (no rounding),
        then interpolated onto the regular grid so no minutes are dropped.
        Variables with real data gaps remain NaN; only the timing
        calculation uses gap-filled Ux.
    """
    input_df = raw_data.copy()
    x_gse = np.float64(orbit['X_GSE'].item())
    target_x_km = np.float64(target_x_km)

    # Build a gap-free Ux for travel-time computation only.
    # This ensures B (and other vars) are not lost when Ux has gaps.
    vx_for_timing = input_df[VX_COL].interpolate(method='time')
    vx = np.asarray(vx_for_timing, dtype=np.float64)

    # Remember which input minutes had NaN Ux (on the original 1-min grid)
    # so we can restore those gaps in the output after propagation.
    vx_orig_nan = input_df[VX_COL].isna()

    # Ballistic travel time from spacecraft X to target X.
    travel_time_seconds = np.round((x_gse - target_x_km) / vx * (-1))
    travel_time = travel_time_seconds.astype('timedelta64[s]')

    # Compute arrival timestamps and enforce shock ordering.
    arrivals = input_df.index + travel_time
    valid_mask = pd.Series(True, index=input_df.index)

    for i in range(1, len(arrivals)):
        previous_indices = input_df.index[:i][arrivals[:i] > arrivals[i]]
        valid_mask.loc[previous_indices] = False

    # Drop older parcels overtaken by faster later parcels.
    input_df = input_df.loc[valid_mask]
    input_df.index = arrivals[valid_mask]

    # Resample onto a regular 1-minute grid.  Arrival times are irregular
    # (not aligned to whole minutes), so we merge them with the target grid
    # and interpolate to snap to grid points.  Limit=2 bridges only the
    # sub-minute jitter from time-shifting; real data gaps pass through as NaN.
    numeric_cols = input_df.select_dtypes(include='number').columns
    input_df = input_df[numeric_cols]
    input_df = input_df[input_df.index.notna()]
    input_df = input_df[~input_df.index.duplicated(keep='first')]
    input_df = input_df.sort_index()

    grid = pd.date_range(raw_data.index.min(), raw_data.index.max(), freq='min')
    combined = input_df.index.union(grid)
    result = input_df.reindex(combined).interpolate(
        method='index', limit=2).reindex(grid)

    # Restore NaN in Ux where it was originally missing.
    # The gap-filled Ux was only for timing, not for output.
    if VX_COL in result.columns:
        # Map original NaN mask onto the output grid (same freq, close alignment).
        vx_nan_on_grid = vx_orig_nan.reindex(grid, method='nearest', tolerance='30s')
        vx_nan_mask = vx_nan_on_grid.fillna(False).astype(bool)
        result.loc[vx_nan_mask, VX_COL] = np.nan

    return result


# ---------------------------------------------------------------------------
# Solar Orbiter propagation
# ---------------------------------------------------------------------------

def solo_ballistic_propagation(raw_data):
    """Propagate Solar Orbiter observations from variable distance to 1 AU.

    Parameters
    ----------
    raw_data : pd.DataFrame
        Hourly time series with DatetimeIndex.  Must contain columns
        'distance_au' and 'Vr' (positive outward) plus the science
        variables listed in SOLO_OUTPUT_COLS.

    Returns
    -------
    pd.DataFrame
        Propagated data on a regular hourly grid spanning the input
        time range.
    """
    df = raw_data.copy()

    df = df[df['Vr'] > 0]

    distance_km = (1.0 - df['distance_au']) * AU_KM
    travel_time_seconds = distance_km / df['Vr']
    travel_time = pd.to_timedelta(travel_time_seconds, unit='s')

    arrivals = df.index + travel_time
    valid_mask = pd.Series(True, index=df.index)

    for i in range(1, len(arrivals)):
        previous_indices = df.index[:i][arrivals[:i] > arrivals[i]]
        valid_mask.loc[previous_indices] = False

    df = df.loc[valid_mask]
    df.index = arrivals[valid_mask]

    df = df[SOLO_OUTPUT_COLS]
    df = df[df.index.notna()]
    df = df[~df.index.duplicated(keep='first')]
    df = df.sort_index()

    grid = pd.date_range(raw_data.index.min(), raw_data.index.max(), freq='h')
    combined = df.index.union(grid)
    result = df.reindex(combined).interpolate(
        method='index', limit=2).reindex(grid)

    return result


def _load_solo_raw_csv(filepath):
    return pd.read_csv(filepath, parse_dates=['datetime'], index_col='datetime')


def _write_solo_propagated_csv(df, filepath):
    out = df[SOLO_OUTPUT_COLS].dropna(how='all').copy()
    epoch = pd.Timestamp('1965-01-01')
    out.insert(0, 'seconds_since_1965',
               (out.index - epoch).total_seconds())
    out.insert(0, 'datetime',
               out.index.strftime('%Y-%m-%dT%H:%M:%S'))
    out.to_csv(filepath, index=False, float_format='%.2f')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Propagate Solar Orbiter data to 1 AU.')
    parser.add_argument('--start', type=int, required=True)
    parser.add_argument('--end', type=int, required=True)
    args = parser.parse_args()

    for year in range(args.start, args.end + 1):
        print('=== Year {} ==='.format(year))
        primary = os.path.join(
            SOLO_DIR, 'SolarOrbiter_{}_raw.csv'.format(year))
        if not os.path.exists(primary):
            print('  Raw file not found: {}'.format(primary))
            continue

        frames = [_load_solo_raw_csv(primary)]
        for adj_year in (year - 1, year + 1):
            adj_path = os.path.join(
                SOLO_DIR, 'SolarOrbiter_{}_raw.csv'.format(adj_year))
            if os.path.exists(adj_path):
                frames.append(_load_solo_raw_csv(adj_path))

        combined = pd.concat(frames).sort_index()
        combined = combined[~combined.index.duplicated(keep='first')]

        print('  Propagating {} rows...'.format(len(combined)))
        propagated = solo_ballistic_propagation(combined)

        year_start = pd.Timestamp('{}-01-01'.format(year))
        year_end = pd.Timestamp('{}-12-31 23:00:00'.format(year))
        propagated = propagated.loc[year_start:year_end]

        out_path = os.path.join(
            SOLO_DIR, 'SolarOrbiter_{}_propagated.csv'.format(year))
        _write_solo_propagated_csv(propagated, out_path)
        valid_rows = propagated[SOLO_OUTPUT_COLS].dropna(how='all').shape[0]
        print('  Wrote {} rows to {}'.format(
            valid_rows, os.path.basename(out_path)))
