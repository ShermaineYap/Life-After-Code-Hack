-- Singular test: revenue must count only real customers' completed orders.
-- If a test account leaks in, fct_revenue's order total exceeds this count.
with expected as (
    select count(*) as n
    from {{ ref('stg_orders') }} o
    join {{ ref('stg_customers') }} c using (customer_id)
    where not c.is_test_account and o.order_status = 'completed'
),
actual as (
    select coalesce(sum(completed_orders), 0) as n from {{ ref('fct_revenue') }}
)
select expected.n as expected_orders, actual.n as actual_orders
from expected, actual
where expected.n <> actual.n
