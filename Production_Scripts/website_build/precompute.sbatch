#!/bin/bash
# Precompute satellite/planet trajectory traces through the model .outs.
#
# One node, 32 GNU-parallel workers over the 492-month queue. Each work item is a
# MONTH; the worker runs INTERP_OUTPUT.exe for every body whose window covers that
# month, reusing the month's hot .outs across bodies (see run_month.sh). Raw
# per-body/per-month output lands in spice/interp_out/<Body>/<YYYYMM>.dat; stitch
# into per-body/year chunks afterward.
#
# Usage:  sbatch Production_Scripts/precompute.sbatch            # all months
#         sbatch Production_Scripts/precompute.sbatch months.txt # custom month list
#         (or run as a plain script if this box has no SLURM: bash Production_Scripts/precompute.sbatch)
#
# ============================================================================
# ADAPT-FOR-OPERATIONAL-BOX: the SBATCH header + module loads below came from
# Great Lakes. Before running here, confirm/replace:
#   - Does this box use SLURM at all? If not, run with `bash` (the #SBATCH lines
#     are inert comments) and set NWORKERS by hand.
#   - --account / --partition / --chdir are Great Lakes-specific.
#   - `module load gcc/openmpi` matches how INTERP_OUTPUT.exe was linked; rebuild
#     or re-point EXE for this box's toolchain.
#   - MSWIM2D_ROOT below defaults to the repo root via this script's location.
# ============================================================================
#SBATCH --job-name=traj_precompute
#SBATCH --account=tuija98
#SBATCH --partition=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --mail-user=cdimarco@umich.edu
#SBATCH --mail-type=BEGIN,END,FAIL
#SBATCH --output=logs/precompute_%j.out
#SBATCH --error=logs/precompute_%j.err

set -uo pipefail
# This script + run_month.sh + the two maps live here (Production_Scripts);
# the trajectory/result DATA tree stays under spice/.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${MSWIM2D_ROOT:-$(cd "$HERE/../.." && pwd)}"
SPICE=$ROOT/spice
cd "$ROOT"

module load gcc/10.3.0 openmpi/4.1.6 2>/dev/null || true  # INTERP_OUTPUT.exe link deps (no-op without `module`)

export EXE="${EXE:-$ROOT/INTERP_OUTPUT.exe}"
export TRAJDIR="$SPICE/trajectories"        # per-body <Body>.dat inputs (data tree)
export RESULTS="$SPICE/interp_out"          # per-body/per-month outputs (data tree)
export OUTSMAP="$HERE/month_outs.map"       # moved here with the precompute scripts
export BODYMAP="$HERE/body_months.map"
export WORKROOT=/tmp/traj_${SLURM_JOB_ID:-$$}

NWORKERS=${SLURM_CPUS_PER_TASK:-32}
MONTHS_FILE="${1:-}"

mkdir -p "$RESULTS" "$WORKROOT" logs
trap 'rm -rf "$WORKROOT"' EXIT

echo "=== traj precompute ==="
echo "host=$(hostname) jobid=${SLURM_JOB_ID:-?} workers=$NWORKERS"
echo "exe=$EXE"
date

# Month queue: a custom file, else column 1 of the .outs map (all 492).
if [ -n "$MONTHS_FILE" ]; then
  months_src="$MONTHS_FILE"
else
  cut -f1 "$OUTSMAP" > "$WORKROOT/months.txt"
  months_src="$WORKROOT/months.txt"
fi
echo "months to process: $(wc -l < "$months_src")"

parallel --will-cite -j "$NWORKERS" --halt never --joblog "logs/precompute_${SLURM_JOB_ID:-$$}.joblog" \
  bash "$HERE/run_month.sh" {} < "$months_src"

echo "=== summary ==="
for d in "$RESULTS"/*/; do
  [ -d "$d" ] && echo "  $(basename "$d"): $(ls "$d" | wc -l) months"
done
date
echo "=== done ==="
