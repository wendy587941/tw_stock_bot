"""ETL Dispatcher Lambda。

由 EventBridge 每日（週一至五 15:30 台北）觸發：抓取 TWSE 全市場當日行情，
逐檔 fan-out 進 ingest queue，交由 worker 平行處理。
此處只負責「取得清單並派工」，不做轉換與寫入。

資料源：MI_INDEX（每日收盤行情，type=ALLBUT0999）。此端點必須帶 date 參數，
且回傳自帶 date 欄位；休市日（含颱風假等臨時休市）直接回 stat != OK。

防呆設計（核心原則：交易日由「資料來源」認定，不由系統時鐘推斷）：
  - 向來源指名索取 trade_date 當天的資料。查無資料 → 當天沒開盤（或尚未發布）→ 不派工。
    颱風假、臨時休市因此自動被擋掉，毋須維護一張永遠追不上的假日清單。
  - 來源自報的 date 必須與 trade_date 相符，否則中止：這道交叉檢查讓
    「把別天的行情蓋上今天日期」在結構上不可能發生。
  - MARKET_HOLIDAYS 僅作為省一次 HTTP 呼叫的快速短路，不再是正確性的依據。
  - 手動回補/測試：event 帶 {"trade_date": "YYYY-MM-DD"} 即回補該日（來源會給出該日真實行情）；
    若該日列在 MARKET_HOLIDAYS，需另帶 {"force": true} 繞過短路。
"""

import datetime as dt
import json
import os
import re
import urllib.request

import boto3

QUEUE_URL = os.environ["INGEST_QUEUE_URL"]

# 國定假日等非交易日（YYYY-MM-DD，逗號分隔），由 Terraform env 注入，免改碼即可逐年維護。
MARKET_HOLIDAYS = {
    d.strip() for d in os.environ.get("MARKET_HOLIDAYS", "").split(",") if d.strip()
}

# TWSE 每日收盤行情：ALLBUT0999 = 全部（不含權證、牛熊證、可展延牛熊證）
TWSE_MI_INDEX = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"

# MI_INDEX 的個股表以 array 回傳，欄位順序見同表 fields。
_COL_CODE, _COL_NAME = 0, 1
_COL_VOLUME, _COL_TRANSACTION, _COL_VALUE = 2, 3, 4
_COL_OPEN, _COL_HIGH, _COL_LOW, _COL_CLOSE = 5, 6, 7, 8
_COL_SIGN, _COL_DIFF = 9, 10
_MIN_COLS = _COL_DIFF + 1

# 個股表的辨識依據：欄位定義比中文標題穩定（標題含年度與全形括號）。
_FIRST_FIELD = "證券代號"

_TAG_RE = re.compile(r"<[^>]*>")

TPE = dt.timezone(dt.timedelta(hours=8))
sqs = boto3.client("sqs")


def _taipei_now() -> dt.datetime:
    """台北時間（UTC+8）當下時刻。"""
    return dt.datetime.now(TPE)


def _is_trading_day(d: dt.date) -> bool:
    """週末（六/日）或列在假日清單 → 非交易日。

    僅供省下一次 HTTP 呼叫；真正的認定權在資料來源（見 _fetch_day）。
    """
    if d.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    return d.isoformat() not in MARKET_HOLIDAYS


def _parse_change(sign_html: str, diff: str) -> str:
    """漲跌欄位還原成帶正負號的字串。

    來源把方向與幅度拆成兩欄：方向欄是 HTML（如 <p style='color:red'>+</p>、
    <p>X</p> 表除權息），幅度欄恆為非負數。無法判讀時回空字串。
    """
    sign = _TAG_RE.sub("", sign_html or "").strip()
    value = (diff or "").strip().replace(",", "")
    if not value:
        return ""
    try:
        magnitude = abs(float(value))
    except ValueError:
        return ""
    return f"{-magnitude if sign == '-' else magnitude:.4f}"


def _normalize(row: list) -> dict | None:
    """MI_INDEX 的 array 列 → 與舊來源 STOCK_DAY_ALL 相同的欄位名。

    如此 worker 以降的下游完全不必因換來源而改動。
    """
    if len(row) < _MIN_COLS:
        return None
    code = (row[_COL_CODE] or "").strip()
    if not code:
        return None
    return {
        "Code": code,
        "Name": (row[_COL_NAME] or "").strip(),
        "TradeVolume": row[_COL_VOLUME],
        "TradeValue": row[_COL_VALUE],
        "OpeningPrice": row[_COL_OPEN],
        "HighestPrice": row[_COL_HIGH],
        "LowestPrice": row[_COL_LOW],
        "ClosingPrice": row[_COL_CLOSE],
        "Change": _parse_change(row[_COL_SIGN], row[_COL_DIFF]),
        "Transaction": row[_COL_TRANSACTION],
    }


def _fetch_day(d: dt.date) -> tuple[str, list[dict]] | None:
    """指名索取 d 當日行情。休市/尚未發布 → None。

    回傳 (來源自報的日期 ISO, 正規化後的個股列表)。
    """
    url = f"{TWSE_MI_INDEX}?date={d.strftime('%Y%m%d')}&type=ALLBUT0999&response=json"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "tw-stock-bot/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)

    # 休市日回 stat="很抱歉，沒有符合條件的資料!"，且無 date 欄位。
    if payload.get("stat") != "OK":
        return None

    raw_date = (payload.get("date") or "").strip()
    if len(raw_date) != 8 or not raw_date.isdigit():
        return None
    source_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"

    for table in payload.get("tables") or []:
        fields = table.get("fields") or []
        if not fields or fields[0] != _FIRST_FIELD:
            continue
        rows = [_normalize(r) for r in table.get("data") or []]
        return source_date, [r for r in rows if r]

    return None


def handler(event, context):
    event = event or {}
    now = _taipei_now()
    trade_date = event.get("trade_date") or now.strftime("%Y-%m-%d")
    force = bool(event.get("force"))

    d = dt.date.fromisoformat(trade_date)

    # 快速短路①：週末/已知國定假日不必打 API。force=true 可繞過（回補/測試）。
    if not force and not _is_trading_day(d):
        print(f"skip: {trade_date} is not a trading day (weekend/holiday)")
        return {"trade_date": trade_date, "dispatched": 0, "skipped": "non_trading_day"}

    fetched = _fetch_day(d)

    # 防呆①：來源查無當日資料 → 沒開盤（颱風假/臨時休市）或盤後尚未發布 → 不派工。
    if fetched is None:
        print(f"skip: TWSE has no data for {trade_date} (market closed or not published)")
        return {"trade_date": trade_date, "dispatched": 0, "skipped": "market_closed"}

    source_date, stocks = fetched

    # 防呆②：來源自報日期須與派工日期一致，否則寧可不寫也不寫錯日期。
    if source_date != trade_date:
        print(f"abort: source date {source_date} != requested {trade_date}")
        return {
            "trade_date": trade_date,
            "dispatched": 0,
            "skipped": "source_date_mismatch",
            "source_date": source_date,
        }

    # 防呆③：來源回空清單（結構改版或解析失效）→ 不派工，回報異常讓監控接手。
    if not stocks:
        print(f"abort: no parsable rows for {trade_date}")
        return {"trade_date": trade_date, "dispatched": 0, "skipped": "empty_source"}

    sent = 0
    batch: list[dict] = []
    for item in stocks:
        # 交易日取自來源自報值（非系統時鐘），供 worker 做分區與主鍵
        item["TradeDate"] = source_date
        batch.append(
            {"Id": item["Code"], "MessageBody": json.dumps(item, ensure_ascii=False)}
        )

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
