# AGENTS.md: how to work in this repository

Read this before changing anything. It applies to every GitLab Duo flow in
`.gitlab/duo/flows/` and to people.

## What this is

A dbt project (DuckDB) behind a KPI dashboard for a fictional Malaysian online
store, plus **Downstream**: the agents and pipeline that take a data change
from an issue to a monitored production release.

```
seeds/            raw CSVs (raw schema): customers (contains PII), orders, events
models/staging/   one model per source, renamed + typed; PII is hashed HERE and nowhere else
models/intermediate/  reusable joins/aggregations
models/marts/     tables the dashboard reads; meta.contract = v1 means downstream consumers depend on the columns
models/exposures.yml  every dashboard tile, so lineage can name the tiles a change reaches
tests/            singular dbt tests
dashboard/app.py  the FastAPI dashboard (reads marts.* only)
scripts/          lineage review, PII sentinel, SCI, monitor, release notes, GitLab bridge (ds.py)
pytests/          unit tests for scripts/ and dashboard/
```

## Commands

| Task | Command |
| --- | --- |
| Full review of the current branch (what the agents run) | `bash scripts/downstream_review.sh <project id>` → `reports/review.md` |
| Build + test everything | `dbt build --profiles-dir .` |
| Build only what changed | `dbt build --profiles-dir . --select state:modified+ --state state` |
| Unit tests | `pytest pytests -q` |
| Regenerate the Hands-off flow after editing `downstream.yaml` | `python scripts/build_flows.py` |
| Pipeline operations | `python scripts/ds.py --project <id> <command>` (see its `--help`) |

`run_command` has no shell: call programs directly (`program="bash"`,
`args="scripts/downstream_review.sh 123"`), never with `&&`, `|` or `>`.

## Conventions

- Models: `stg_<source>`, `int_<thing>`, `dim_<entity>`, `fct_<process>`, `kpi_<surface>`. snake_case columns.
- Money columns end in `_myr` and are `decimal`. Months are `date` truncated to the first day, named `*_month`.
- Every mart excludes `is_test_account = true` customers.
- Every model has a description in its layer's YAML. Key columns (`*_id`, `*_month`) get `unique` + `not_null`.
- A new dashboard tile = a new exposure in `models/exposures.yml` that `depends_on` the mart it reads.
- Commit messages: Conventional Commits, ending with the issue reference, e.g. `feat(marts): add monthly active customers (#12)`.
- Branches: `downstream/<issue-iid>-<slug>`.

## Rules agents must not break

1. Never delete or weaken a test, scan, or CI job to make a pipeline pass. Fix the cause.
2. Never put raw email, phone, IC/NRIC, passport or card numbers outside the `raw` schema. Hash in staging.
3. Never rename or remove a column of a `meta.contract` model without updating every downstream reference in the same merge request; the lineage review blocks this.
4. Never push to the default branch directly. Changes go through a merge request and its pipeline.
5. Production is deployed only by the `deploy-production` job. In Supervised mode it needs a human approval.
6. Never print or commit secrets. Cloud credentials exist only as masked CI variables.
