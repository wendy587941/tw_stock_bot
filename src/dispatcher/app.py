"""ETL Dispatcher Lambda。

由 EventBridge 每日觸發：抓取 TWSE 全市場當日行情，逐檔 fan-out 進 ingest queue，
交由 worker 平行處理。此處只負責「取得清單並派工」，不做轉換與寫入。
"""

import datetime as dt
import json
import os
import urllib.request

import boto3

QUEUE_URL = os.environ["INGEST_QUEUE_URL"]

# TWSE OpenAPI：一次回傳全上市公司當日 OHLCV（主資料源）
TWSE_STOCK_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"

sqs = boto3.client("sqs")


def _taipei_today() -> str:
    """以台北時間（UTC+8）取當日日期字串。API 回傳的是最近一個交易日資料。"""
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    return now.strftime("%Y-%m-%d")


def _fetch_all_stocks() -> list[dict]:
    req = urllib.request.Request(
        TWSE_STOCK_DAY_ALL, headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def handler(event, context):
    trade_date = _taipei_today()
    stocks = _fetch_all_stocks()

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
