-- 清洗 OHLCV：型別轉換 + 排除停市股（close<=0，比照 analyzer 的 _build_facts 規則）。
-- open/close 為 Trino 保留字，需雙引號；date 為分區鍵亦保留字。
with src as (
    select * from {{ source('silver', 'silver_ohlcv') }}
)
select
    code,
    name,
    cast(trade_date as date) as trade_date,
    "open"                   as open_price,
    high                     as high_price,
    low                      as low_price,
    "close"                  as close_price,
    volume,
    cast("date" as date)     as partition_date
from src
where "close" is not null
  and "close" > 0
