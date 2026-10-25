# Demo video script (target 2:50, hard limit under 3:00)

Record the real GitLab UI, not slides. One beat per lifecycle stage. No music, no
third-party logos. Speak slowly; cut the waiting, never fake output.

| Time | On screen | Voice-over |
| --- | --- | --- |
| 0:00–0:15 | The KPI dashboard, then a Slack-style note: "who broke revenue?" | "For data teams, code isn't the hard part. What breaks is everything after the commit: a renamed column, a missing test, a leaked email, a dashboard nobody watched. Downstream runs that lifecycle on GitLab." |
| 0:15–0:25 | Create demo issue 1 (`demo/issues.md`) | "One human action: a growth manager asks for monthly active customers, and slips in a column rename." |
| 0:25–0:45 | AI > Sessions: the flow starts. Triage comment with acceptance criteria and test plan. The merge request opens | "The triage agent writes a testable plan. The developer agent implements it and opens a merge request." |
| 0:45–1:15 | The lineage reviewer's MR note: blast radius, the rename breaking `fct_revenue` and `int_customer_orders`, the 4 dashboard tiles; then its fix commit and added tests | "The reviewer reads the lineage graph, not just the diff. It catches that the rename would break revenue reporting, fixes both models, and adds the tests nobody wrote." |
| 1:15–1:30 | MR pipeline: lineage, slim dbt build, dbt tests, SAST, secrets, dependencies, PII Sentinel, all green | "Every change is scanned, including a PII check that reads the data, not just column names." |
| 1:30–1:45 | SCI table in the MR note | "It rebuilds only what changed on top of main's warehouse and scores each build's software carbon intensity against a full refresh." |
| 1:45–2:00 | The gatekeeper's "please press Merge" note; click Merge. Main pipeline: package, staging deploy, staging monitor, release v1.1.0 page with the data-contract diff and evidence | "A person presses Merge. Then the image and tested warehouse are packaged, staging is deployed and monitored, and only then is a release cut, with a contract diff and evidence." |
| 2:00–2:15 | The approval prompt in the session; click Approve; deploy-production runs; dashboard shows the new tile | "One approval. Production deploys. The new tile is live." |
| 2:15–2:40 | Demo 3: a run with `DEMO_INJECT_ANOMALY`; every dbt test passes; monitor-staging fails 3,297 → 329; rollback-staging; incident | "And when an upstream extract silently loses 90% of its rows, every row-level test still passes. The monitor compares against the last good release, rolls staging back, and opens an incident. Production never sees it." |
| 2:40–2:50 | The final summary comment on the issue, all nine stages listed | "Plan to monitor, nine stages, one flow. Downstream, built on GitLab Duo Agent Platform." |

Recording tips

- Record each beat separately at 1080p; stitch in any editor (Clipchamp is free on Windows).
- Zoom the browser to 125% so text is readable.
- Pre-run demo 3 before recording so you're not waiting on camera; show the real job logs.
- Upload to YouTube as **Public**, title: "Downstream: the post-code lifecycle for data pipelines on GitLab Duo".
