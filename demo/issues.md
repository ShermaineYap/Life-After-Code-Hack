# Demo issues

Copy these into GitLab issues word for word when recording. Each one exercises a
different part of the lifecycle. Run them in this order on a fresh project, after
one normal release has gone to production (so a monitor baseline exists).

## 1. The happy path, with a trap (main demo)

**Title:** Add monthly active customers to the KPI dashboard

**Description:**

> Growth wants to see monthly active customers next to revenue on the KPI dashboard.
>
> - An active customer is a real customer (not a test account) who had at least one
>   product event or a completed order in that month.
> - Add it as a new column `active_customers` on `kpi_dashboard` and as its own mart
>   so other teams can reuse it.
> - While you're in there, `amount_myr` in `stg_orders` should be called `order_amount_myr`
>   to match the finance glossary.
>
> Done when the dashboard shows the number for September 2026.

What should happen: triage writes a plan; the developer adds
`fct_monthly_active_customers` and renames the column; (a verified correct end state is
`demo/monthly-active-customers.patch`: `git apply demo/monthly-active-customers.patch`); the **lineage reviewer catches that the rename breaks `fct_revenue` and
`int_customer_orders`**, fixes both, adds the missing key tests, and posts the SCI
comparison; CI goes green; the gatekeeper merges; staging deploys and passes its
monitor; release `v1.1.0` is cut with a data-contract changelog; you approve
production; the dashboard shows the new tile.

## 2. Personal data leak (Assisted review flow)

Open a merge request yourself (not through the flow) that adds this to
`models/marts/dim_customer.sql`'s select list:

```sql
    c.customer_name || ' <' || r.email || '>' as contact_label,
```

with `left join {{ ref('raw_customers') }} r using (customer_id)`.
Assign the Downstream Review service account as reviewer. The PII Sentinel fails the
pipeline (the values look like email addresses even though the column name doesn't),
and the review flow leaves a suggestion that removes it.

## 3. Silent data loss and rollback

Run a pipeline on `main` from **Build > Pipelines > Run pipeline** with the variable
`DEMO_INJECT_ANOMALY = true`. Every dbt test passes (each row is still valid), but the
build keeps only 10% of orders. `monitor-staging` compares against the last good
release, fails on the row-count collapse, and `rollback-staging` restores the previous
Cloud Run revision. No release is cut and production is never touched.
