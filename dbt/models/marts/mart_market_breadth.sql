-- 市場廣度（每日漲跌家數/成交量/平均漲跌幅），Tableau「市場總覽」KPI 與趨勢用。
select
    trade_date,
    count(*)                                                  as total_count,
    sum(case when pct_change > 0 then 1 else 0 end)           as advancers,
    sum(case when pct_change < 0 then 1 else 0 end)           as decliners,
    sum(case when pct_change = 0 then 1 else 0 end)           as unchanged,
    sum(volume)                                               as total_volume,
    round(avg(pct_change), 4)                                 as avg_pct_change,
    round(
        cast(sum(case when pct_change > 0 then 1 else 0 end) as double)
        / nullif(sum(case when pct_change is not null then 1 else 0 end), 0) * 100.0,
        2
    )                                                         as breadth_pct
from {{ ref('fct_daily_ohlcv') }}
group by trade_date
