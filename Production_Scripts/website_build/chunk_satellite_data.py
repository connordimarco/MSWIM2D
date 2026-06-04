#!/usr/bin/env python3
"""Chunk the in-situ satellite input CSVs into per-year files for the browser.

Reads the monolithic hourly CSVs in MSWIM2D_Data_New/Satellite_Data/
(heliographic SPHERICAL components) and writes, per source, one CSV per year
into Satellite_Data/chunks/<id>/<YYYY>.csv plus a chunks/manifest.json index.

The spherical -> Cartesian HGI conversion is done HERE (once) so the browser
never has to: the chunk files already carry Bx,By,Bz,Ux,Uy,Uz so they line up
directly with the MSWIM2D model output columns.

Conversion (heliographic latitude assumed ~0, consistent with the 2D ecliptic
model) at position longitude lambda:
    r_hat   = ( cos l,  sin l, 0)
    lon_hat = (-sin l,  cos l, 0)
    lat_hat = ( 0,      0,     1)
=>  Bx = Br cos l - Blon sin l ;  By = Br sin l + Blon cos l ;  Bz = Blat
    (same for U).  rho = n.

Position written for the middle-column dots: r=1 AU for L1/STEREO (assumed),
and for Solar Orbiter the propagated point (r=1, lon) plus the original
(orig_r, orig_lon) so the page can draw the propagation line.

Run with system Python 3.6+: `python3 chunk_satellite_data.py`
"""

import csv
import json
import math
import os
from collections import OrderedDict

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE, 'MSWIM2D_Data_New', 'Satellite_Data')
OUT_DIR = os.path.join(SRC_DIR, 'chunks')

DEG = math.pi / 180.0

# id, source file, display label, plot color, propagated-to-1AU flag
SOURCES = [
    {'id': 'l1',      'file': 'l1.csv',      'label': 'L1 (MIDL)',     'color': '#ff7f0e', 'propagated': False},
    {'id': 'stereoA', 'file': 'stereoA.csv', 'label': 'STEREO-A',      'color': '#2ca02c', 'propagated': False},
    {'id': 'stereoB', 'file': 'stereoB.csv', 'label': 'STEREO-B',      'color': '#9467bd', 'propagated': False},
    {'id': 'solo',    'file': 'solo.csv',    'label': 'Solar Orbiter', 'color': '#e377c2', 'propagated': True},
]

HEADER = ['datetime', 'r', 'lon', 'Bx', 'By', 'Bz', 'Ux', 'Uy', 'Uz', 'rho', 'T']
HEADER_SOLO = HEADER + ['orig_r', 'orig_lon']


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return float('nan')


def fmt(x):
    if x != x:          # NaN
        return ''
    return '%g' % round(x, 4)


def convert_row(row, propagated):
    lon = fnum(row.get('lon'))
    Br, Blat, Blon = fnum(row.get('Br')), fnum(row.get('Blat')), fnum(row.get('Blon'))
    Ur, Ulat, Ulon = fnum(row.get('Ur')), fnum(row.get('Ulat')), fnum(row.get('Ulon'))
    n, T = fnum(row.get('n')), fnum(row.get('T'))

    c, s = math.cos(lon * DEG), math.sin(lon * DEG)
    Bx = Br * c - Blon * s
    By = Br * s + Blon * c
    Ux = Ur * c - Ulon * s
    Uy = Ur * s + Ulon * c

    out = [row['datetime'], '1', fmt(lon),
           fmt(Bx), fmt(By), fmt(Blat),
           fmt(Ux), fmt(Uy), fmt(Ulat),
           fmt(n), fmt(T)]
    if propagated:
        out += [fmt(fnum(row.get('orig_r_au'))), fmt(fnum(row.get('orig_lon')))]
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description='Chunk in-situ satellite CSVs into per-year files for the website.')
    ap.add_argument('--data-new', default=os.path.join(BASE, 'MSWIM2D_Data_New'),
                    help='MSWIM2D_Data_New dir holding Satellite_Data/ (default: beside this script)')
    a = ap.parse_args()
    global SRC_DIR, OUT_DIR
    SRC_DIR = os.path.join(a.data_new, 'Satellite_Data')
    OUT_DIR = os.path.join(SRC_DIR, 'chunks')

    manifest = {'sources': []}

    for src in SOURCES:
        path = os.path.join(SRC_DIR, src['file'])
        if not os.path.exists(path):
            print('skip %s (not found)' % src['file'])
            continue

        by_year = OrderedDict()
        with open(path, 'r') as f:
            for row in csv.DictReader(f):
                dt = row.get('datetime', '')
                if len(dt) < 4:
                    continue
                by_year.setdefault(dt[:4], []).append(convert_row(row, src['propagated']))

        sdir = os.path.join(OUT_DIR, src['id'])
        os.makedirs(sdir, exist_ok=True)
        header = HEADER_SOLO if src['propagated'] else HEADER
        years = sorted(int(y) for y in by_year)

        for y in years:
            with open(os.path.join(sdir, '%d.csv' % y), 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(by_year['%04d' % y])

        manifest['sources'].append({
            'id': src['id'], 'label': src['label'], 'color': src['color'],
            'propagated': src['propagated'], 'years': years,
        })
        print('%-8s -> %d years (%d..%d)' % (src['id'], len(years), years[0], years[-1]))

    with open(os.path.join(OUT_DIR, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
    print('wrote %s' % os.path.join(OUT_DIR, 'manifest.json'))


if __name__ == '__main__':
    main()
