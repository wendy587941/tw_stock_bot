-- 衍生技術指標：前日收盤、漲跌幅、MA5/MA20（依個股分組、交易日排序的視窗函數）。
-- ephemeral：內聯為 CTE，不在 Glue 產生中間物件。
with base as (
    select * from {{ ref('stg_ohlcv') }}
),

with_lag as (
    select
        *,
        lag(close_price) over (
            partition by code order by trade_date
        ) as prev_close
    from base
)

select
    code,
    name,
    trade_date,
    open_price,
    high_price,
    low_price,
    close_price,
    volume,
    prev_close,
    case
        when prev_close is not null and prev_close > 0
            then (close_price - prev_close) / prev_close * 100.0
    end as pct_change,
    avg(close_price) over (
        partition by code order by trade_date
        rows between 4 preceding and current row
    ) as ma5,
    avg(close_price) over (
        partition by code order by trade_date
        rows between 19 preceding and current row
    ) as ma20
from with_lag
