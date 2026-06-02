#!/usr/bin/env python3
"""Refresh a STEREO SPK kernel (A=ahead or B=behind) for the website satellite
trajectories.

NAIF only ships STEREO-A_merged.bsp and it's frozen at 2018-12-31; there's no
STEREO-B kernel at NAIF at all. The live, complete source is the STEREO Science
Center (SSC), which publishes definitive ephemeris as SPK *transfer-format*
(.xsp) segments, one delivery every ~2 weeks. Each segment covers [some epoch ..
its delivery date], but the epoch resets irregularly, so we convert EVERY
delivery (tobin) and let spkmerge build the true maximal union; remaining gaps
are real tracking outages (e.g. STEREO-A's ~2-month 2020 gap).

  ahead  -> body -234, StereoUlysses/STEREO-A_merged.bsp, seeded from the frozen
            NAIF 2006-2018 file (kept as the priority base), segments fill forward.
  behind -> body -235, StereoUlysses/STEREO-B_merged.bsp, built purely from SSC
            segments (no NAIF base). STEREO-B lost contact 2014-10, so coverage
            ends there.

Run ON HEROT (needs internet to SSC + the NAIF toolkit utilities tobin/spkmerge,
auto-fetched into <work>/bin). Reusable from the future cron. Requires spiceypy.

  /usr/bin/python3 Scripts/refresh_stereo_kernel.py --sc behind \
      --kernel-root /homedata/MSWIM2D
"""
import os
import re
import sys
import argparse
import subprocess
import datetime as dt
import urllib.request

NAIF_UTIL = 'https://naif.jpl.nasa.gov/pub/naif/utilities/PC_Linux_64bit'
SSC_BASE = ('https://stereo-ssc.nascom.nasa.gov/data/moc_sds/%s/'
            'data_products/definitive_ephemerides')

# Per-spacecraft configuration.
SC = {
    'ahead':  dict(body=-234, prefix='ahead',  out='STEREO-A_merged.bsp',
                   start=dt.date(2018, 12, 1), use_base=True,
                   min_end='2024', max_start='2007'),
    'behind': dict(body=-235, prefix='behind', out='STEREO-B_merged.bsp',
                   start=dt.date(2006, 1, 1), use_base=False,
                   min_end='2014', max_start='2007'),
}


def _fetch(url, dest, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    with open(dest, 'wb') as f:
        f.write(data)
    return len(data)


def _seg_date(fname):
    p = fname.split('_')
    return dt.date(int(p[1]), 1, 1) + dt.timedelta(days=int(p[2]) - 1)


def list_segments(ssc, prefix):
    with urllib.request.urlopen(ssc + '/', timeout=60) as r:
        html = r.read().decode('latin-1')
    names = sorted(set(re.findall(prefix + r'_20\d\d_\d{3}_\d+\.depm\.xsp', html)))
    return sorted((_seg_date(n), n) for n in names)


def ensure_utils(bindir):
    os.makedirs(bindir, exist_ok=True)
    paths = []
    for u in ('tobin', 'spkmerge'):
        dest = os.path.join(bindir, u)
        if not os.path.exists(dest):
            _fetch('%s/%s' % (NAIF_UTIL, u), dest)
            os.chmod(dest, 0o755)
        paths.append(dest)
    return paths


def coverage(spice, bsp, body):
    cov = spice.cell_double(40000)
    spice.spkcov(bsp, body, cov)
    n = spice.wncard(cov)
    if n == 0:
        return None
    out = []
    for i in range(n):
        lo, hi = spice.wnfetd(cov, i)
        out.append((spice.et2utc(lo, 'ISOC', 0)[:10], spice.et2utc(hi, 'ISOC', 0)[:10]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sc', choices=('ahead', 'behind'), required=True)
    ap.add_argument('--kernel-root', required=True)
    ap.add_argument('--work', default=None)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    import spiceypy as spice
    cfg = SC[a.sc]
    kroot = a.kernel_root
    sk = os.path.join(kroot, 'SpiceKernels', 'StereoUlysses')
    lsk = os.path.join(kroot, 'SpiceKernels', 'Planets', 'naif0012.tls')
    out_path = os.path.join(sk, cfg['out'])
    work = a.work or os.path.join(kroot, '.st%s_build' % a.sc[0])
    ssc = SSC_BASE % a.sc

    spice.furnsh(lsk)
    base = None
    if cfg['use_base'] and os.path.exists(out_path):
        base = out_path
        print('base (existing) coverage:', coverage(spice, base, cfg['body']))

    segs = list_segments(ssc, cfg['prefix'])
    if not segs:
        print('ERROR: no SSC segments found at %s' % ssc, file=sys.stderr)
        sys.exit(1)
    picked = [(d, n) for d, n in segs if d >= cfg['start']]
    print('SSC %s definitive: %d total, %d delivered >= %s (%s .. %s)'
          % (a.sc, len(segs), len(picked), cfg['start'], picked[0][1], picked[-1][1]))
    if a.dry_run:
        return

    xspdir, bspdir = os.path.join(work, 'xsp'), os.path.join(work, 'bsp')
    os.makedirs(xspdir, exist_ok=True)
    os.makedirs(bspdir, exist_ok=True)
    tobin, spkmerge = ensure_utils(os.path.join(work, 'bin'))

    seg_bsps, skipped = [], []
    for d, name in picked:
        xsp = os.path.join(xspdir, name)
        bsp = os.path.join(bspdir, name.replace('.depm.xsp', '.bsp'))
        try:
            _fetch('%s/%s' % (ssc, name), xsp)
            if os.path.exists(bsp):
                os.remove(bsp)
            subprocess.run([tobin, xsp, bsp], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except Exception:
            skipped.append(name)
            continue  # malformed/duplicate delivery must not abort the build
        seg_bsps.append(bsp)
    print('converted %d segments -> .bsp (%d skipped)' % (len(seg_bsps), len(skipped)))
    if skipped:
        print('   skipped:', ', '.join(skipped))
    if not seg_bsps:
        print('ERROR: no segments converted', file=sys.stderr)
        sys.exit(1)

    new = os.path.join(work, cfg['out'].replace('.bsp', '.new.bsp'))
    if os.path.exists(new):
        os.remove(new)
    cfg_file = os.path.join(work, 'spkmerge.cfg')
    lines = ['LEAPSECONDS_KERNEL = %s' % lsk, 'SPK_KERNEL = %s' % new]
    sources = ([base] if base else []) + seg_bsps
    for src in sources:
        lines.append('  SOURCE_SPK_KERNEL = %s' % src)
        lines.append('    BODIES = %d' % cfg['body'])
    with open(cfg_file, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    subprocess.run([spkmerge], input=(cfg_file + '\n').encode(), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    spice.kclear()
    spice.furnsh(lsk)
    ivl = coverage(spice, new, cfg['body'])
    print('NEW %s coverage: %s' % (cfg['out'], None if ivl is None else (ivl[0][0], ivl[-1][1])))
    prev = None
    for lo, hi in (ivl or []):
        if prev and (dt.date(*map(int, lo.split('-'))) - dt.date(*map(int, prev.split('-')))).days > 1:
            print('   GAP: %s -> %s' % (prev, lo))
        print('   interval: %s -> %s' % (lo, hi))
        prev = hi
    if ivl is None or ivl[-1][1] < cfg['min_end'] or ivl[0][0] > cfg['max_start']:
        print('ERROR: coverage sanity check failed; leaving any existing file in place',
              file=sys.stderr)
        sys.exit(1)

    stamp = dt.datetime.utcnow().strftime('%Y%m%d')
    if os.path.exists(out_path):
        bak = '%s.bak.%s' % (out_path, stamp)
        if not os.path.exists(bak):
            os.replace(out_path, bak)
            print('backed up old -> %s' % os.path.basename(bak))
        else:
            os.remove(out_path)
    import shutil
    shutil.copy2(new, out_path)
    print('installed %s' % out_path)


if __name__ == '__main__':
    main()
