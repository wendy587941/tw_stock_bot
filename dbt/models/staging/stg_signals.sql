-- 清洗訊號逐列。rank/close 為 Trino 保留字，需雙引號。
select
    cast(trade_date as date) as trade_date,
    signal_type,
    "rank"                   as rank_no,
    code,
    name,
    "close"                  as close_price,
    volume,
    pct_change,
    score
from {{ source('silver', 'signals') }}
