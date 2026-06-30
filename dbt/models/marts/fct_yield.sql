-- 殖利率排行事實表（dividend_ingest 算好的逐列排行），Tableau「訊號與殖利率」用。
-- 來源為不過期的 S3 副本（curated/yield），可做歷史趨勢。
select
    trade_date,
    rank_no,
    code,
    name,
    dividend_yield,
    pe_ratio,
    pb_ratio
from {{ ref('stg_yield') }}
