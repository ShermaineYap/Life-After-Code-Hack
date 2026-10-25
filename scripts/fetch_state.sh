#!/usr/bin/env bash
# Fetch the "state" of the last good build on the default branch: its dbt
# manifest and catalog (for lineage diffs), its warehouse (so a merge request
# can rebuild only what changed), its SCI report (the carbon baseline) and the
# last known-good monitor snapshot. Missing files are fine: first release.
#
#   scripts/fetch_state.sh [project-id]   (project defaults to $CI_PROJECT_ID)
set -uo pipefail
mkdir -p state monitoring
REF="${CI_DEFAULT_BRANCH:-main}"
PROJECT_ARGS=()
[ -n "${1:-}" ] && PROJECT_ARGS=(--project "$1")

fetch() {  # job, artifact path, output
  if python scripts/ds.py "${PROJECT_ARGS[@]}" job-artifact --ref "$REF" --job "$1" --path "$2" --output "$3" >/dev/null 2>&1; then
    echo "state: fetched $2 from last successful $1 on $REF"
  else
    rm -f "$3"; echo "state: no $2 from $1 on $REF (first release or expired)"
  fi
}

fetch dbt-build target/manifest.json  state/manifest.json
fetch dbt-build target/catalog.json   state/catalog.json
fetch dbt-build build/warehouse.duckdb state/warehouse.duckdb
fetch dbt-build reports/sci.json      state/sci.json
fetch monitor-production monitoring/baseline.json monitoring/baseline.json
# No production release yet: fall back to the last healthy staging snapshot.
[ -f monitoring/baseline.json ] || fetch monitor-staging monitoring/baseline.json monitoring/baseline.json

# A JSON error body is not a manifest: drop anything that isn't valid JSON.
for f in state/manifest.json state/catalog.json state/sci.json monitoring/baseline.json; do
  [ -f "$f" ] && ! python -c "import json,sys; json.load(open(sys.argv[1]))" "$f" 2>/dev/null && rm -f "$f"
done
# Same for the warehouse: a real DuckDB file is never a few bytes of error text.
if [ -f state/warehouse.duckdb ] && [ "$(wc -c < state/warehouse.duckdb)" -lt 16384 ]; then
  rm -f state/warehouse.duckdb
fi
exit 0
