# Sourced helper for the Step-2 wrappers -- derives run end-months from the
# data manifest so nothing is hard-coded. Regenerating data/DATA_MANIFEST.txt is
# all it takes to advance run_step2_final / run_step2_preliminary.
#
# Requires $ROOT to point at the repo root (the wrappers set it). Sets:
#   DATA_SAFE_MONTH      YYYYMM through which ALL active sources are present
#                        (the data-safe product end -> run_step2_final / 2.1 END)
#   LAST_POSSIBLE_MONTH  YYYYMM the furthest any single active source reaches
#                        (the provisional tail end -> run_step2_preliminary END)
#   AFTER_SAFE_MONTH     DATA_SAFE_MONTH + 1 month
#                        (run_step2_preliminary START; its restart seed = DATA_SAFE_MONTH)
#
# Parse rule (per the manifest header): ignore "#" lines; data lines are
# "|"-delimited. The two summary records are:
#   DATA_SAFE     | <last_utc ISO-8601> | <limiting source key>
#   LAST_POSSIBLE | <last_utc ISO-8601> | <source key>

_manifest="${ROOT:?ROOT must be set before sourcing manifest_dates.sh}/data/DATA_MANIFEST.txt"
[ -f "$_manifest" ] || { echo "ERROR: manifest not found: $_manifest" >&2; exit 1; }

# ISO-8601 "YYYY-MM-DDThh:mm:ss" -> "YYYYMM"
_iso_to_month() { printf '%s%s' "${1:0:4}" "${1:5:2}"; }

# Record lines only ($1 anchored, so the "#   DATA_SAFE ..." schema comment is skipped).
_ds_iso=$(awk -F'|' '$1 ~ /^DATA_SAFE/      {gsub(/ /,"",$2); print $2; exit}' "$_manifest")
_lp_iso=$(awk -F'|' '$1 ~ /^LAST_POSSIBLE/  {gsub(/ /,"",$2); print $2; exit}' "$_manifest")
[ -n "$_ds_iso" ] && [ -n "$_lp_iso" ] || {
  echo "ERROR: could not parse DATA_SAFE / LAST_POSSIBLE from $_manifest" >&2; exit 1; }

DATA_SAFE_MONTH=$(_iso_to_month "$_ds_iso")
LAST_POSSIBLE_MONTH=$(_iso_to_month "$_lp_iso")

# DATA_SAFE_MONTH + 1 calendar month
_y=$((10#${_ds_iso:0:4})); _m=$((10#${_ds_iso:5:2} + 1))
if [ "$_m" -gt 12 ]; then _m=1; _y=$((_y + 1)); fi
AFTER_SAFE_MONTH=$(printf '%04d%02d' "$_y" "$_m")
