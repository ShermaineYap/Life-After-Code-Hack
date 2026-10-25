#!/usr/bin/env bash
# The whole Downstream review in one command, so a Duo agent (whose
# run_command tool has no shell operators) can run it, read one report, fix
# what it says, and run it again.
#
#   bash scripts/downstream_review.sh [project-id]
#
# Steps: fetch main's state -> dbt parse -> lineage diff -> slim build on top of
# main's warehouse (full build if there is no state) -> PII scan -> SCI vs the
# last full build. Writes reports/review.md and exits non-zero if anything blocks.
set -uo pipefail
cd "$(dirname "$0")/.."
export DBT_TARGET="${DBT_TARGET:-ci}" DUCKDB_PATH="${DUCKDB_PATH:-target/warehouse.duckdb}"
mkdir -p reports target
STATUS=0
block() { [ "$STATUS" -eq 0 ] && STATUS=$1; return 0; }  # keep the first (root) cause

python -m pip install -q -r requirements.txt >/dev/null 2>&1 || true
bash scripts/fetch_state.sh "${1:-}"

dbt parse --profiles-dir . -q || { echo "dbt parse failed: fix the SQL/YAML above."; exit 1; }

python scripts/lineage_impact.py --manifest target/manifest.json \
  --state-manifest state/manifest.json --state-catalog state/catalog.json \
  --json reports/lineage.json --markdown reports/lineage.md --fail-on-breaking >/dev/null || block 2
python -c "import json,sys; sys.exit(1 if json.load(open('reports/lineage.json'))['test_gaps'] else 0)" || block 5

if [ -f state/warehouse.duckdb ] && [ -f state/manifest.json ]; then
  cp state/warehouse.duckdb target/warehouse.duckdb
  dbt build --profiles-dir . --select state:modified+ --state state -q > reports/dbt.log 2>&1 || block 3
  LABEL="slim (state:modified+)"
else
  dbt build --profiles-dir . -q > reports/dbt.log 2>&1 || block 3
  LABEL="full refresh"
fi

python scripts/sci.py --run-results target/run_results.json --label "$LABEL" \
  --baseline state/sci.json --json reports/sci.json --markdown reports/sci.md >/dev/null
python scripts/pii_sentinel.py --warehouse target/warehouse.duckdb \
  --json reports/pii.json --markdown reports/pii.md --junit reports/pii-junit.xml >/dev/null || block 4

{
  case $STATUS in
    0) echo "# Downstream review: ready to merge";;
    2) echo "# Downstream review: BLOCKED by a contract break";;
    3) echo "# Downstream review: BLOCKED, dbt build or tests failed (see reports/dbt.log)";;
    4) echo "# Downstream review: BLOCKED, personal data outside the raw schema";;
    5) echo "# Downstream review: BLOCKED, new or changed models are missing tests";;
  esac
  echo
  cat reports/lineage.md; echo
  if grep -qE "ERROR|FAIL" reports/dbt.log 2>/dev/null; then echo "**dbt build output:**"; echo '```'; grep -E "ERROR|FAIL|Error" reports/dbt.log | head -20; echo '```'; echo; fi
  cat reports/pii.md; echo
  cat reports/sci.md
} > reports/review.md

cat reports/review.md
exit $STATUS
