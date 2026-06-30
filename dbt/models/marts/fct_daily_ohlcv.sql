-- 個股每日事實表（含均線/漲跌幅），Tableau「個股走勢」儀表板主資料表。
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
    pct_change,
    ma5,
    ma20
from {{ ref('int_ohlcv_with_ma') }}
