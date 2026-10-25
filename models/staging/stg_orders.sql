-- One row per order, typed and renamed.
select
    order_id,
    customer_id,
    order_date,
    status      as order_status,
    amount_myr
from {{ ref('raw_orders') }}
{% if var('inject_anomaly', false) %}
-- Demo only: simulate an upstream extract that silently lost ~90% of rows.
where order_id % 10 = 0
{% endif %}
