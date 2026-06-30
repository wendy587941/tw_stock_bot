-- 每日漲跌幅前 10 名（Tableau「市場總覽」Top movers 長條圖）。
-- 由 OHLCV 重算，與 analyzer 訊號互補（此處不依賴訊號是否入湖）。
with ranked as (
    select
        trade_date,
        code,
        name,
        close_price,
        pct_change,
        volume,
        row_number() over (partition by trade_date order by pct_change desc) as gain_rank,
        row_number() over (partition by trade_date order by pct_change asc)  as lose_rank
    from {{ ref('fct_daily_ohlcv') }}
    where pct_change is not null
)

select
    trade_date,
    code,
    name,
    close_price,
    pct_change,
    volume,
    case when gain_rank <= 10 then 'gainer' else 'loser' end as mover_type,
    case when gain_rank <= 10 then gain_rank else lose_rank end as rank_no
from ranked
where gain_rank <= 10 or lose_rank <= 10
