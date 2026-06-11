"""ETL Dispatcher Lambda。

由 EventBridge 每日（週一至五 15:30 台北）觸發：抓取 TWSE 全市場當日行情，
逐檔 fan-out 進 ingest queue，交由 worker 平行處理。
此處只負責「取得清單並派工」，不做轉換與寫入。

防呆設計：
  - STOCK_DAY_ALL 回傳的是「最近一個交易日」資料、且各筆不含日期；若在非交易日
    （週末/國定假日）執行，會把上一交易日的數字蓋上今天日期寫成髒資料 → 故先擋掉。
  - 來源回空清單（API 異常或尚未發布）→ 不派工，回報 skip 讓監控接手。
  - 手動回補/測試：event 帶 {"force": true} 可繞過交易日檢查；
    帶 {"trade_date": "YYYY-MM-DD"} 可覆寫派工日期。
"""

import datetime as dt
import json
import os
import urllib.request

import boto3

QUEUE_URL = os.environ["INGEST_QUEUE_URL"]

# 國定假日等非交易日（YYYY-MM-DD，逗號分隔），由 Terraform env 注入，免改碼即可逐年維護。
MARKET_HOLIDAYS = {
    d.strip() for d in os.environ.get("MARKET_HOLIDAYS", "").split(",") if d.strip()
}

# TWSE OpenAPI：一次回傳全上市公司當日 OHLCV（主資料源）
TWSE_STOCK_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"

TPE = dt.timezone(dt.timedelta(hours=8))
sqs = boto3.client("sqs")


def _taipei_now() -> dt.datetime:
    """台北時間（UTC+8）當下時刻。"""
    return dt.datetime.now(TPE)


def _is_trading_day(d: dt.date) -> bool:
    """週末（六/日）或列在假日清單 → 非交易日。"""
    if d.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    return d.isoformat() not in MARKET_HOLIDAYS


def _fetch_all_stocks() -> list[dict]:
    req = urllib.request.Request(
        TWSE_STOCK_DAY_ALL, headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def handler(event, context):
    event = event or {}
    now = _taipei_now()
    trade_date = event.get("trade_date") or now.strftime("%Y-%m-%d")
    force = bool(event.get("force"))

    # 防呆①：非交易日直接跳過，避免舊資料蓋今天日期寫成髒資料。force=true 可繞過（回補/測試）。
    if not force and not _is_trading_day(now.date()):
        print(f"skip: {trade_date} is not a trading day (weekend/holiday)")
        return {"trade_date": trade_date, "dispatched": 0, "skipped": "non_trading_day"}

    stocks = _fetch_all_stocks()

    # 防呆②：來源空清單（API 異常或尚未發布）→ 不派工，回報異常讓監控接手。
    if not stocks:
        print(f"abort: STOCK_DAY_ALL returned empty for {trade_date}")
        return {"trade_date": trade_date, "dispatched": 0, "skipped": "empty_source"}

    sent = 0
    batch: list[dict] = []
    for item in stocks:
        code = item.get("Code")
        if not code:
            continue
        # 補上交易日（STOCK_DAY_ALL 各筆不含日期），供 worker 做分區與主鍵
        item["TradeDate"] = trade_date
        batch.append({"Id": code, "MessageBody": json.dumps(item, ensure_ascii=False)})

        # SQS SendMessageBatch 單批上限 10 則
        if len(batch) == 10:
            sqs.send_message_batch(QueueUrl=QUEUE_URL, Entries=batch)
            sent += len(batch)
            batch = []

    if batch:
        sqs.send_message_batch(QueueUrl=QUEUE_URL, Entries=batch)
        sent += len(batch)

    print(f"dispatched {sent} stock messages for {trade_date}")
    return {"trade_date": trade_date, "dispatched": sent}
