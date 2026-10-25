-- One row per customer. Raw email and phone stop here: only a salted hash of
-- the email leaves staging, so no mart can expose contact details.
select
    customer_id,
    full_name                                   as customer_name,
    sha256(lower(trim(email)) || 'downstream')  as email_hash,
    country,
    signup_date,
    is_test_account
from {{ ref('raw_customers') }}
