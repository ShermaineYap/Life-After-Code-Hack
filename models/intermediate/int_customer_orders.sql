-- Lifetime order facts per customer (completed orders only).
select
    customer_id,
    min(order_date)             as first_order_date,
    max(order_date)             as last_order_date,
    count(*)                    as lifetime_orders,
    sum(amount_myr)             as lifetime_revenue_myr
from {{ ref('stg_orders') }}
where order_status = 'completed'
group by customer_id
