-- One row per month: the headline numbers on the KPI dashboard.
with revenue as (
    select order_month, sum(completed_orders) as orders, sum(net_revenue_myr) as net_revenue_myr
    from {{ ref('fct_revenue') }}
    group by order_month
),
new_customers as (
    select cast(date_trunc('month', first_order_date) as date) as order_month,
           count(*) as new_customers
    from {{ ref('dim_customer') }}
    where first_order_date is not null and not is_test_account
    group by 1
)
select
    r.order_month,
    r.orders,
    round(r.net_revenue_myr, 2)                                  as net_revenue_myr,
    coalesce(n.new_customers, 0)                                 as new_customers,
    round(r.net_revenue_myr / nullif(r.orders, 0), 2)            as avg_order_value_myr
from revenue r
left join new_customers n using (order_month)
