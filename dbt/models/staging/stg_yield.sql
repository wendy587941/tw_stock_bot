-- 清洗殖利率排行逐列。rank 為 Trino 保留字，需雙引號。
select
    cast(trade_date as date) as trade_date,
    "rank"                   as rank_no,
    code,
    name,
    dividend_yield,
    pe_ratio,
    pb_ratio
from {{ source('silver', 'yield_ranking') }}
