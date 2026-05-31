#!/bin/bash
# Collect z=0 .outs files from Output/ into a flat directory for the website.

set -euo pipefail

# Project root derived from this script's location, so it is portable across
# machines (e.g. Great Lakes). Override with MSWIM2D_ROOT if needed.
ROOT="${MSWIM2D_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SRCDIR="$ROOT/Output"
DESTDIR="$ROOT/Output_flat"

mkdir -p "$DESTDIR"

for dir in "$SRCDIR"/[0-9][0-9][0-9][0-9][0-9][0-9]/OH; do
    [ -d "$dir" ] || continue
    yyyymm=$(basename "$(dirname "$dir")")
    for f in "$dir"/*.outs; do
        [ -f "$f" ] || continue
        cp -v "$f" "$DESTDIR/${yyyymm}.outs"
    done
done

echo "Done. Files in $DESTDIR:"
ls -lh "$DESTDIR"/*.outs 2>/dev/null || echo "(none)"
