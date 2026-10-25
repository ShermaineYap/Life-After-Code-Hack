-- Build metadata read by the dashboard's /health endpoint and the monitor.
select
    '{{ invocation_id }}'                                    as invocation_id,
    cast('{{ run_started_at }}' as timestamp)                as built_at,
    '{{ env_var("CI_COMMIT_SHORT_SHA", "local") }}'          as git_sha,
    '{{ env_var("RELEASE_VERSION", "dev") }}'                as release_version,
    (select count(*) from {{ ref('stg_orders') }})           as order_rows,
    (select max(order_date) from {{ ref('stg_orders') }})    as max_order_date
