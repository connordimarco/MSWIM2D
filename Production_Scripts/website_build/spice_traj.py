#!/usr/bin/env python3
"""SPICE trajectory CLI for the PHP interpolation endpoint (satellite mode).

interpolate.php shells out to this with a spiceypy-equipped Python:
  python3 spice_traj.py --sat "VOYAGER 1" --start 1980-01-01T00:00:00 \
          --end 1980-02-01T00:00:00 --cadence hour --kernel-root /homedata/MSWIM2D

Prints JSON on stdout: {"points": [[iso, x_AU, y_AU, z], ...]}  or  {"error": "..."}.
Positions are HGI/HCI ecliptic in AU with z forced to 0 (matches INTERPOLATE.exe
input and the old Scripts/SelectTrajectory.py). Standalone: only spiceypy is
imported (lazily), so a syntax/usage check works without it.
"""
import os
import sys
import json
import argparse
import datetime as dt

_METAKERNELS = ['planetsKern.txt', 'cassiniKern.txt', 'voyagerKern.txt',
                'stereoulyssesKern.txt', 'newhorizonsKern.txt', 'junoKern.txt',
                'pioneerKern.txt', 'galileoKern.txt']

_PLANETS = {'EARTH', 'MARS', 'JUPITER', 'SATURN', 'URANUS', 'NEPTUNE', 'PLUTO'}

_SC_TARGET = {
    'NEWHORIZONS': 'NEW HORIZONS', 'NEWHORIZON': 'NEW HORIZONS',
    'VOYAGER1': 'VOYAGER 1', 'VOYAGER2': 'VOYAGER 2',
    'JUNO': 'JUNO', 'ULYSSES': 'ULYSSES', 'CASSINI': 'CASSINI',
    'STEREOA': 'STEREO AHEAD', 'STEREO-A': 'STEREO AHEAD', 'STEREO': 'STEREO AHEAD',
    'STEREOB': 'STEREO BEHIND', 'STEREO-B': 'STEREO BEHIND',
    'PIONEER10': 'PIONEER 10', 'PIONEER-10': 'PIONEER 10',
    'PIONEER11': 'PIONEER 11', 'PIONEER-11': 'PIONEER 11',
    'GALILEO': 'GALILEO ORBITER', 'GALILEOORBITER': 'GALILEO ORBITER',
}

# Fallback validity windows ONLY. The real window is read at runtime from the
# loaded SPK kernels via spkcov() (see _kernel_window), so it always tracks the
# deployed .bsp files. These hard-coded values are used solely when coverage
# can't be determined (e.g. an old spiceypy without cell_double).
_SC_RANGE = {
    'CASSINI': (dt.datetime(1997, 10, 15), dt.datetime(2017, 9, 15)),
    'VOYAGER 1': (dt.datetime(1977, 9, 6), dt.datetime(2030, 12, 31)),
    'VOYAGER 2': (dt.datetime(1977, 8, 21), dt.datetime(2030, 12, 31)),
    'JUNO': (dt.datetime(2011, 8, 6), dt.datetime(2020, 8, 22)),
    'ULYSSES': (dt.datetime(1990, 10, 7), dt.datetime(2009, 6, 30)),
    'STEREO AHEAD': (dt.datetime(2006, 10, 26), dt.datetime(2050, 1, 1)),
    'STEREO BEHIND': (dt.datetime(2006, 10, 26), dt.datetime(2014, 12, 31)),
    'NEW HORIZONS': (dt.datetime(2006, 1, 19), dt.datetime(2030, 12, 31)),
    'PIONEER 10': (dt.datetime(1972, 3, 3), dt.datetime(2003, 1, 23)),
    'PIONEER 11': (dt.datetime(1973, 4, 6), dt.datetime(1995, 9, 30)),
    'GALILEO ORBITER': (dt.datetime(1989, 10, 18), dt.datetime(2003, 9, 21)),
}

_CADENCE = {'hour': 3600, 'day': 86400, 'minute': 60}


class TrajError(Exception):
    pass


def _resolve_target(sat_name):
    key = sat_name.upper().replace(' ', '').replace('_', '')
    plain = sat_name.upper().strip()
    if plain in _PLANETS:
        return plain + ' BARYCENTER', None
    if plain == 'EARTH':
        return 'EARTH BARYCENTER', None
    if key in _SC_TARGET:
        tgt = _SC_TARGET[key]
        return tgt, _SC_RANGE.get(tgt)
    raise TrajError('unknown satellite %r' % sat_name)


def _parse(s):
    return dt.datetime.strptime(s.replace('Z', ''), '%Y-%m-%dT%H:%M:%S')


def _et_cal(spice, et):
    """ET seconds -> 'YYYY-MM-DD' for user-facing coverage messages."""
    try:
        return spice.et2utc(et, 'ISOC', 0)[:10]
    except Exception:
        return '?'


def _kernel_window(spice, target):
    """Actual [start, end] ET coverage of `target` across all loaded SPK kernels,
    or None if it can't be determined. This lets the valid window track whatever
    .bsp files are currently deployed instead of a hard-coded guess, so a kernel
    refresh needs no code change."""
    try:
        body_id = spice.bodn2c(target)
    except Exception:
        return None
    try:
        cover = spice.cell_double(20000)
    except Exception:
        try:
            cover = spice.stypes.SPICEDOUBLE_CELL(20000)
        except Exception:
            return None
    try:
        nspk = spice.ktotal('SPK')
    except Exception:
        return None
    found = False
    for i in range(nspk):
        try:
            spk = spice.kdata(i, 'SPK')[0]
            spice.spkcov(spk, body_id, cover)  # accumulates this body's intervals
            found = True
        except Exception:
            pass  # body absent from this file, or file unreadable -> skip
    if not found:
        return None
    try:
        card = spice.wncard(cover)
        if card == 0:
            return None
        lo = spice.wnfetd(cover, 0)[0]            # earliest interval start
        hi = spice.wnfetd(cover, card - 1)[1]     # latest interval end
        return (lo, hi)
    except Exception:
        return None


def generate(sat_name, start, end, cadence, kernel_root):
    import spiceypy as spice  # lazy; only needed here

    step = _CADENCE.get(cadence.lower())
    if step is None:
        raise TrajError('cadence must be hour, day, or minute')
    if end <= start:
        raise TrajError('end must be after start')

    target, fallback_window = _resolve_target(sat_name)

    cwd = os.getcwd()
    try:
        os.chdir(kernel_root)  # metakernels use relative ./SpiceKernels/... paths
        for mk in _METAKERNELS:
            p = os.path.join('SpiceKernels', mk)
            if os.path.exists(p):
                try:
                    spice.furnsh(p)
                except Exception:
                    pass  # a missing spacecraft kernel must not break planet queries

        times, ets = [], []
        t = start
        delta = dt.timedelta(seconds=step)
        while t <= end:
            times.append(t)
            ets.append(spice.str2et(t.strftime('%Y-%m-%dT%H:%M:%S')))
            t += delta

        # Validate the request against the ACTUAL coverage of the loaded kernels,
        # so the window reflects the deployed .bsp files. Fall back to the
        # hard-coded _SC_RANGE only if coverage can't be read.
        win = _kernel_window(spice, target)
        if win is not None:
            lo, hi = win
            if ets[0] < lo or ets[-1] > hi:
                raise TrajError('%s ephemeris only covers %s to %s' % (
                    sat_name, _et_cal(spice, lo), _et_cal(spice, hi)))
        elif fallback_window is not None and (start < fallback_window[0] or end > fallback_window[1]):
            raise TrajError('%s ephemeris only covers %s to %s' % (
                sat_name, fallback_window[0].strftime('%Y/%m/%d'),
                fallback_window[1].strftime('%Y/%m/%d')))

        positions, _ = spice.spkpos(target, ets, 'HCI', 'NONE', 'SOLAR SYSTEM BARYCENTER')
        pts = []
        for when, pos in zip(times, positions):
            x = spice.convrt(float(pos[0]), 'KM', 'AU')
            y = spice.convrt(float(pos[1]), 'KM', 'AU')
            pts.append([when.strftime('%Y-%m-%dT%H:%M:%S'), x, y, 0.0])
        return pts
    finally:
        try:
            spice.kclear()
        except Exception:
            pass
        os.chdir(cwd)


# Canonical dropdown entries (must match interpolate.html). Planets report a
# null window (de430 covers any date); spacecraft report their kernel coverage.
_COVERAGE_BODIES = ['Earth', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune',
                    'Pluto', 'NewHorizons', 'Voyager1', 'Voyager2', 'Juno',
                    'Ulysses', 'STEREO-A', 'STEREO-B', 'Cassini', 'Galileo',
                    'Pioneer10', 'Pioneer11']


def all_coverage(kernel_root):
    """{dropdown_name: [start_iso, end_iso] or None} read from the loaded kernels.
    Planets -> None (de430 covers any date). Drives the website dropdown's
    per-satellite time-range labels, so they track the deployed kernels."""
    import spiceypy as spice
    out = {}
    cwd = os.getcwd()
    try:
        os.chdir(kernel_root)
        for mk in _METAKERNELS:
            p = os.path.join('SpiceKernels', mk)
            if os.path.exists(p):
                try:
                    spice.furnsh(p)
                except Exception:
                    pass
        for name in _COVERAGE_BODIES:
            if name.upper() in _PLANETS:
                out[name] = None  # de430: any date
                continue
            try:
                target, _ = _resolve_target(name)
            except TrajError:
                out[name] = None
                continue
            win = _kernel_window(spice, target)
            out[name] = None if win is None else [
                _et_cal(spice, win[0]), _et_cal(spice, win[1])]
    finally:
        try:
            spice.kclear()
        except Exception:
            pass
        os.chdir(cwd)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sat')
    ap.add_argument('--start')
    ap.add_argument('--end')
    ap.add_argument('--cadence', default='hour')
    ap.add_argument('--kernel-root', required=True)
    ap.add_argument('--coverage', action='store_true',
                    help="dump JSON of each dropdown body's coverage window and exit")
    a = ap.parse_args()
    if a.coverage:
        try:
            print(json.dumps({'coverage': all_coverage(a.kernel_root)}))
        except Exception as e:
            print(json.dumps({'error': 'SPICE error: %s' % e}))
        return
    if not (a.sat and a.start and a.end):
        print(json.dumps({'error': 'need --sat, --start, --end (or --coverage)'}))
        return
    try:
        pts = generate(a.sat, _parse(a.start), _parse(a.end), a.cadence, a.kernel_root)
        print(json.dumps({'points': pts}))
    except TrajError as e:
        print(json.dumps({'error': str(e)}))
    except Exception as e:  # surface SPICE/import errors as a clean JSON error
        print(json.dumps({'error': 'SPICE error: %s' % e}))


if __name__ == '__main__':
    main()
