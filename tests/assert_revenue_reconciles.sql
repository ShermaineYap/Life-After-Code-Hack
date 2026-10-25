-- Singular test: the KPI table's revenue must equal the sum of fct_revenue.
select 1
from (select round(sum(net_revenue_myr), 2) as total from {{ ref('kpi_dashboard') }}) k,
     (select round(sum(net_revenue_myr), 2) as total from {{ ref('fct_revenue') }}) f
where abs(k.total - f.total) > 0.05
