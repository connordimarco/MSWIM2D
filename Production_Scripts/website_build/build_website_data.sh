#!/bin/bash
# Build the website model-output data products for the given months, IN PLACE on
# this box's fast disk (/data). herot reads them over NFS (/nfs/tuija/...), so
# nothing is copied across - the deploy is just one-time symlinks on herot.
#
#   1. flatten  Output/<YYYYMM>/OH/*.outs  ->  Output_flat/<original _e-name>.outs
#               (HARD LINK, same filesystem - no extra space, instant)
#   2. coarse   Output_flat/*.outs  ->  snapshots_coarse/<YYYYMM>/*.bin + manifest
#               (decimated grid for the in-browser field movie)
#
# The raw .outs are read directly by interpolate.php's INTERPOLATE.exe (run on
# herot, Tim-style). The flattened name keeps its `_e` stamp so the endpoint can
# resolve a month -> file and the exe can parse StartTime.
#
#   Scripts/build_website_data.sh YYYYMM [YYYYMM ...]   # specific months
#   Scripts/build_website_data.sh all                   # every month in Output/
#
# One-time herot wiring (see bottom of output) symlinks Output_flat and
# snapshots_coarse into /homedata/SWORD/MSWIM2D_Data_New so the docroot serves
# them and the PHP reads them.
set -euo pipefail

ROOT="${MSWIM2D_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# Dir holding this script and its operational siblings (Production_Scripts).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLIT=$HERE/split_outs.py
PRODUCTS=$HERE/build_products_manifest.py
# Python for split_outs (numpy) + build_products_manifest. refresh.sh passes the
# mswim2d_env interpreter via PYTHON; falls back to python3 for standalone use.
PYBIN="${PYTHON:-python3}"
# Everything the website needs lives under one dir; herot makes ONE symlink to it.
DATA=$ROOT/website_data/MSWIM2D_Data_New

# Source trees, in confidence order (final -> preliminary -> prediction). The run
# splits one continuous simulation into these three by data confidence; we flatten
# ALL of them into the SINGLE Output_flat below, so the interpolation endpoint never
# needs to know about tiers (a month's .outs name is unique regardless of tier). The
# tier split is surfaced to the website only via products.json (see build_products_manifest.py).
#
# Default to whichever tiered trees exist; if NONE do (today, before the rsync from
# the run box), fall back to the legacy single Output/ so this keeps working. Override
# the whole list with MSWIM2D_TREES="tier:path tier:path ...".
if [ -n "${MSWIM2D_TREES:-}" ]; then
  read -r -a TREES <<< "$MSWIM2D_TREES"
else
  TREES=()
  for entry in "final:$ROOT/Output_final" "preliminary:$ROOT/Output_preliminary" "prediction:$ROOT/Output_prediction"; do
    [ -d "${entry#*:}" ] && TREES+=("$entry")
  done
  [ "${#TREES[@]}" -eq 0 ] && TREES=("final:$ROOT/Output")
fi
echo "Source trees: ${TREES[*]}"

# Operational cutoff: months strictly before MIN_MONTH are served from Tim
# Keebler's reference run (hard-linked into Output_flat under a per-month _e
# name), NOT from our Output/. Skip them here so a rebuild - especially the
# `all` form, which globs every YYYYMM in Output/ including our pre-200407
# spin-up - never clobbers the Tim files (or their coarse snapshots). Rebuild a
# pre-cutoff month deliberately with: MIN_MONTH=0 Scripts/build_website_data.sh YYYYMM
MIN_MONTH="${MIN_MONTH:-200407}"

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 YYYYMM [YYYYMM ...] | all" >&2
  exit 1
fi

# Refresh the satellite INPUT data (the in-situ .dat.gz BATSRUS reads) and
# rewrite data/DATA_MANIFEST.txt before (re)building the model OUTPUT products.
# Separate concern, separate script - this only orchestrates the call. Skip with
# SKIP_SATELLITE_DOWNLOAD=1. Non-fatal: a download hiccup must not abort the
# website build (we run under `set -e`, hence the `|| true`).
if [ "${SKIP_SATELLITE_DOWNLOAD:-0}" != "1" ]; then
  echo "=== Refreshing satellite input data (SKIP_SATELLITE_DOWNLOAD=1 to skip) ==="
  "$HERE/../data_download/update_satellite_data.sh" || echo "WARN: satellite refresh failed; continuing with model-output build."
fi

# Map every YYYYMM that exists in any tree to its source .outs (most-confident tree
# wins a duplicated month) and to its tier. Months should be disjoint across trees,
# but ordering makes a collision deterministic.
declare -A path_of tier_of
for entry in "${TREES[@]}"; do
  tier=${entry%%:*}; tree=${entry#*:}
  for d in "$tree"/[0-9][0-9][0-9][0-9][0-9][0-9]; do
    [ -d "$d" ] || continue
    ym=$(basename "$d")
    [ -n "${path_of[$ym]:-}" ] && continue   # earlier (more-confident) tree already claimed it
    src=$(ls "$d/OH"/*.outs 2>/dev/null | head -1 || true)
    [ -n "$src" ] || continue
    path_of[$ym]=$src
    tier_of[$ym]=$tier
  done
done

if [ "$1" = "all" ]; then
  mapfile -t months < <(printf '%s\n' "${!path_of[@]}" | sort)
else
  months=("$@")
fi

mkdir -p "$DATA/Output_flat" "$DATA/snapshots_coarse"

# Opt-in prune of stale months (PRUNE_STALE=1, only on `all`): remove Output_flat
# .outs and snapshots_coarse dirs for months no longer owned by any source tree
# (or Tim's pre-MIN_MONTH reference set). Kept OPT-IN and `all`-only on purpose:
# during a partial rsync of the tier trees, the "valid" set is incomplete, and an
# automatic prune would delete the only copy of months whose tree hasn't landed yet.
if [ "${PRUNE_STALE:-0}" = "1" ] && [ "$1" = "all" ]; then
  declare -A valid
  for ym in "${!path_of[@]}"; do valid[$ym]=1; done            # tier-tree months
  for f in "$ROOT"/Tim_MSWIM2D_1hr/*.outs; do                   # Tim reference (< MIN_MONTH)
    [ -e "$f" ] || continue
    m=$(basename "$f"); m=${m##*_}; m=${m%.outs}
    [[ "$m" =~ ^[0-9]{6}$ ]] && [ "$m" -lt "$MIN_MONTH" ] && valid[$m]=1
  done
  pruned=()
  for f in "$DATA"/Output_flat/*.outs; do
    [ -e "$f" ] || continue
    if [[ "$(basename "$f")" =~ _e([0-9]{4})([0-9]{2}) ]]; then
      m="${BASH_REMATCH[1]}${BASH_REMATCH[2]}"
      if [ -z "${valid[$m]:-}" ]; then rm -f "$f"; rm -rf "$DATA/snapshots_coarse/$m"; pruned+=("$m"); fi
    fi
  done
  if [ "${#pruned[@]}" -gt 0 ]; then
    echo "Pruned ${#pruned[@]} stale month(s): ${pruned[*]}"
    # Drop the pruned months from the coarse manifest so the browser never fetches them.
    MSWIM2D_PRUNED="${pruned[*]}" "$PYBIN" - "$DATA/snapshots_coarse/manifest.json" <<'PY'
import json, os, sys
p = sys.argv[1]
gone = set(os.environ.get('MSWIM2D_PRUNED', '').split())
if os.path.exists(p):
    d = json.load(open(p))
    for m in gone:
        d.get('months', {}).pop(m, None)
    json.dump(d, open(p, 'w'))
PY
  fi
fi

built=()
for ym in "${months[@]}"; do
  if [[ "$ym" =~ ^[0-9]{6}$ ]] && [ "$ym" -lt "$MIN_MONTH" ]; then
    echo "skip $ym (< MIN_MONTH=$MIN_MONTH; served from Tim's reference run, not clobbering)"
    continue
  fi
  src=${path_of[$ym]:-}
  if [ -z "$src" ]; then
    echo "skip $ym (no .outs in any source tree)"
    continue
  fi
  # Hard link (Output trees and website_data share /data) - no copy, keep original name.
  ln -f "$src" "$DATA/Output_flat/$(basename "$src")"
  built+=("$ym")
done

if [ "${#built[@]}" -eq 0 ]; then
  echo "Nothing to build."
  exit 0
fi

echo "Flattened ${#built[@]} month(s). Building coarse grid..."
MSWIM2D_DATA_NEW="$DATA" "$PYBIN" "$SPLIT" --coarse-only "${built[@]}"

# Regenerate products.json - the confidence-tier index (final/preliminary/prediction
# ranges + the two seam dates from data/DATA_MANIFEST.txt) the Data page reads. Written
# before the chmod below so apache picks up the right perms. Non-fatal.
echo "Writing products.json (output confidence tiers)..."
"$PYBIN" "$PRODUCTS" --root "$ROOT" --data-new "$DATA" \
  || echo "WARN: products.json generation failed; tier cards may be stale."

# Model output is mode 0770; herot's apache reads/serves it over NFS so it needs
# other-read. Hard-linking from Output/ re-inherits 0770, so re-apply every build.
chmod -R o+rX "$DATA"

cat <<EOF

=== Built ${#built[@]} month(s): ${built[*]} ===
Everything the website needs is under: $DATA
  Output_flat/      raw .outs (hard links, all tiers) - read by interpolate.php on herot
  snapshots_coarse/ decimated grid + manifest - served for the field movie
  products.json     confidence-tier index (final/preliminary/prediction) - read by the Data page
  Satellite_Data/   in-situ input CSVs

herot serves it all via ONE symlink (no data copied - read over NFS):
  ln -sfn /nfs/tuija/cdimarco/MSWIM2D/website_data/MSWIM2D_Data_New \\
          /homedata/MSWIM2D/MSWIM2D_Data_New

The precomputed trajectory traces (the Data page's TRAJ_BASE='precomputed_trajectories/chunks/')
are served from a SIBLING dir, deployed by refresh.sh stage 5. It also needs a one-time
docroot symlink on herot, e.g.:
  ln -sfn /homedata/MSWIM2D/precomputed_trajectories \\
          /var/www/html/MSWIM2D/precomputed_trajectories
EOF
