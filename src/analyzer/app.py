"""Analyzer Lambda — 每日台股盤勢投資摘要。

由 EventBridge 每日（週一至五 16:00 台北，晚於 ETL 15:30）觸發：
  1) 從 DynamoDB GSI1 撈「當日」+「前一交易日」全市場個股（close/volume/name）
  2) 純 Python 算出確定性訊號與統計（漲跌家數、漲幅/跌幅/成交量前 N 名）
  3) 把訊號寫回 DynamoDB（點亮 GSI2 sparse 訊號索引）
  4) 把「算好的數字」交給 Bedrock Claude 改寫成繁中摘要（grounding：LLM 不碰原始資料、只潤飾 → 防幻覺）
  5) 摘要 + facts 雙寫：DynamoDB（SUMMARY 項目，供 LINE 即時讀）+ S3 marts（gold，供 BI/回溯）

防呆與回補：
  - 非交易日（週末/國定假日）直接跳過，不呼叫 Bedrock（省成本）。
  - event 帶 {"force": true} 繞過交易日檢查；{"trade_date": "YYYY-MM-DD"} 覆寫分析日期。
"""

import datetime as dt
import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

HOT_TABLE = os.environ["HOT_TABLE"]
MARTS_BUCKET = os.environ["MARTS_BUCKET"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
TOP_N = int(os.environ.get("TOP_N", "5"))

# 國定假日等非交易日（YYYY-MM-DD，逗號分隔），由 Terraform env 注入，與 dispatcher 共用同一份清單。
MARKET_HOLIDAYS = {
    d.strip() for d in os.environ.get("MARKET_HOLIDAYS", "").split(",") if d.strip()
}

# 摘要/訊號項目保留約 1 年後由 TTL 自動刪除，控成本（比照 worker hot store）。
TTL_DAYS = 400

TPE = dt.timezone(dt.timedelta(hours=8))

table = boto3.resource("dynamodb").Table(HOT_TABLE)
s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime")


# ── 日期/交易日工具（與 dispatcher 一致的判斷邏輯） ──────────────────────────
def _is_trading_day(d: dt.date) -> bool:
    """週末（六/日）或列在假日清單 → 非交易日。"""
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in MARKET_HOLIDAYS


def _prev_trading_day(d: dt.date) -> dt.date:
    """往前回推最近一個交易日（跳過週末與假日）。"""
    p = d - dt.timedelta(days=1)
    while not _is_trading_day(p):
        p -= dt.timedelta(days=1)
    return p


# ── DynamoDB 讀取 ───────────────────────────────────────────────────────────
def _query_day(trade_date: str) -> list[dict]:
    """用 GSI1 一次撈某交易日全市場個股（自動翻頁）。"""
    items: list[dict] = []
    kwargs = {
        "IndexName": "GSI1",
        "KeyConditionExpression": Key("GSI1PK").eq(f"DATE#{trade_date}"),
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return items
        kwargs["ExclusiveStartKey"] = lek


def _f(v):
    """DynamoDB Decimal → float；None 維持 None。"""
    return float(v) if isinstance(v, Decimal) else v


def _code_of(item: dict) -> str:
    """從 PK（STOCK#2330）取個股代號。"""
    return item["PK"].split("#", 1)[1]


# ── 訊號計算（grounding 的確定性來源） ──────────────────────────────────────
def _build_facts(today: list[dict], prev: list[dict], trade_date: str) -> dict:
    prev_close = {
        _code_of(i): _f(i.get("close"))
        for i in prev
        if _f(i.get("close")) is not None
    }

    rows = []  # 每檔含 pct_change 的整理結果
    advancers = decliners = unchanged = 0
    for i in today:
        close = _f(i.get("close"))
        if close is None:
            continue
        code = _code_of(i)
        volume = _f(i.get("volume")) or 0.0
        name = i.get("name", code)
        pc = prev_close.get(code)
        pct = None
        if pc and pc > 0:
            pct = (close - pc) / pc * 100.0
            if pct > 0:
                advancers += 1
            elif pct < 0:
                decliners += 1
            else:
                unchanged += 1
        rows.append(
            {"code": code, "name": name, "close": close, "volume": volume, "pct_change": pct}
        )

    ranked = [r for r in rows if r["pct_change"] is not None]
    top_gainers = sorted(ranked, key=lambda r: r["pct_change"], reverse=True)[:TOP_N]
    top_losers = sorted(ranked, key=lambda r: r["pct_change"])[:TOP_N]
    most_active = sorted(rows, key=lambda r: r["volume"], reverse=True)[:TOP_N]

    rated = advancers + decliners + unchanged
    return {
        "trade_date": trade_date,
        "total_count": len(rows),
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "breadth_pct": round(advancers / rated * 100.0, 2) if rated else None,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "most_active": most_active,
    }


# ── 寫回 DynamoDB（訊號 sparse index + 摘要） ───────────────────────────────
def _dec(v):
    return Decimal(str(round(v, 4))) if v is not None else None


def _write_signals(facts: dict, trade_date: str, expires: int) -> int:
    """把入選個股寫成訊號項目，點亮 GSI2 sparse 索引。"""
    groups = [
        ("gainer", facts["top_gainers"], lambda r: r["pct_change"]),
        ("loser", facts["top_losers"], lambda r: abs(r["pct_change"])),
        ("active", facts["most_active"], lambda r: r["volume"]),
    ]
    count = 0
    with table.batch_writer() as bw:
        for signal_type, rows, score_of in groups:
            for r in rows:
                score = score_of(r)
                if score is None:
                    continue
                item = {
                    "PK": f"STOCK#{r['code']}",
                    "SK": f"SIGNAL#{trade_date}#{signal_type}",
                    "GSI2PK": f"SIGNAL#{trade_date}",
                    # 固定寬度補零，讓 GSI2SK 可直接做字典序排名（同型別由高到低 ScanIndexForward=False）
                    "GSI2SK": f"{signal_type}#{score:015.4f}#STOCK#{r['code']}",
                    "signal_type": signal_type,
                    "name": r["name"],
                    "close": _dec(r["close"]),
                    "volume": _dec(r["volume"]),
                    "pct_change": _dec(r["pct_change"]),
                    "ExpiresAt": expires,
                }
                bw.put_item(Item={k: v for k, v in item.items() if v is not None})
                count += 1
    return count


# ── Bedrock 摘要生成（LLM 只改寫已算好的數字） ──────────────────────────────
_SYSTEM_PROMPT = (
    "你是專業的台股盤勢分析助理，使用繁體中文。"
    "你只能根據使用者提供的 facts（已由系統精算的數字）撰寫摘要，"
    "嚴禁杜撰、推測或引入任何未在 facts 中出現的數字、個股或事件。"
    "所有數字一律引用 facts 原值。若某類資料缺漏（如無前一交易日資料），請誠實說明而非編造。"
)


def _summary_prompt(facts: dict) -> str:
    return (
        "以下是今日台股盤勢的已精算數據（JSON）。請依據這些數字，"
        "撰寫一份 3–5 段的繁體中文盤勢摘要，涵蓋：整體漲跌氣氛與市場廣度、"
        "漲幅與跌幅領先個股、成交量領先個股。語氣專業、精簡、有依據。\n\n"
        f"```json\n{json.dumps(facts, ensure_ascii=False, indent=2)}\n```"
    )


def _generate_summary(facts: dict) -> str:
    resp = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": _SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": _summary_prompt(facts)}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
    )
    return resp["output"]["message"]["content"][0]["text"].strip()


# ── 進入點 ──────────────────────────────────────────────────────────────────
def handler(event, context):
    event = event or {}
    now = dt.datetime.now(TPE)
    trade_date = event.get("trade_date") or now.strftime("%Y-%m-%d")
    force = bool(event.get("force"))

    d = dt.date.fromisoformat(trade_date)
    if not force and not _is_trading_day(d):
        print(f"skip: {trade_date} is not a trading day")
        return {"trade_date": trade_date, "summarized": False, "skipped": "non_trading_day"}

    today = _query_day(trade_date)
    if not today:
        print(f"abort: no data in hot store for {trade_date}")
        return {"trade_date": trade_date, "summarized": False, "skipped": "empty_source"}

    prev_date = _prev_trading_day(d).isoformat()
    prev = _query_day(prev_date)
    facts = _build_facts(today, prev, trade_date)

    expires = int((now + dt.timedelta(days=TTL_DAYS)).timestamp())
    signal_count = _write_signals(facts, trade_date, expires)

    summary_text = _generate_summary(facts)
    generated_at = now.isoformat()

    # 摘要寫回 DynamoDB（供 LINE 即時讀）。facts 以 JSON 字串存，避免 Decimal 巢狀轉換。
    table.put_item(
        Item={
            "PK": f"SUMMARY#{trade_date}",
            "SK": "DAILY",
            "summary_text": summary_text,
            "facts_json": json.dumps(facts, ensure_ascii=False),
            "model_id": BEDROCK_MODEL_ID,
            "generated_at": generated_at,
            "ExpiresAt": expires,
        }
    )

    # 摘要 + facts 寫 S3 marts（gold，不過期，供 BI/回溯）
    s3.put_object(
        Bucket=MARTS_BUCKET,
        Key=f"marts/daily_summary/date={trade_date}/summary.json",
        Body=json.dumps(
            {
                "trade_date": trade_date,
                "generated_at": generated_at,
                "model_id": BEDROCK_MODEL_ID,
                "facts": facts,
                "summary_text": summary_text,
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        ContentType="application/json",
    )

    print(
        f"summarized {trade_date}: total={facts['total_count']} "
        f"adv={facts['advancers']} dec={facts['decliners']} signals={signal_count}"
    )
    return {
        "trade_date": trade_date,
        "summarized": True,
        "total_count": facts["total_count"],
        "signal_count": signal_count,
    }
