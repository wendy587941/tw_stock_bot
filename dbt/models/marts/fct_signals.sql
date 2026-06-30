-- 訊號事實表（analyzer 算好的 gainer/loser/active 逐列），Tableau「訊號與殖利率」用。
-- 來源為不過期的 S3 副本（curated/signals），可做歷史趨勢。
select
    trade_date,
    signal_type,
    rank_no,
    code,
    name,
    close_price,
    volume,
    pct_change,
    score
from {{ ref('stg_signals') }}
