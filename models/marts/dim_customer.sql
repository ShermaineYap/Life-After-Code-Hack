-- Customer dimension used by the dashboard's customer table.
select
    c.customer_id,
    c.customer_name,
    c.email_hash,
    c.country,
    c.signup_date,
    c.is_test_account,
    o.first_order_date,
    o.last_order_date,
    coalesce(o.lifetime_orders, 0)          as lifetime_orders,
    coalesce(o.lifetime_revenue_myr, 0)     as lifetime_revenue_myr,
    case
        when coalesce(o.lifetime_revenue_myr, 0) >= 5000 then 'vip'
        when coalesce(o.lifetime_orders, 0) >= 3 then 'repeat'
        when coalesce(o.lifetime_orders, 0) >= 1 then 'one_time'
        else 'prospect'
    end                                     as customer_segment
from {{ ref('stg_customers') }} c
left join {{ ref('int_customer_orders') }} o using (customer_id)
