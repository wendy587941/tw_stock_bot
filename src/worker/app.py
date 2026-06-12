"""ETL Worker Lambda。

由 ingest queue（SQS）觸發，每批處理多檔個股：
  - Bronze：原始 JSON 原汁原味落地 S3（raw bucket）
  - Silver：清洗數值欄位轉 Parquet 落地 S3（curated bucket）
  - Hot   ：寫入 DynamoDB 供 < 10ms 點查（含 TTL 控成本）

回傳 batchItemFailures，讓單筆失敗只重投該筆，不整批重來（ReportBatchItemFailures）。
"""

import datetime as dt
import io
import json
import os
from decimal import Decimal

import boto3
import pandas as pd

RAW_BUCKET = os.environ["RAW_BUCKET"]
CURATED_BUCKET = os.environ["CURATED_BUCKET"]
HOT_TABLE = os.environ["HOT_TABLE"]

# hot store 原始特徵保留約 1 年後由 TTL 自動刪除，避免無限累積成本
TTL_DAYS = 400

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(HOT_TABLE)


def _to_float(v):
    """TWSE 數值欄位可能含千分位逗號或為 '--'（無交易），無法解析則回 None。"""
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, AttributeError, TypeError):
        return None


def _dec(v):
    return Decimal(str(v)) if v is not None else None


def _process(body: str) -> None:
    data = json.loads(body)
    code = data["Code"]
    trade_date = data["TradeDate"]
    prefix = f"date={trade_date}/{code}"

    # --- Bronze：原始 JSON ---
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=f"raw/{prefix}.json",
        Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    # --- Silver：清洗後 Parquet ---
    row = {
        "code": code,
        "name": data.get("Name"),
        "trade_date": trade_date,
        "open": _to_float(data.get("OpeningPrice")),
        "high": _to_float(data.get("HighestPrice")),
        "low": _to_float(data.get("LowestPrice")),
        "close": _to_float(data.get("ClosingPrice")),
        "volume": _to_float(data.get("TradeVolume")),
    }
    buf = io.BytesIO()
    pd.DataFrame([row]).to_parquet(buf, engine="pyarrow", index=False)
    s3.put_object(
        Bucket=CURATED_BUCKET, Key=f"curated/{prefix}.parquet", Body=buf.getvalue()
    )

    # --- Hot：DynamoDB 點查 ---
    expires = int(
        (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=TTL_DAYS)).timestamp()
    )
    item = {
        "PK": f"STOCK#{code}",
        "SK": f"DATE#{trade_date}",
        "GSI1PK": f"DATE#{trade_date}",
        "GSI1SK": f"STOCK#{code}",
        "name": row["name"],  # 供 analyzer 產生可讀摘要（台積電 +3.2% 而非 2330 +3.2%）
        "close": _dec(row["close"]),
        "volume": _dec(row["volume"]),
        "ExpiresAt": expires,
    }
    # DynamoDB 不接受 None 屬性，移除無交易而解析失敗的欄位
    table.put_item(Item={k: v for k, v in item.items() if v is not None})


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            _process(record["body"])
        except Exception as exc:  # 單筆失敗不影響整批
            print(f"failed message {record.get('messageId')}: {exc}")
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}
