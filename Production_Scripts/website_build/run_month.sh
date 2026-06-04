#!/bin/bash
# run_month.sh <YYYYMM>
# Process ONE month: run INTERP_OUTPUT.exe for every body whose window covers the
# month, against that month's single .outs. Reads the .outs cold once; bodies
# 2..N reuse the page cache. All INTERP.in paths are kept short (<100 chars,
# Fortran character(len=100)) by cd-ing into a node-local workdir and using bare
# relative symlink names.
#
# Required env (exported by precompute.sbatch):
#   EXE       absolute path to INTERP_OUTPUT.exe
#   TRAJDIR   dir of per-body <Body>.dat trajectory files
#   RESULTS   output root; writes RESULTS/<Body>/<YYYYMM>.dat
#   OUTSMAP   month_outs.map  (YYYYMM \t src \t outs-path)
#   BODYMAP   body_months.map (Body \t firstYYYYMM \t lastYYYYMM)
#   WORKROOT  node-local scratch root (e.g. /tmp/traj_$JOBID)
set -u

ym="$1"

outs=$(awk -F'\t' -v m="$ym" '$1==m{print $3}' "$OUTSMAP")
if [ -z "$outs" ] || [ ! -e "$outs" ]; then
  echo "FAIL $ym no-outs"; exit 1
fi
base=$(basename "$outs")

wd="$WORKROOT/$ym"
mkdir -p "$wd" || { echo "FAIL $ym mkdir"; exit 1; }
cd "$wd"      || { echo "FAIL $ym cd"; exit 1; }
ln -sf "$outs" "./$base"

n_ok=0
while IFS=$'\t' read -r body a b; do
  [ -z "$body" ] && continue
  # month outside this body's [first,last] window -> skip (no data anyway)
  if [[ "$ym" < "$a" || "$ym" > "$b" ]]; then continue; fi
  ln -sf "$TRAJDIR/$body.dat" "./$body.dat"
  printf './%s\nreal4\n%s.dat\n%s.out\n' "$base" "$body" "$body" > "in_$body"
  "$EXE" < "in_$body" > "log_$body" 2>&1
  if [ -s "$body.out" ] && [ "$(wc -l < "$body.out")" -gt 1 ]; then
    mkdir -p "$RESULTS/$body"
    cp -f "$body.out" "$RESULTS/$body/$ym.dat"
    n_ok=$((n_ok+1))
  fi
done < "$BODYMAP"

cd /
rm -rf "$wd"
echo "DONE $ym bodies=$n_ok"
