"""Webhook Lambda — LINE 互動 webhook（Lambda Function URL 入口）。

使用者在 LINE 傳訊 → LINE Platform POST 到本函式的 Function URL：
  1) 驗 X-Line-Signature（HMAC-SHA256(channel_secret, raw_body) → base64）防偽造請求
  2) 解析 events[]：
     - message(text)：指令路由
         今日 / 盤勢 / 大盤 → 回最近一個交易日的盤勢摘要（SUMMARY）
         訊號             → 回最近交易日的漲幅 / 跌幅榜（GSI2 sparse 訊號索引）
         其他             → 回 help 說明
     - follow           → 擷取並 log userId（供日後設定 push_target），回歡迎詞
  3) 用 replyToken 呼叫 LINE Reply API 回覆

機密：channel_secret（驗章）+ channel_access_token（Reply）皆從 SSM SecureString 讀，不入版控。
純標準庫（hmac / hashlib / base64 / json / urllib）+ boto3（base image 提供），無額外相依 → image 輕、冷啟快。
入口用 Lambda Function URL（auth=NONE，免 API Gateway 費）；公開端點安全由上面的 HMAC 驗章把關。
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request

import boto3
from boto3.dynamodb.conditions import Key

HOT_TABLE = os.environ["HOT_TABLE"]
# SSM SecureString 參數路徑前綴（如 /wendy-tw-stock-bot/dev/line），與 notifier 共用同一前綴。
SSM_PREFIX = os.environ["SSM_PREFIX"].rstrip("/")
TOP_N = int(os.environ.get("TOP_N", "5"))

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_TEXT_LIMIT = 4900  # LINE 單則 text 上限 5000，保守截斷

TPE = dt.timezone(dt.timedelta(hours=8))

table = boto3.resource("dynamodb").Table(HOT_TABLE)
ssm = boto3.client("ssm")

HELP_TEXT = (
    "可用指令：\n"
    "・今日 / 盤勢 → 最近交易日台股盤勢摘要\n"
    "・訊號 → 漲幅榜 / 跌幅榜\n"
    "・help → 顯示本說明"
)


# ── 設定讀取（SSM SecureString，需解密）──────────────────────────────────────
def _load_config() -> tuple[str | None, str | None]:
    """讀 channel_secret（驗章用）與 channel_access_token（Reply 用）。任一缺漏回 None。"""
    resp = ssm.get_parameters(
        Names=[f"{SSM_PREFIX}/channel_secret", f"{SSM_PREFIX}/channel_access_token"],
        WithDecryption=True,
    )
    values = {p["Name"].rsplit("/", 1)[1]: p["Value"] for p in resp["Parameters"]}
    return values.get("channel_secret"), values.get("channel_access_token")


# ── 簽章驗證（防偽造請求）────────────────────────────────────────────────────
def _verify_signature(secret: str | None, raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256(channel_secret, 原始 body bytes) → base64，與 X-Line-Signature 常數時間比對。"""
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)


# ── 資料讀取（DynamoDB）──────────────────────────────────────────────────────
def _latest_summary(max_back: int = 7) -> tuple[str | None, dict | None]:
    """從今天往前找最近一個有摘要的交易日，回 (date_str, item)；找不到回 (None, None)。"""
    today = dt.datetime.now(TPE).date()
    for off in range(max_back):
        d = (today - dt.timedelta(days=off)).isoformat()
        item = table.get_item(Key={"PK": f"SUMMARY#{d}", "SK": "DAILY"}).get("Item")
        if item and item.get("summary_text"):
            return d, item
    return None, None


def _query_signals(date_str: str, signal_type: str) -> list[dict]:
    """查 GSI2 sparse 訊號索引：某交易日某類訊號（gainer/loser）分數由高到低取前 TOP_N。"""
    resp = table.query(
        IndexName="GSI2",
        KeyConditionExpression=Key("GSI2PK").eq(f"SIGNAL#{date_str}")
        & Key("GSI2SK").begins_with(f"{signal_type}#"),
        ScanIndexForward=False,  # GSI2SK 內嵌固定寬度分數 → 反向即由高到低
        Limit=TOP_N,
    )
    return resp.get("Items", [])


# ── 回覆內容組裝 ─────────────────────────────────────────────────────────────
def _pct(r: dict) -> str:
    v = r.get("pct_change")
    return "" if v is None else f"{float(v):+.2f}%"


def _fmt_signal(idx: int, r: dict) -> str:
    code = r["PK"].split("#", 1)[1]
    name = r.get("name", code)
    head = f"{code} {name}" if name != code else code
    return f"{idx}. {head} {_pct(r)}".rstrip()


def _summary_reply() -> str:
    d, item = _latest_summary()
    if not item:
        return "目前尚無可用的盤勢摘要，請稍後再試。"
    today = dt.datetime.now(TPE).strftime("%Y-%m-%d")
    text = item["summary_text"].strip()
    if len(text) > LINE_TEXT_LIMIT:
        text = text[: LINE_TEXT_LIMIT - 12].rstrip() + "…（全文略）"
    # 使用者問「今日」但當天非交易日/摘要未產出時，回最近交易日並註明
    prefix = "" if d == today else f"（最近交易日 {d}）\n"
    return prefix + text


def _signals_reply() -> str:
    d, item = _latest_summary()
    if not item:
        return "目前尚無可用的訊號資料。"
    gainers = _query_signals(d, "gainer")
    losers = _query_signals(d, "loser")
    lines = [f"📊 訊號榜｜{d}"]
    if gainers:
        lines.append("\n📈 漲幅榜")
        lines += [_fmt_signal(i, r) for i, r in enumerate(gainers, 1)]
    if losers:
        lines.append("\n📉 跌幅榜")
        lines += [_fmt_signal(i, r) for i, r in enumerate(losers, 1)]
    return "\n".join(lines) if (gainers or losers) else "今日無訊號資料。"


def _route(text: str) -> str:
    t = text.strip().lower()
    if t in ("今日", "今天", "盤勢", "大盤"):
        return _summary_reply()
    if t in ("訊號", "訊號榜", "signal", "榜"):
        return _signals_reply()
    return HELP_TEXT


# ── LINE Reply API（標準庫 urllib 直呼）──────────────────────────────────────
def _reply(token: str, reply_token: str, text: str) -> None:
    """以 replyToken 回覆（限時 ~1 分鐘、一次性）。失敗只 log，不讓整個 webhook 失敗（LINE 需快速 200）。"""
    body = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    req = urllib.request.Request(
        LINE_REPLY_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        print(f"WARN line_reply_failed: HTTP {e.code} {e.read().decode('utf-8', 'replace')}")


# ── 進入點（Lambda Function URL，payload format 2.0）────────────────────────
def handler(event, context):
    event = event or {}
    # Function URL header 為小寫；body 在二進位/壓縮時會 base64 編碼
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    raw = event.get("body") or ""
    body_bytes = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode("utf-8")

    secret, token = _load_config()
    if not _verify_signature(secret, body_bytes, headers.get("x-line-signature", "")):
        print("reject: invalid X-Line-Signature")
        return {"statusCode": 403, "body": "invalid signature"}

    try:
        payload = json.loads(body_bytes.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": "bad json"}

    for ev in payload.get("events", []):
        etype = ev.get("type")
        reply_token = ev.get("replyToken")
        if etype == "follow":
            uid = (ev.get("source") or {}).get("userId")
            print(f"follow event userId={uid}")  # log 出來供設定 push_target（精準推播）
            if reply_token and token:
                _reply(token, reply_token, "歡迎！輸入「今日」看台股盤勢摘要，或「訊號」看漲跌榜。")
        elif etype == "message" and (ev.get("message") or {}).get("type") == "text":
            if reply_token and token:
                _reply(token, reply_token, _route(ev["message"]["text"]))
        # 其他事件類型（unfollow / sticker / image…）忽略，回 200 即可

    # LINE 期望快速 200（含 console「Verify」測試：空 events 也回 200）
    return {"statusCode": 200, "body": "ok"}
