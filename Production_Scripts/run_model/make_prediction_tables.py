#!/usr/bin/env python3
"""Build persistence-forecast lookup tables anchored at the DATA FRONTIER.

This is the operational sibling of prediction_analysis/scripts/make_future_tables.py.
That one is calendar-locked: it tiles a whole year Y from the last rotation of
Y-1, end-anchored at Jan 1. run2_prediction instead forecasts forward from the
real data frontier (manifest LAST_POSSIBLE, e.g. 2026-03-28 23:00), which is NOT a
Jan-1 boundary and crosses a calendar year. So here the loop block is end-anchored
at an ARBITRARY frontier timestamp and tiled across an N-month horizon.

Method (persistence forecast):
  * block = last --block-hours ending AT the frontier (default 655 h ~= one
    Carrington rotation); interior gaps linearly interpolated, edges held;
  * the block is tiled forward so its LAST hour lands exactly on the frontier and
    repeats from there -> the forecast joins the real run seamlessly at the seam;
  * 8 physical columns get raised-cosine seam smoothing (--seam-smooth-hours);
  * SatPhi kept as its natural sawtooth (tile).

Sources: only those still reporting AT the frontier contribute (>= MIN_COVERAGE of
the block window is real data). At a 2026-03-28 frontier that is L1 only (Solar
Orbiter ends 2026-01-01, STEREO-A 2025-06-30), which is the honest persistence
state at that moment. Output per RUN year so RunAll_Prediction.pl finds them:
  <out-dir>/<src>/<src>_<Y>.dat.gz   for every year Y the horizon touches.
"""
import argparse
import datetime as dt
import gzip
import math
import os

SEC_PER_HR = 3600
EPOCH1965 = dt.datetime(1965, 1, 1)
DEFAULT_BLOCK_HOURS = 655
DEFAULT_SMOOTH_HOURS = 12
SMOOTH_COLS = range(2, 10)        # Br,Blat,Blon,Ur,Ulat,Ulon,n,T (not Seconds/SatPhi)
MIN_COVERAGE = 0.5                # source skipped if < this fraction of block is real
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))      # MSWIM2D (shared data/ lives here)


def sec1965(d):
    return (d - EPOCH1965).total_seconds()


def l1_dir(Y):
    """OMNI before the 2004-07 seam, MIDL after (matches the real run's L1 source)."""
    return "data/L1-old" if Y <= 2004 else "data/L1"


def source_config(frontier_year):
    """Sources to try, gated by the satellite windows at the frontier year. A
    source with no real data in the block window is dropped by the coverage check."""
    cfg = [("L1", l1_dir(frontier_year), "l1")]
    for name, sub, prefix in (("STEREOA", "data/STEREOA", "STEREOA"),
                              ("STEREOB", "data/STEREOB", "STEREOB"),
                              ("SolarOrbiter", "data/SolarOrbiter",
                               "SolarOrbiter")):
        # gate on lookup-table existence (the satellite refresh only writes
        # years with usable plasma), like the RunAll drivers — hard-coded
        # year caps here silently emptied the 2026+ prediction tables
        if any(os.path.exists(os.path.join(REPO, sub, f"{prefix}_{y}.dat.gz"))
               for y in (frontier_year, frontier_year - 1)):
            cfg.append((name, sub, prefix))
    return cfg


def read_rows(path):
    with gzip.open(path, "rt") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip() != ""]
    return lines[:4], lines[4:]       # (header[4 lines], data rows)


def recurrence_block(subdir, prefix, frontier_sec, block_hours):
    """Last block_hours ending AT frontier_sec (inclusive). Read from the frontier
    year and the year before (block may straddle Jan 1). Returns (head, merged, start)
    where merged maps hour-index 0..block_hours-1 -> split row; start is the time of
    index 0 and (start + block_hours*3600) == frontier_sec + 3600."""
    frontier_excl = frontier_sec + SEC_PER_HR          # one hour past the last data hour
    fyear = (EPOCH1965 + dt.timedelta(seconds=frontier_sec)).year
    head, allrows = None, {}
    for yy in (fyear - 1, fyear):
        p = os.path.join(REPO, subdir, f"{prefix}_{yy}.dat.gz")
        if not os.path.exists(p):
            continue
        h, rows = read_rows(p)
        head = head or h
        for r in rows:
            t = float(r.split()[0])
            if t < frontier_excl:
                allrows[t] = r.split()
    if not allrows:
        return None, {}, 0
    # Anchor at this source's own last data hour, not the global frontier: a
    # source whose data ends earlier (e.g. L1 ending 03-28 at an 04-30
    # STEREO-A frontier) still yields a valid persistence block, with its
    # real-time corotation phase preserved by the (t - start) % block tiling.
    anchor_excl = min(frontier_excl, max(allrows) + SEC_PER_HR)
    start = anchor_excl - block_hours * SEC_PER_HR
    merged = {int(round((t - start) / SEC_PER_HR)): row
              for t, row in allrows.items() if start <= t}
    return head, merged, start


def fill_block(merged, block_hours):
    """Gap-free list blk[0..block_hours-1]: present hours verbatim, interior gaps
    linearly interpolated per column, edge gaps held. Col 0 is a placeholder."""
    present = sorted(merged)
    sample = merged[present[0]]
    ncol = len(sample)
    decs = [len(sample[c].split(".")[1]) if "." in sample[c] else 0 for c in range(ncol)]
    blk = [None] * block_hours
    for i in range(block_hours):
        if i in merged:
            blk[i] = list(merged[i]); continue
        lo = max((p for p in present if p < i), default=None)
        hi = min((p for p in present if p > i), default=None)
        if lo is not None and hi is not None:
            w = (i - lo) / (hi - lo)
            row = list(merged[lo])
            for c in range(1, ncol):
                a, b = float(merged[lo][c]), float(merged[hi][c])
                row[c] = f"{a + (b - a) * w:.{decs[c]}f}"
            blk[i] = row
        else:
            blk[i] = list(merged[lo if lo is not None else hi])
    return blk


def fmt_like(val, token):
    dec = len(token.split(".")[1]) if "." in token else 0
    return f"{val:.{dec}f}"


def build_source(name, subdir, prefix, frontier_sec, year, block_hours,
                 smooth_hours, outdir):
    """Write <outdir>/<name>/<prefix>_<year>.dat.gz: the block tiled across year Y
    (Nov 1 (Y-1) .. Feb 1 (Y+1) pads), phase-locked so the block's last hour sits
    on the frontier. Returns True if written, False if the source has no coverage."""
    head, merged, start = recurrence_block(subdir, prefix, frontier_sec, block_hours)
    if head is None:
        return False
    cov = len(merged) / block_hours
    if len(merged) < 2 or (name != "L1" and cov < MIN_COVERAGE):
        print(f"  {name:13s}: only {len(merged)}/{block_hours} block rows "
              f"({cov*100:.0f}%) at frontier -> SKIP")
        return False
    blk = fill_block(merged, block_hours)
    gapnote = "" if len(merged) == block_hours else f" [{block_hours-len(merged)} gaps filled]"

    out_start = sec1965(dt.datetime(year - 1, 11, 1))   # Nov 1 (Y-1) left pad
    out_end = sec1965(dt.datetime(year + 1, 2, 1))       # Feb 1 (Y+1) right pad
    times, fields = [], []
    t = out_start
    while t <= out_end:
        idx = int(((t - start) // SEC_PER_HR) % block_hours)
        f = list(blk[idx]); f[0] = f"{float(t):.1f}"
        times.append(t); fields.append(f); t += SEC_PER_HR

    n_seams = 0
    if smooth_hours and smooth_hours > 0:
        half = smooth_hours // 2
        n = len(fields)
        nat = {c: [float(fields[i][c]) for i in range(n)] for c in SMOOTH_COLS}
        seams = [i for i in range(n)
                 if ((times[i] - start) // SEC_PER_HR) % block_hours == 0]
        for p in seams:
            lo, hi = p - half, p + half
            li, ri = max(lo, 0), min(hi, n - 1)
            if ri <= li:
                continue
            n_seams += 1
            for c in SMOOTH_COLS:
                L, R = nat[c][li], nat[c][ri]
                for q in range(max(lo, 0), min(hi, n)):
                    s = (q - lo) / smooth_hours
                    g = 0.5 * (1.0 - math.cos(math.pi * s))
                    fields[q][c] = fmt_like(L + (R - L) * g, fields[q][c])

    out_rows = [" ".join(f) for f in fields]
    src_lbl = ("OMNI" if subdir.endswith("L1-old") else
               "MIDL" if subdir.endswith("/L1") else name)
    anchor_excl = start + block_hours * SEC_PER_HR
    lag_h = (frontier_sec + SEC_PER_HR - anchor_excl) / SEC_PER_HR
    aiso = (EPOCH1965 + dt.timedelta(seconds=anchor_excl - SEC_PER_HR)).isoformat()
    desc = (f"{name} ({src_lbl}) PERSISTENCE prediction Y={year}: last {block_hours} h "
            f"ending {aiso} (~{block_hours/24:.2f} d) repeated, satphi=tile, "
            f"seam-smooth={smooth_hours}h, anchor-lag={lag_h:.0f}h behind frontier")
    os.makedirs(os.path.join(outdir, name), exist_ok=True)
    out = os.path.join(outdir, name, f"{prefix}_{year}.dat.gz")
    with gzip.open(out, "wt") as f:
        f.write("\n".join([desc, head[1], str(len(out_rows)), head[3]]) + "\n")
        f.write("\n".join(out_rows) + "\n")
    lagnote = f" anchor-lag={lag_h/24:.1f}d" if lag_h > SEC_PER_HR / 3600 else ""
    print(f"  {name:13s}: {src_lbl:4s} -> {out}  rows={len(out_rows)} seams={n_seams}{gapnote}{lagnote}")
    return True


def manifest_frontier():
    """Read LAST_POSSIBLE last_utc from data/DATA_MANIFEST.txt -> ISO string."""
    mpath = os.path.join(REPO, "data", "DATA_MANIFEST.txt")
    with open(mpath) as f:
        for ln in f:
            if ln.startswith("LAST_POSSIBLE"):
                return ln.split("|")[1].strip()
    raise SystemExit(f"no LAST_POSSIBLE record in {mpath}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frontier", default=None,
                    help="data-frontier ISO-8601 (default: manifest LAST_POSSIBLE)")
    ap.add_argument("--months", type=int, default=12,
                    help="forecast horizon in months (default 12)")
    ap.add_argument("--block-hours", type=int, default=DEFAULT_BLOCK_HOURS)
    ap.add_argument("--seam-smooth-hours", type=int, default=DEFAULT_SMOOTH_HOURS)
    ap.add_argument("--out-dir", required=True,
                    help="target data dir (e.g. data_prediction)")
    args = ap.parse_args()

    fiso = args.frontier or manifest_frontier()
    frontier = None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            frontier = dt.datetime.strptime(fiso, fmt); break
        except ValueError:
            continue
    if frontier is None:
        raise SystemExit(f"unparseable --frontier {fiso!r} (want ISO-8601)")
    frontier_sec = sec1965(frontier)

    # Forecast starts the month AFTER the frontier month and spans --months.
    fy, fm = frontier.year, frontier.month
    sm = fm + 1; sy = fy
    if sm > 12:
        sm = 1; sy += 1
    # last forecast month = start + (months-1)
    total = (sm - 1) + (args.months - 1)
    ey = sy + total // 12
    years = list(range(sy, ey + 1))

    outdir = os.path.join(REPO, args.out_dir)
    os.makedirs(outdir, exist_ok=True)
    print(f"prediction tables: frontier {fiso}  horizon {args.months} mo  "
          f"start {sy}-{sm:02d}  years {years}  block {args.block_hours} h  -> {outdir}")
    for year in years:
        for name, subdir, prefix in source_config(fy):
            build_source(name, subdir, prefix, frontier_sec, year,
                         args.block_hours, args.seam_smooth_hours, outdir)


if __name__ == "__main__":
    main()
