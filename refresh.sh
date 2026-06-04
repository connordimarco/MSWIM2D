#!/usr/bin/env bash
# ============================================================================
# refresh.sh - one-shot monthly MSWIM2D update, end to end.
#
#   data  ->  model (3 tiers)  ->  trajectories  ->  website  ->  rsync to herot
#
# SAFE BY DEFAULT: with no flags it only PRINTS the plan (every command it would
# run, with the exact month ranges it computed). Add --run to actually execute.
# The model stage is hours of MPI and wipes/rebuilds the provisional tiers, so
# look at the plan first.
#
#   ./refresh.sh                 # dry plan (recommended first)
#   ./refresh.sh --run           # execute
#   ./refresh.sh --run --force   # also rebuild preliminary+prediction even if frontiers didn't move
#   ./refresh.sh --run --skip-data   # skip the satellite re-download (reuse current data/)
#   MPI_RANKS=8 ./refresh.sh --run   # override bare-metal MPI rank count (default 6)
#
# ---------------------------------------------------------------------------
# THE LOGIC (why each tier runs, given the two manifest frontiers):
#
#   S = DATA_SAFE month      (all active sources present through here -> FINAL end)
#   L = LAST_POSSIBLE month  (furthest any source reaches            -> PRELIM end)
#   F = last FINAL month on disk that has output    (the real final frontier)
#   P = last PRELIMINARY month on disk
#
#   FINAL        runs iff S > F        -> RunAll_Step2_final  F+1 .. S
#                (final is settled; never rebuilt over existing months)
#   PRELIMINARY  if FINAL ran (its seed restart changed): WIPE Output_preliminary,
#                  re-run S+1 .. L   (this also drops months just promoted to final)
#                elif L moved out:    extend  P+1 .. L
#                elif L moved in:     trim the months now past the frontier
#   PREDICTION   runs iff FINAL or PRELIMINARY changed (it is anchored at L and
#                  seeds from the new preliminary restart): rebuild data_prediction,
#                  WIPE Output_prediction, re-run L+1 .. L+horizon
#
# So DATA_SAFE and LAST_POSSIBLE moving independently each trigger only the work
# they actually invalidate. --force forces the prelim+prediction rebuild.
# ============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PS="$ROOT/Production_Scripts"
DD="$PS/data_download"; RM="$PS/run_model"; WB="$PS/website_build"

# ---- config ----
export MPI_RANKS="${MPI_RANKS:-6}"         # bare-metal MPI ranks; RunAll_*.pl read $MPI_RANKS
PRED_HORIZON="${PRED_HORIZON:-12}"         # prediction-tier length in months
INTERP_EXE="${INTERP_OUTPUT_EXE:-$ROOT/INTERP_OUTPUT.exe}"
# Python for the website_build stage (needs numpy/pandas/spiceypy). The processing
# venv mswim2d_env has them; fall back to python3 if it's missing. data_download/
# keeps its own interpreters (python3.8 anaconda + /usr/bin/python3.12).
PY="${MSWIM2D_PY:-$ROOT/mswim2d_env/bin/python}"
[ -x "$PY" ] || PY=python3

RUN=0; FORCE=0; SKIP_DATA=0
for a in "$@"; do case "$a" in
  --run)       RUN=1 ;;
  --force)     FORCE=1 ;;
  --skip-data) SKIP_DATA=1 ;;
  -h|--help)   sed -n '2,40p' "$0"; exit 0 ;;
  *) echo "unknown arg: $a" >&2; exit 2 ;;
esac; done

log(){ printf '\n========== %s ==========\n' "$*"; }
# Echo every command; execute only under --run. eval runs in this shell, so
# `export`s in one do_ persist into later ones. do_ is non-fatal (failures are
# logged but the pipeline continues); do_strict ABORTS on failure and is used for
# the model/table stages, where building the website from a failed run is wrong.
do_(){ echo "+ $*"; [ "$RUN" = 1 ] && { eval "$*" || echo "!! WARN (continuing): $*" >&2; }; return 0; }
do_strict(){ echo "+ $*"; if [ "$RUN" = 1 ]; then eval "$*" || { echo "!! FAILED, aborting refresh: $*" >&2; exit 1; }; fi; }
# Like do_ but RETURNS the command's exit status (0 in plan mode), so the caller
# can branch on success — used to gate the precompute on make_traj succeeding.
do_check(){ echo "+ $*"; if [ "$RUN" = 1 ]; then eval "$*"; return $?; fi; return 0; }

# ---- helpers ----
# last YYYYMM under $1 that actually has an OH/*.outs (an empty/restart-only dir
# does not count as a built month)
last_month(){
  local d
  for d in $(ls -d "$1"/[0-9][0-9][0-9][0-9][0-9][0-9] 2>/dev/null | sort -r); do
    if ls "$d"/OH/*.outs >/dev/null 2>&1; then basename "$d"; return; fi
  done
}
# YYYYMM + N months
m_add(){ local y=$((10#${1:0:4})) mo=$((10#${1:4:2})) t=$(( 10#${1:0:4}*12 + 10#${1:4:2}-1 + $2 ))
         printf '%04d%02d' $(( t/12 )) $(( t%12 + 1 )); }

# ---- rsync destination from .env ----
HEROT_AUTH=$(sed -n 's/^ *HEROT_AUTH *= *"\(.*\)".*/\1/p' "$ROOT/.env" 2>/dev/null)
HEROT_DIR=$( sed -n 's/^ *HEROT_DIR *= *"\(.*\)".*/\1/p'  "$ROOT/.env" 2>/dev/null)

[ "$RUN" = 1 ] || echo "*** PLAN ONLY (no --run): commands below are printed, not executed ***"

# ============================================================================
log "STAGE 1  satellite data -> manifest"
if [ "$SKIP_DATA" = 1 ]; then echo "(skipped: --skip-data; reusing data/)"
else do_ "\"$DD/update_satellite_data.sh\""; fi

# Frontier months, straight from the manifest (never hard-coded).
# shellcheck disable=SC1090
source "$RM/manifest_dates.sh"
S="$DATA_SAFE_MONTH"; AS="$AFTER_SAFE_MONTH"; L="$LAST_POSSIBLE_MONTH"
F=$(last_month Output_final); P=$(last_month Output_preliminary); D=$(last_month Output_prediction)
echo "manifest : DATA_SAFE=$S   LAST_POSSIBLE=$L"
echo "on disk  : final<=${F:-none}   preliminary<=${P:-none}   prediction<=${D:-none}   (MPI ranks=$MPI_RANKS)"

# ============================================================================
log "STAGE 2  model"
final_ran=0; prelim_ran=0

# ---- FINAL ----
if [ -z "$F" ]; then
  echo "!! Output_final is empty -> this needs the ONE-TIME spin-up, not refresh.sh:"
  echo "     perl $RM/RunAll_Step1.pl       -s=199601 -e=200312"
  echo "     perl $RM/RunAll_Step2_final.pl -s=200407 -e=$S"
  echo "   Aborting (run those once, then re-run refresh.sh)."
  exit 1
elif [[ "$S" > "$F" ]]; then
  do_strict "perl \"$RM/RunAll_Step2_final.pl\" -s=$(m_add "$F" 1) -e=$S"
  final_ran=1
else
  echo "final       : current (frontier $F, DATA_SAFE $S)"
fi

# ---- PRELIMINARY ----
if [ "$final_ran" = 1 ] || [ "$FORCE" = 1 ]; then
  if [[ "$L" > "$S" ]]; then
    echo "preliminary : final frontier moved -> full re-run S+1..L (drops promoted months)"
    do_ "rm -rf Output_preliminary/[0-9][0-9][0-9][0-9][0-9][0-9]"
    do_strict "perl \"$RM/RunAll_Preliminary.pl\" -s=$AS -e=$L"
    prelim_ran=1
  else
    echo "preliminary : empty range (LAST_POSSIBLE $L <= DATA_SAFE $S); clearing"
    do_ "rm -rf Output_preliminary/[0-9][0-9][0-9][0-9][0-9][0-9]"
  fi
elif [ -z "$P" ] && [[ "$L" > "$S" ]]; then
  echo "preliminary : none on disk -> build S+1..L"
  do_strict "perl \"$RM/RunAll_Preliminary.pl\" -s=$AS -e=$L"
  prelim_ran=1
elif [ -n "$P" ] && [[ "$L" > "$P" ]]; then
  echo "preliminary : extend $(m_add "$P" 1)..L"
  do_strict "perl \"$RM/RunAll_Preliminary.pl\" -s=$(m_add "$P" 1) -e=$L"
  prelim_ran=1
elif [ -n "$P" ] && [[ "$P" > "$L" ]]; then
  echo "preliminary : frontier retreated -> trim months past $L"
  do_ "for m in Output_preliminary/[0-9][0-9][0-9][0-9][0-9][0-9]; do [[ \"\$(basename \"\$m\")\" > \"$L\" ]] && rm -rf \"\$m\"; done"
  prelim_ran=1
else
  echo "preliminary : current (frontier ${P:-none}, LAST_POSSIBLE $L)"
fi

# ---- PREDICTION (anchored at the frontier; seeds from the preliminary restart) ----
# Prediction seeds month L read-only from Output_preliminary/L, so it can only run
# when a preliminary tier exists, i.e. L > S. If L <= S there is no provisional tail
# to seed from and we skip (rather than die on a missing/wiped seed restart).
if ! [[ "$L" > "$S" ]]; then
  echo "prediction  : skipped (no provisional tier to seed from; LAST_POSSIBLE $L <= DATA_SAFE $S)"
elif [ "$final_ran" = 1 ] || [ "$prelim_ran" = 1 ] || [ "$FORCE" = 1 ] || [ -z "$D" ]; then
  ps=$(m_add "$L" 1); pe=$(m_add "$L" "$PRED_HORIZON")
  echo "prediction  : rebuild $ps..$pe (horizon ${PRED_HORIZON}mo)"
  do_strict "python3 \"$RM/make_prediction_tables.py\" --out-dir data_prediction --months $PRED_HORIZON"   # -> data_prediction/ (anchored at L)
  do_ "rm -rf Output_prediction/[0-9][0-9][0-9][0-9][0-9][0-9]"
  do_strict "perl \"$RM/RunAll_Prediction.pl\" -s=$ps -e=$pe"
else
  echo "prediction  : current"
fi

# ============================================================================
log "STAGE 3  trajectory precompute"
# Needs spiceypy (make_traj) + INTERP_OUTPUT.exe (the trace). If either is missing,
# skip the WHOLE stage cleanly so the website build + deploy (stages 4-5) still run.
traj_end=$(m_add "$(m_add "$L" "$PRED_HORIZON")" 1)         # first-of month after the last predicted month
if ! "$PY" -c "import spiceypy" 2>/dev/null; then
  echo "SKIP trajectory stage: $PY lacks spiceypy (build mswim2d_env from requirements.txt). Stages 4-5 still run."
elif [ ! -x "$INTERP_EXE" ]; then
  echo "SKIP trajectory stage: INTERP_OUTPUT.exe not at $INTERP_EXE (build it via the root Makefile). Stages 4-5 still run."
else
  # make_traj MUST succeed (it extends trajectories to the new horizon); only then
  # do we precompute, so we never trace against stale/short trajectories.
  if do_check "\"$PY\" \"$WB/make_traj.py\" --kernel-root \"$ROOT/spice\" --out \"$ROOT/spice/trajectories\" --model-end ${traj_end:0:4}-${traj_end:4:2}-01"; then
    do_ "\"$PY\" \"$WB/make_maps.py\""                       # rebuild month_outs.map + body_months.map
    do_ "export EXE=\"$INTERP_EXE\" TRAJDIR=\"$ROOT/spice/trajectories\" RESULTS=\"$ROOT/spice/interp_out\" OUTSMAP=\"$WB/month_outs.map\" BODYMAP=\"$WB/body_months.map\" WORKROOT=\"/tmp/traj_\$\$\""
    do_ "mkdir -p \"$ROOT/spice/interp_out\""
    # No SLURM / GNU parallel here -> fan months out with xargs -P (run_month.sh is env-driven).
    do_ "cut -f1 \"$WB/month_outs.map\" | xargs -P \"$MPI_RANKS\" -I{} bash \"$WB/run_month.sh\" {}"
    do_ "\"$PY\" \"$WB/stitch.py\" --interp-out \"$ROOT/spice/interp_out\" --outs-map \"$WB/month_outs.map\" --out \"$ROOT/spice/chunks\""
  else
    echo "!! make_traj failed -> skipping precompute (would trace stale trajectories). Stages 4-5 still run."
  fi
fi

# ============================================================================
log "STAGE 4  website data products"
do_ "SKIP_SATELLITE_DOWNLOAD=1 PYTHON=\"$PY\" \"$WB/build_website_data.sh\" all"   # flatten + coarse grid + products.json
# Overlay data: export the source CSVs and chunk them, BOTH pointed at the same
# Satellite_Data/ inside the rsync'd tree, so fresh data flows export -> chunk ->
# HEROT_DIR/Satellite_Data/chunks via the one main rsync below.
SATDIR="$ROOT/website_data/MSWIM2D_Data_New/Satellite_Data"
do_ "\"$PY\" \"$WB/export_website_data.py\" --out-dir \"$SATDIR\""
do_ "\"$PY\" \"$WB/chunk_satellite_data.py\" --data-new \"$ROOT/website_data/MSWIM2D_Data_New\""

# ============================================================================
log "STAGE 5  rsync -> herot"
if [ -n "$HEROT_AUTH" ] && [ -n "$HEROT_DIR" ]; then
  # One rsync ships everything under MSWIM2D_Data_New, including the in-situ
  # overlay chunks at HEROT_DIR/Satellite_Data/chunks.
  do_ "rsync -az --delete \"$ROOT/website_data/MSWIM2D_Data_New/\" \"$HEROT_AUTH:$HEROT_DIR/\""
  # Trajectory precompute chunks are served at the docroot-level
  # precomputed_trajectories/ (a sibling of MSWIM2D_Data_New, so OUTSIDE the
  # --delete above). Override the dest with HEROT_TRAJ_DIR if needed.
  TRAJ_DEST="${HEROT_TRAJ_DIR:-$(dirname "$HEROT_DIR")/precomputed_trajectories}"
  if [ -d "$ROOT/spice/chunks" ]; then
    do_ "rsync -az --delete \"$ROOT/spice/chunks/\" \"$HEROT_AUTH:$TRAJ_DEST/chunks/\""
  fi
else
  echo "SKIP rsync: HEROT_AUTH / HEROT_DIR not found in .env"
fi

log "DONE  $([ "$RUN" = 1 ] && echo 'executed' || echo 'PLAN only - re-run with --run to execute')"
