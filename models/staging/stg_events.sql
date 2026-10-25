-- One row per product event.
select
    event_id,
    customer_id,
    event_type,
    event_ts                as event_at,
    cast(event_ts as date)  as event_date
from {{ ref('raw_events') }}
