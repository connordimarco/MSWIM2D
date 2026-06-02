#!/bin/bash
# Collect z=0 .outs files from Output/ into a flat directory for the website.

set -euo pipefail

# Project root derived from this script's location, so it is portable across
# machines (e.g. Great Lakes). Override with MSWIM2D_ROOT if needed.
ROOT="${MSWIM2D_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SRCDIR="$ROOT/Output"
DESTDIR="$ROOT/Output_flat"
# Months before MIN_MONTH are served from Tim Keebler's reference run, not our
# Output/. Skip them so we never overwrite the Tim files. Override with MIN_MONTH=0.
MIN_MONTH="${MIN_MONTH:-200407}"

mkdir -p "$DESTDIR"

for dir in "$SRCDIR"/[0-9][0-9][0-9][0-9][0-9][0-9]/OH; do
    [ -d "$dir" ] || continue
    yyyymm=$(basename "$(dirname "$dir")")
    if [ "$yyyymm" -lt "$MIN_MONTH" ]; then
        echo "skip $yyyymm (< MIN_MONTH=$MIN_MONTH; Tim's reference run)"
        continue
    fi
    for f in "$dir"/*.outs; do
        [ -f "$f" ] || continue
        cp -v "$f" "$DESTDIR/${yyyymm}.outs"
    done
done

echo "Done. Files in $DESTDIR:"
ls -lh "$DESTDIR"/*.outs 2>/dev/null || echo "(none)"
