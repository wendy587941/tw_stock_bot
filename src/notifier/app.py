"""Notifier Lambda — 每日台股盤勢摘要 LINE 推播。

由 EventBridge 每日（週一至五 16:30 台北，晚於 analyzer 16:00 約 30 分鐘確保摘要已落地）觸發：
  1) 解析 trade_date（台北今日；交易日防呆，假日 skip 不推空訊息）
  2) 從 DynamoDB GetItem PK=SUMMARY#{date}, SK=DAILY → 取 summary_text(+facts_json)
  3) 組 LINE 純文字訊息（facts 標題列 + 摘要本文；過長截斷）
  4) 從 SSM 讀 channel access token → urllib 呼叫 LINE API：
       - 預設 broadcast（推給所有 OA followers；自用 bot 通常只有使用者一人，免取 userId）
       - 若 SSM 另設 push_target（U/C/R 開頭 id）→ 自動改用 push 指定對象推播（向後相容）
  5) 回傳 {pushed, trade_date, mode}

grounding：標題列數字一律引用 analyzer 算好的 facts，notifier 不重算、不交 LLM。
防呆與回補：
  - 非交易日（週末/國定假日）直接跳過，不呼叫 LINE。
  - 找不到當日摘要 → 不推空訊息，回 skipped:no_summary（供 Week 6 CloudWatch 告警捕捉）。
  - event 帶 {"force": true} 繞過交易日檢查；{"trade_date": "YYYY-MM-DD"} 覆寫推播日期。
"""

import datetime as dt
import json
import os
import urllib.error
import urllib.request

import boto3

HOT_TABLE = os.environ["HOT_TABLE"]
# SSM SecureString/String 參數路徑前綴（如 /wendy-tw-stock-bot/dev/line），token/target 不入版控。
SSM_PREFIX = os.environ["SSM_PREFIX"].rstrip("/")

# 國定假日等非交易日（YYYY-MM-DD，逗號分隔），由 Terraform env 注入，與 dispatcher/analyzer 共用同一份清單。
MARKET_HOLIDAYS = {
    d.strip() for d in os.environ.get("MARKET_HOLIDAYS", "").split(",") if d.strip()
}

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
# LINE 單則 text 上限 5000 字元；保守在 4900 截斷本文，預留標題列空間。
LINE_TEXT_LIMIT = 4900

# 台北時區與星期中文（weekday(): 週一=0 … 週日=6）
TPE = dt.timezone(dt.timedelta(hours=8))
_WEEKDAY_TW = ["一", "二", "三", "四", "五", "六", "日"]

table = boto3.resource("dynamodb").Table(HOT_TABLE)
ssm = boto3.client("ssm")


# ── 日期/交易日工具（與 dispatcher/analyzer 一致的判斷邏輯） ──────────────────
def _is_trading_day(d: dt.date) -> bool:
    """週末（六/日）或列在假日清單 → 非交易日。"""
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in MARKET_HOLIDAYS


# ── 設定讀取（SSM；token 用 SecureString，需解密） ──────────────────────────
def _load_config() -> tuple[str, str | None]:
    """取回 channel access token（必填）與 push_target（選填）。token 不經過 Terraform state。

    push_target 未設定（broadcast 模式）時，SSM 會把它列在 InvalidParameters 而非 Parameters，
    視為 None 即可，不視為錯誤。
    """
    resp = ssm.get_parameters(
        Names=[f"{SSM_PREFIX}/channel_access_token", f"{SSM_PREFIX}/push_target"],
        WithDecryption=True,
    )
    values = {p["Name"].rsplit("/", 1)[1]: p["Value"] for p in resp["Parameters"]}
    if "channel_access_token" not in values:
        raise RuntimeError(f"missing SSM parameter {SSM_PREFIX}/channel_access_token")
    target = (values.get("push_target") or "").strip() or None
    return values["channel_access_token"], target


# ── 訊息組裝（標題列數字一律引用 analyzer facts，不重算） ────────────────────
def _build_message(trade_date: str, summary_text: str, facts: dict) -> str:
    weekday = _WEEKDAY_TW[dt.date.fromisoformat(trade_date).weekday()]
    header = f"📊 台股盤勢摘要｜{trade_date}（{weekday}）"

    adv, dec, unch = facts.get("advancers"), facts.get("decliners"), facts.get("unchanged")
    breadth = facts.get("breadth_pct")
    if adv is not None and dec is not None:
        stat = f"漲 {adv}　跌 {dec}　平 {unch}"
        if breadth is not None:
            stat += f"　市場廣度 {breadth}%"
        header = f"{header}\n{stat}"

    body = summary_text.strip()
    # 防呆：標題 + 分隔線 + 本文超過上限則截斷本文
    fixed = f"{header}\n───────────────\n"
    if len(fixed) + len(body) > LINE_TEXT_LIMIT:
        body = body[: LINE_TEXT_LIMIT - len(fixed) - 12].rstrip() + "…（全文見資料庫）"
    return fixed + body


# ── LINE Messaging API（標準庫 urllib 直呼，不裝 SDK） ──────────────────────
def _send(token: str, target: str | None, text: str) -> str:
    """有 target → push 指定對象；否則 broadcast 給所有 followers。回傳實際使用的模式。"""
    message = {"type": "text", "text": text}
    if target:
        url, body, mode = LINE_PUSH_URL, {"to": target, "messages": [message]}, f"push:{target}"
    else:
        url, body, mode = LINE_BROADCAST_URL, {"messages": [message]}, "broadcast"

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        # 401 token 失效 / 429 額度 / 5xx → 讀錯誤內文後 raise，讓 Lambda 標記失敗（供告警）
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"LINE {mode} failed: HTTP {e.code} {detail}") from e
    return mode


# ── 進入點 ──────────────────────────────────────────────────────────────────
def handler(event, context):
    event = event or {}
    now = dt.datetime.now(TPE)
    trade_date = event.get("trade_date") or now.strftime("%Y-%m-%d")
    force = bool(event.get("force"))

    d = dt.date.fromisoformat(trade_date)
    if not force and not _is_trading_day(d):
        print(f"skip: {trade_date} is not a trading day")
        return {"trade_date": trade_date, "pushed": False, "skipped": "non_trading_day"}

    resp = table.get_item(Key={"PK": f"SUMMARY#{trade_date}", "SK": "DAILY"})
    item = resp.get("Item")
    if not item or not item.get("summary_text"):
        print(f"skip: no summary in hot store for {trade_date}")
        return {"trade_date": trade_date, "pushed": False, "skipped": "no_summary"}

    facts = json.loads(item.get("facts_json") or "{}")
    text = _build_message(trade_date, item["summary_text"], facts)

    token, target = _load_config()
    mode = _send(token, target, text)

    print(f"sent summary for {trade_date} via {mode} ({len(text)} chars)")
    return {"trade_date": trade_date, "pushed": True, "mode": mode}
