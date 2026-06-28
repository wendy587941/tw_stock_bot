"""Webhook Lambda — LINE 互動 webhook（API Gateway HTTP API 入口）。

使用者在 LINE 傳訊 → LINE Platform POST 到本函式：
  1) 驗 X-Line-Signature（HMAC-SHA256(channel_secret, raw_body) → base64）防偽造請求
  2) 解析 events[]：
     - message(text)：指令路由
         今日 / 盤勢 / 大盤   → 最近交易日盤勢摘要（SUMMARY，文字）
         訊號                 → 最近交易日漲幅 / 跌幅榜（GSI2 sparse 訊號索引，文字）
         殖利率               → 殖利率排行（Flex 表格 + Quick Reply 翻頁）
         配息 <股號>          → 個股除息/到帳/現金股利（Flex 卡片；ETF 未公告時誠實說明）
         月配/季配/半年配/年配 → 該頻率配息股清單（Flex 表格 + 翻頁 + 切換頻率）
         其他                 → help 說明
     - postback           → Quick Reply 翻頁（'yld:<page>' / 'frq:<freq>:<page>'）
     - follow             → 擷取並 log userId（供日後設定 push_target），回歡迎詞
  3) 用 replyToken 呼叫 LINE Reply API 回覆（文字或 Flex）

顯示策略：LINE 無原生摺疊 → 長清單（殖利率/頻率）用 Flex 表格取代純文字一面牆，
並以 Quick Reply postback 分頁（每頁 PAGE_SIZE 筆），避免單則訊息過長。

機密：channel_secret（驗章）+ channel_access_token（Reply）皆從 SSM SecureString 讀，不入版控。
純標準庫（hmac / hashlib / base64 / json / urllib）+ boto3（base image 提供），無額外相依 → image 輕、冷啟快。
入口用 API Gateway HTTP API（payload format 2.0）；公開端點安全由上面的 HMAC 驗章把關。
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
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
    "・殖利率 → 全市場殖利率排行\n"
    "・配息 <股號> → 除息日 / 到帳日 / 現金股利（例：配息 2330）\n"
    "・月配 / 季配 / 半年配 / 年配 → 該頻率配息股清單\n"
    "・help → 顯示本說明"
)

# 股號：4~6 碼數字（可含 1 碼英文後綴，如 1101B 特別股、00940 ETF）
_CODE_RE = re.compile(r"\d{4,6}[A-Za-z]?")

# 配息頻率指令 → DIVFREQ# 桶名（與 dividend_ingest 一致）
FREQ_COMMANDS = {"月配": "monthly", "季配": "quarterly", "半年配": "semiannual", "年配": "annual"}
FREQ_LABELS = {"monthly": "月配", "quarterly": "季配", "semiannual": "半年配", "annual": "年配"}


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


def _latest_yield(max_back: int = 7) -> tuple[str | None, dict | None]:
    """從今天往前找最近一個有殖利率排行的交易日，回 (date_str, item)；找不到回 (None, None)。

    比照 _latest_summary 的回退模式（殖利率擷取排程在 17:00，當天稍晚才有資料）。
    """
    today = dt.datetime.now(TPE).date()
    for off in range(max_back):
        d = (today - dt.timedelta(days=off)).isoformat()
        item = table.get_item(Key={"PK": f"YIELD#{d}", "SK": "RANKING"}).get("Item")
        if item and item.get("top_json"):
            return d, item
    return None, None


def _get_dividend(code: str) -> dict | None:
    """讀個股配息維度 DIVIDEND#{code}/META（擷取端預算好，webhook 一次 GetItem）。"""
    return table.get_item(Key={"PK": f"DIVIDEND#{code}", "SK": "META"}).get("Item")


def _get_freq_list(freq: str) -> dict | None:
    """讀配息頻率清單 DIVFREQ#{freq}/LIST（擷取端預算好，webhook 一次 GetItem）。"""
    return table.get_item(Key={"PK": f"DIVFREQ#{freq}", "SK": "LIST"}).get("Item")


# ── LINE 訊息 / Flex / Quick Reply 建構工具 ──────────────────────────────────
# 長清單（殖利率/頻率）每頁筆數：LINE 無原生摺疊，改用 Flex 表格 + Quick Reply postback 翻頁。
PAGE_SIZE = 10


def _text_msg(text: str, quick: list | None = None) -> dict:
    msg = {"type": "text", "text": text[:LINE_TEXT_LIMIT]}
    if quick:
        msg["quickReply"] = {"items": quick}
    return msg


def _flex_msg(alt_text: str, bubble: dict, quick: list | None = None) -> dict:
    msg = {"type": "flex", "altText": alt_text[:400], "contents": bubble}
    if quick:
        msg["quickReply"] = {"items": quick}
    return msg


def _qr_postback(label: str, data: str) -> dict:
    """Quick Reply：postback（翻頁，不在聊天室留下使用者訊息泡泡）。"""
    return {"type": "action", "action": {"type": "postback", "label": label[:20], "data": data}}


def _qr_message(label: str, text: str) -> dict:
    """Quick Reply：message（送出一則文字，等同使用者打字，用於切換頻率指令）。"""
    return {"type": "action", "action": {"type": "message", "label": label[:20], "text": text}}


def _page_quick(prefix: str, page: int, pages: int, extra: list | None = None) -> list | None:
    """組翻頁 Quick Reply：prefix 為 postback data 前綴（如 'yld' 或 'frq:monthly'）。"""
    items = []
    if page > 0:
        items.append(_qr_postback("◀ 上一頁", f"{prefix}:{page - 1}"))
    if page < pages - 1:
        items.append(_qr_postback("下一頁 ▶", f"{prefix}:{page + 1}"))
    if extra:
        items.extend(extra)
    return items or None


def _list_bubble(title: str, subtitle: str, rows: list[tuple], footer: str | None = None) -> dict:
    """排行/清單用 Flex 氣泡：rows = [(左字串, 右字串, 右側色碼)]，取代純文字一面牆。"""
    body = [
        {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                # 左欄（代號/股名）較長 → 不換行、放不下則截斷（代號在前，仍可辨識）
                {"type": "text", "text": left, "size": "sm", "flex": 5, "wrap": False},
                # 右欄（殖利率/除息日）優先完整顯示：加寬欄位，避免日期月日被截
                {
                    "type": "text",
                    "text": right,
                    "size": "sm",
                    "flex": 5,
                    "align": "end",
                    "color": color or "#333333",
                    "wrap": False,
                },
            ],
        }
        for left, right, color in rows
    ] or [{"type": "text", "text": "目前無資料", "size": "sm", "color": "#999999"}]
    header = [{"type": "text", "text": title, "weight": "bold", "size": "md"}]
    if subtitle:
        header.append({"type": "text", "text": subtitle, "size": "xs", "color": "#999999", "wrap": True})
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {"type": "box", "layout": "vertical", "contents": header},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
    }
    if footer:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": [{"type": "text", "text": footer, "size": "xxs", "color": "#aaaaaa", "wrap": True}],
        }
    return bubble


def _kv_bubble(title: str, pairs: list[tuple], footer: str | None = None) -> dict:
    """單檔卡片 Flex 氣泡：pairs = [(欄位名, 值)]。

    採直式欄位（標籤在上、值在下、整寬 wrap）而非左右兩欄 —— 避免窄欄位把日期/值截斷。
    """
    body = [
        {
            "type": "box",
            "layout": "vertical",
            "spacing": "none",
            "margin": "md",
            "contents": [
                {"type": "text", "text": k, "size": "xs", "color": "#999999"},
                {"type": "text", "text": v, "size": "sm", "weight": "bold", "color": "#333333", "wrap": True},
            ],
        }
        for k, v in pairs
    ]
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [{"type": "text", "text": title, "weight": "bold", "size": "md", "wrap": True}],
        },
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
    }
    if footer:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": [{"type": "text", "text": footer, "size": "xxs", "color": "#aaaaaa", "wrap": True}],
        }
    return bubble


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


def _yield_message(page: int = 0) -> dict:
    """殖利率排行 Flex 表格（每頁 PAGE_SIZE 筆，Quick Reply 翻頁）。"""
    d, item = _latest_yield()
    if not item:
        return _text_msg("目前尚無可用的殖利率排行，請稍後再試。")
    try:
        top = json.loads(item["top_json"])
    except (json.JSONDecodeError, KeyError):
        return _text_msg("殖利率排行資料異常，請稍後再試。")
    if not top:
        return _text_msg("今日無殖利率排行資料。")
    pages = (len(top) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    rows = []
    for i, r in enumerate(top[start : start + PAGE_SIZE], start + 1):
        name = r.get("name", r["code"])
        head = f"{i}. {r['code']} {name}" if name != r["code"] else f"{i}. {r['code']}"
        rows.append((head, f"{float(r['yield']):.2f}%", "#1f8f4e"))
    subtitle = f"{d}　第 {page + 1}/{pages} 頁（共 {len(top)} 檔）"
    bubble = _list_bubble("📈 殖利率排行", subtitle, rows)
    return _flex_msg(f"殖利率排行｜{d}", bubble, _page_quick("yld", page, pages))


def _dividend_message(text: str) -> dict:
    """個股配息：解析股號 → GetItem DIVIDEND#{code}/META → Flex 卡片（缺值顯示「待公告」）。"""
    m = _CODE_RE.search(text)
    if not m:
        return _text_msg("請輸入股號，例：配息 2330")
    code = m.group(0).upper()
    item = _get_dividend(code)
    if not item:
        # ETF（多 00 開頭）配息日需待發行商公告才進除息預告 → 誠實說明，而非泛用「查無」（§12）
        if code.startswith("00"):
            return _text_msg(
                f"{code} 目前無近期除息預告。\n"
                "ETF 配息日需待發行商公告、進入除權除息預告後才會提供，公告後會自動更新。"
            )
        return _text_msg(f"查無 {code} 的配息資料，請確認股號（例：配息 2330）。")
    name = item.get("name", code)
    title = f"💰 {name}（{code}）配息" if name != code else f"💰 {code} 配息"
    cash = item.get("cash_dividend")
    cash_str = f"{float(cash):g} 元/股" if cash is not None else "待公告"
    pairs = []
    if item.get("period"):
        pairs.append(("股利期間", item["period"]))
    pairs.append(("除息日", item.get("ex_date") or "待公告"))
    pairs.append(("到帳日", item.get("pay_date") or "待公告"))
    pairs.append(("現金股利", cash_str))
    return _flex_msg(f"{name} 配息", _kv_bubble(title, pairs))


def _freq_message(freq: str, page: int = 0) -> dict:
    """配息頻率清單 Flex 表格（依除息日近→遠，每頁 PAGE_SIZE 筆，Quick Reply 翻頁 + 切換頻率）。"""
    label = FREQ_LABELS.get(freq, freq)
    item = _get_freq_list(freq)
    if not item:
        return _text_msg(f"目前尚無{label}清單，請稍後再試。")
    try:
        items = json.loads(item["items_json"])
    except (json.JSONDecodeError, KeyError):
        return _text_msg(f"{label}清單資料異常，請稍後再試。")
    if not items:
        return _text_msg(f"目前無{label}配息股資料。")
    total = int(item.get("total", len(items)))
    shown = len(items)
    pages = (shown + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    rows = []
    for r in items[start : start + PAGE_SIZE]:
        name = r.get("name", r["code"])
        head = f"{r['code']} {name}" if name != r["code"] else r["code"]
        # 右欄只放日期（「除息」字樣移到副標），保留最大寬度給日期避免被截
        rows.append((head, r.get("ex_date") or "待公告", "#888888"))
    subtitle = f"共 {total} 檔" + (f"，近期除息 {shown} 檔" if total > shown else "")
    subtitle += f"　第 {page + 1}/{pages} 頁　｜ 右為除息日"
    bubble = _list_bubble(f"🗓️ {label}清單", subtitle, rows, footer="※ ETF 頻率為精選名單，持續擴充")
    # 翻頁 + 切換其他頻率（message 型 Quick Reply）
    switch = [_qr_message(lab, lab) for lab, fq in FREQ_COMMANDS.items() if fq != freq]
    quick = _page_quick(f"frq:{freq}", page, pages, extra=switch)
    return _flex_msg(f"{label}清單", bubble, quick)


def _route(text: str) -> dict:
    """文字訊息路由 → 回一則 LINE 訊息（文字或 Flex）。"""
    t = text.strip().lower()
    if t in ("今日", "今天", "盤勢", "大盤"):
        return _text_msg(_summary_reply())
    if t in ("訊號", "訊號榜", "signal", "榜"):
        return _text_msg(_signals_reply())
    if t in ("殖利率", "殖利率排行", "yield"):
        return _yield_message(0)
    if text.strip() in FREQ_COMMANDS:
        return _freq_message(FREQ_COMMANDS[text.strip()], 0)
    if "配息" in text:
        return _dividend_message(text)
    return _text_msg(HELP_TEXT)


def _route_postback(data: str) -> dict | None:
    """Quick Reply postback 翻頁路由：'yld:<page>' / 'frq:<freq>:<page>'。無法解析回 None。"""
    parts = (data or "").split(":")
    if len(parts) == 2 and parts[0] == "yld" and parts[1].isdigit():
        return _yield_message(int(parts[1]))
    if len(parts) == 3 and parts[0] == "frq" and parts[2].isdigit():
        freq = parts[1]
        if freq in FREQ_LABELS:
            return _freq_message(freq, int(parts[2]))
    return None


# ── LINE Reply API（標準庫 urllib 直呼）──────────────────────────────────────
def _reply(token: str, reply_token: str, message: dict) -> None:
    """以 replyToken 回一則訊息（文字或 Flex）。限時 ~1 分鐘、一次性；失敗只 log（LINE 需快速 200）。"""
    body = {"replyToken": reply_token, "messages": [message]}
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
                _reply(
                    token,
                    reply_token,
                    _text_msg("歡迎！輸入「今日」看台股盤勢摘要，或「訊號」看漲跌榜、「殖利率」看排行。"),
                )
        elif etype == "message" and (ev.get("message") or {}).get("type") == "text":
            if reply_token and token:
                _reply(token, reply_token, _route(ev["message"]["text"]))
        elif etype == "postback":
            msg = _route_postback((ev.get("postback") or {}).get("data", ""))
            if msg and reply_token and token:
                _reply(token, reply_token, msg)
        # 其他事件類型（unfollow / sticker / image…）忽略，回 200 即可

    # LINE 期望快速 200（含 console「Verify」測試：空 events 也回 200）
    return {"statusCode": 200, "body": "ok"}
