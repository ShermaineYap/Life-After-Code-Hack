# Devpost submission text

Paste section by section. Replace `<...>` before submitting.

**Project name:** Downstream

**Tagline:** The post-code lifecycle for data pipelines: GitLab Duo agents that take an analytics change from issue to monitored release.

**Path / autonomy:** Path A: Start Fresh, Supervised.

## Inspiration

Analytics engineers don't struggle to write SQL. They struggle with what happens after: a
renamed column silently breaks revenue reporting, nobody writes tests for the new model, a
raw email lands in a shared table, every change rebuilds the whole warehouse, and the
dashboard is wrong for a weekend before anyone notices. Most DevSecOps automation is built
for web services. Data teams get none of it, so we built Downstream for them.

## What it does

One human opens an issue. A single GitLab Duo flow then:

- **Plan**: the triage agent writes acceptance criteria and a data-test plan on the issue.
- **Create**: the developer agent implements the dbt change and opens a merge request.
- **Verify**: the lineage reviewer diffs the dbt manifest against main's, finds every downstream model and dashboard tile the change reaches, blocks contract breaks (a removed column still used downstream), fixes them, and adds missing tests.
- **Secure**: SAST, secret detection, SBOM dependency scanning, and a PII Sentinel that reads sampled values, not just column names.
- **Package**: the dashboard image and the exact tested warehouse file are versioned in GitLab's registries.
- **Configure**: staging and production environments on Google Cloud Run, one image per data release.
- **Release**: a semantic version derived from the data contract, release notes written as a contract diff, and an evidence bundle.
- **Monitor**: after every deploy and hourly, a monitor compares the live data with the last good release; on anomaly the pipeline rolls back and an incident is opened.
- **Govern**: one human approval before production, hard rules in AGENTS.md, evidence on every release.

The flow ends by posting a summary of all nine stages on the original issue.

## How we built it

- **GitLab Duo Agent Platform**: a custom flow (`.gitlab/duo/flows/downstream.yaml`) with six `AgentComponent`s and a `HumanInputComponent` approval gate routed on `approve`/`reject`; a generated Hands-off variant; an Assisted MR review flow; triggers on Work item created, Mention, and Merge request created; `AGENTS.md` and `agent-config.yml` for context and runtime.
- **One orchestrator, not a chain**: flow triggers only fire on human actions, so a flow can't hand off to another by merging. Downstream runs one flow whose agents drive GitLab through a small bridge (`scripts/ds.py`): wait for pipelines, auto-merge, press the manual production job, read artefacts, open incidents.
- **GitLab CI/CD**: build, test, package, staging, release, production, monitor stages; `Jobs/SAST`, `Jobs/Secret-Detection`, `Jobs/Dependency-Scanning.v2`; JUnit reports; environments; Releases with `glab`; Pages for dbt docs; generic packages; scheduled monitoring; automatic rollback jobs.
- **Data stack**: dbt + DuckDB, FastAPI dashboard on Cloud Run, synthetic Malaysian e-commerce data.
- **Tests**: 44 dbt data tests and 57 unit tests, including static checks that every flow router, input and tool name is valid.

## Challenges

- Designing around the platform's rule that flows can't trigger flows: the single-orchestrator design keeps every step visible in one session and one pipeline.
- Agents' `run_command` has no shell, so the whole review is one script that writes one report the agent can read, fix from and re-run.
- Releases and registries are outside the flow token's API scope, so deterministic steps live in CI and the flow orchestrates them.
- Found in a live run: the flow token can't merge merge requests (`403 insufficient_scope`). Rather than work around it, the gatekeeper hands that one button to a maintainer on the merge request and waits, which also gives a person a look at every change before it reaches `main`.

## Accomplishments

- The lineage reviewer catches a column rename that breaks two models before anything is built, and fixes it.
- A build that silently loses 90% of orders passes every dbt test; Downstream's monitor catches it and rolls back.
- While building it, the new test-gap gate found a real NULL-revenue bug in our own model.

## What's next

GitLab Orbit over MCP for cross-repository blast radius; carbon-aware scheduling of full refreshes into low-intensity hours; column-level lineage from compiled SQL.

## Most Environmentally Impactful (opt-in text)

Downstream measures and reduces the carbon of every data release with the Green Software
Foundation's Software Carbon Intensity specification (ISO/IEC 21031:2024):
SCI = ((E × I) + M) / R, with R = one release.

- **E**: energy from dbt's own `run_results.json` wall-clock time × (vCPU power interpolated 0.71–4.26 W, memory 0.392 W/GB) × PUE 1.1, using Cloud Carbon Footprint's GCP coefficients.
- **I**: Google Cloud's 2025 location-based grid intensity (asia-southeast1: 366 gCO2e/kWh).
- **M**: embodied emissions of the runner, allocated by time and vCPU share.

**The agent acts on it.** Merge requests rebuild only changed models and their
descendants on top of main's warehouse (slim CI) instead of a full refresh, and every
review compares the two. Measured in this repo: a leaf-model change scores **75% lower
SCI** than a full refresh; a change touching staging is 14% lower because it legitimately
rebuilds most of the graph. Each report adds a what-if for a low-carbon region
(europe-north1 at 30 gCO2e/kWh, a 92% lower operational term for the same build). Every
release carries its SCI in the evidence bundle, so carbon is tracked per release like any
other quality signal.

## Links

- GitLab repository: `<https://gitlab.com/...>`
- Live dashboard (Cloud Run): `<https://...run.app>`
- Demo video: `<https://youtu.be/...>`
