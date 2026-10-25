-- Monthly revenue by country, excluding internal test accounts.
select
    cast(date_trunc('month', o.order_date) as date)                         as order_month,
    c.country,
    count(*) filter (where o.order_status = 'completed')                     as completed_orders,
    coalesce(sum(o.amount_myr) filter (where o.order_status = 'completed'), 0) as gross_revenue_myr,
    coalesce(sum(o.amount_myr) filter (where o.order_status = 'refunded'), 0) as refunded_myr,
    coalesce(sum(o.amount_myr) filter (where o.order_status = 'completed'), 0)
      - coalesce(sum(o.amount_myr) filter (where o.order_status = 'refunded'), 0)
                                                                            as net_revenue_myr
from {{ ref('stg_orders') }} o
join {{ ref('stg_customers') }} c using (customer_id)
where not c.is_test_account
group by 1, 2
