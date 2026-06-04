#!/usr/bin/env python3
"""Generate per-body INTERPOLATE.exe trajectory files over the model span.

Imports spice_traj.generate() (the production SPICE CLI) and writes each body's
hourly HGI/HCI position as a `#COORD/#START` traj.dat that INTERPOLATE.exe reads.
One file per body, clamped to (model span) INTERSECT (kernel coverage).

  python make_traj.py --kernel-root . --out trajectories \
         --model-start 1985-01-01 --model-end 2026-01-01

Model span default: 1985-01-01 .. 2026-01-01 (Tim's .outs start 198501; our run
ends 202512). Planets use de430 -> full span. Spacecraft windows come from
spice_traj.all_coverage() (read live from the loaded SPK), padded 1 day inward so
the date-truncated window can't trip generate()'s coverage guard.
"""
import os
import sys
import argparse
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spice_traj

# Dropdown roster -> output filename stem. Names must resolve in spice_traj.
BODIES = ['Earth', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto',
          'NewHorizons', 'Voyager1', 'Voyager2', 'Juno', 'Ulysses',
          'STEREO-A', 'STEREO-B', 'Cassini', 'Galileo', 'Pioneer10', 'Pioneer11']


def _date(s):
    return dt.datetime.strptime(s, '%Y-%m-%d')


def write_traj(path, pts):
    with open(path, 'w') as f:
        f.write('#COORD\nHGI\n\nyear mo dy hr mn sc msc x y z\n#START\n')
        for iso, x, y, _z in pts:
            t = dt.datetime.strptime(iso, '%Y-%m-%dT%H:%M:%S')
            f.write(' %d %d %d %d %d %d %d %.6f %.6f 0.0\n' % (
                t.year, t.month, t.day, t.hour, t.minute, t.second, 0, x, y))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kernel-root', required=True)
    ap.add_argument('--out', default='trajectories')
    ap.add_argument('--model-start', default='1985-01-01')
    ap.add_argument('--model-end', default='2026-01-01')
    ap.add_argument('--only', help='comma-separated subset of body names')
    a = ap.parse_args()

    m0, m1 = _date(a.model_start), _date(a.model_end)
    os.makedirs(a.out, exist_ok=True)

    cov = spice_traj.all_coverage(a.kernel_root)  # {name: [start,end] or None}
    roster = a.only.split(',') if a.only else BODIES

    for name in roster:
        win = cov.get(name)
        if win is None:                       # planet (de430): full model span
            s, e = m0, m1
        else:                                 # spacecraft: pad 1 day inward
            s = max(m0, _date(win[0]) + dt.timedelta(days=1))
            e = min(m1, _date(win[1]) - dt.timedelta(days=1))
        if e <= s:
            print('  SKIP %-12s (no overlap with model span)' % name)
            continue
        try:
            pts = spice_traj.generate(name, s, e, 'hour', a.kernel_root)
        except Exception as exc:
            print('  FAIL %-12s %s' % (name, exc))
            continue
        out = os.path.join(a.out, name + '.dat')
        write_traj(out, pts)
        print('  OK   %-12s %s .. %s  (%d pts)  -> %s' % (
            name, s.date(), e.date(), len(pts), out))


if __name__ == '__main__':
    main()
