#!/bin/bash
# ============================================================================
# update_satellite_data.sh 
#
# Refresh every MSWIM2D satellite input lookup table (.dat.gz that BATSRUS
# reads) to current, then write a human-readable manifest:
#       data/DATA_MANIFEST.txt
# whose headline is the single date the model can be run through RELIABLY
# (= the earliest last-data day among the active assimilated spacecraft).
#
#   Scripts/update_satellite_data.sh [--since-year YYYY] [--force]
#
#   --since-year  first year to check/refresh (default 2025)
#   --force       rebuild every year in range, skipping the gap check
#
# Called automatically by build_website_data.sh unless SKIP_SATELLITE_DOWNLOAD=1.
#
# Per source, for each year in [SINCE_YEAR .. current], it asks
# satellite_data_status.py whether that year-file already covers its window.
# Gap-driven: it re-pulls + rebuilds only if the file is MISSING, has missing
# hours up to (today - LAG_DAYS), or contains non-finite values. The LAG_DAYS
# cushion keeps it from flapping on the last few days the sources have not
# posted yet (SPDF/MIDL lag real time by weeks-months).
#
# Sources / toolchains:
#   Solar Orbiter  py3.8  create_solo -> propagate_solo -> create_solo --write-lookup-table
#   L1 (OMNI/MIDL) py3.12 create_midl_l1 (needs Earth ephemeris CSVs, fetched py3.8)
#   STEREO-A       py3.8  download_stereoA_update (re-pulls monthly CDFs fresh)
#   STEREO-B       retired 2014 -- skipped, reported in the manifest only.
# ----------------------------------------------------------------------------
# NOTE: deliberately NOT `set -e`. One source failing (e.g. a server hiccup)
# must not abort refreshing the others or writing the manifest.
set -uo pipefail

ROOT="${MSWIM2D_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# Directory holding THIS script (and its sibling builders) — the pipeline lives in
# Production_Scripts now, so call siblings by $HERE rather than a hard-coded Scripts/.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY38="${PY38:-/opt/anaconda3/bin/python3.8}" # SolO + STEREO (anaconda; absolute so cron's minimal PATH can't pick the numpy-less /usr/bin/python3.8)
PY312="${PY312:-/usr/bin/python3.12}" # L1 / midl
STATUS="$HERE/satellite_data_status.py"

# Match create_midl_l1.py's default XDG_CACHE_HOME so we clear the right cache.
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$(dirname "$ROOT")/.cache}"
MIDL_CACHE="$XDG_CACHE_HOME/midl"

LAG_DAYS=3
SINCE_YEAR=2025
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --since-year) SINCE_YEAR="$2"; shift 2;;
    --force)      FORCE=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

CUR_YEAR=$(date +%Y)
TODAY=$(date +%Y-%m-%d)
SETTLED_END=$(date -d "$TODAY -$LAG_DAYS days" +%Y-%m-%dT%H:%M:%S)

# needs_rebuild <dat.gz> <year>  -> prints "1" (rebuild) or "0" (leave it),
# and a human reason on stderr.
needs_rebuild() {
  local f="$1" year="$2"
  if [ "$FORCE" = "1" ]; then echo 1; return; fi
  local wstart="${year}-01-01T00:00:00"
  local wend="${year}-12-31T23:00:00"
  # window end = min(settled_end, year end)
  if [[ "$SETTLED_END" < "$wend" ]]; then wend="$SETTLED_END"; fi
  # future year whose window hasn't started -> nothing to do
  if [[ "$wend" < "$wstart" ]]; then echo 0; return; fi
  local reason rc
  reason=$("$PY38" "$STATUS" check "$f" --start "$wstart" --end "$wend" 2>&1); rc=$?
  echo "      [$year] $reason" >&2
  if [ "$rc" = "0" ]; then echo 0; else echo 1; fi
}

refresh_solo() {
  echo "[Solar Orbiter]"
  local y f
  for ((y=SINCE_YEAR; y<=CUR_YEAR; y++)); do
    f="data/SolarOrbiter/SolarOrbiter_${y}.dat.gz"
    if [ "$(needs_rebuild "$f" "$y")" != "1" ]; then echo "  SolO $y: up to date"; continue; fi
    echo "  SolO $y: refreshing (re-pull raw + rebuild)..."
    rm -f "data/SolarOrbiter/raw/solo_merged_hr${y}.txt"
    "$PY38" "$HERE/create_solo.py" --start "$y" --end "$y" \
      && "$PY38" "$HERE/propagate_solo.py" --start "$y" --end "$y" \
      && "$PY38" "$HERE/create_solo.py" --write-lookup-table --start "$y" --end "$y" \
      || echo "  SolO $y: FAILED"
  done
}

refresh_l1() {
  echo "[L1 (OMNI/MIDL)]"
  local y ey f
  for ((y=SINCE_YEAR; y<=CUR_YEAR; y++)); do
    f="data/L1/l1_${y}.dat.gz"
    if [ "$(needs_rebuild "$f" "$y")" != "1" ]; then echo "  L1 $y: up to date"; continue; fi
    echo "  L1 $y: refreshing (clear midl cache + rebuild)..."
    # A year-file spans Nov(y-1)..Jan(y+1); ensure those ephemeris years exist.
    # Don't try to fetch a future year that cannot exist yet (its data isn't
    # posted) -- create_midl_l1 just skips missing ephemeris years at the edge.
    for ey in $((y-1)) "$y" $((y+1)); do
      [ "$ey" -gt "$CUR_YEAR" ] && continue
      [ -f "data/earth_ephemeris/earth_hgi_${ey}.csv" ] && continue
      echo "    fetching Earth ephemeris $ey..."
      "$PY38" "$HERE/fetch_earth_ephemeris.py" --start "$ey" --end "$ey" \
        || echo "    WARN: ephemeris $ey fetch failed (spacepy/numpy incompat); L1 $y may be short at the edge"
    done
    # Drop cached monthly CSVs spanning this file so backfilled hours are pulled.
    rm -f "$MIDL_CACHE/$((y-1))11_L1.csv" "$MIDL_CACHE/$((y-1))12_L1.csv" \
          "$MIDL_CACHE/${y}"??"_L1.csv"   "$MIDL_CACHE/$((y+1))01_L1.csv" 2>/dev/null
    "$PY312" "$HERE/create_midl_l1.py" --start "$y" --end "$y" || echo "  L1 $y: FAILED"
  done
}

refresh_stereoa() {
  echo "[STEREO-A]"
  # The updater rebuilds each requested year fresh (no persistent cache), so
  # collect the contiguous range of years that need work and make one call.
  local y f lo="" hi=""
  for ((y=SINCE_YEAR; y<=CUR_YEAR; y++)); do
    f="data/STEREOA/STEREOA_${y}.dat.gz"
    if [ "$(needs_rebuild "$f" "$y")" = "1" ]; then
      [ -z "$lo" ] && lo="$y"; hi="$y"
    else
      echo "  STEREO-A $y: up to date"
    fi
  done
  if [ -n "$lo" ]; then
    echo "  STEREO-A: refreshing $lo..$hi..."
    "$PY38" "$HERE/download_stereoA_update.py" --start "$lo" --end "$hi" || echo "  STEREO-A: FAILED"
  fi
}

echo "============================================================"
echo " Satellite data refresh  (since $SINCE_YEAR .. $CUR_YEAR)"
echo " Settled window ends $SETTLED_END  (today - ${LAG_DAYS}d)"
echo "============================================================"
refresh_solo
refresh_l1
refresh_stereoa
echo "[STEREO-B] retired 2014 (not transmitting) -- skipped"
echo
echo "============================================================"
echo " Writing manifest"
echo "============================================================"
"$PY38" "$STATUS" manifest
