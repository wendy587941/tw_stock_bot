"""LINE webhook for the local QA demo (Week 13, Stage 5).

Fronts the local Ollama pipeline (``qa.router.respond``) with a FastAPI webhook so
the demo can be driven from a phone. Deliberately points at a **second, demo-only
LINE channel** — never the production channel A, whose webhook lives on a Lambda
Function URL. Pointing channel A here would take the live bot down.

Request path:  LINE ──► Cloudflare Tunnel ──► this server ──► Ollama (localhost).

Two constraints shape the design:

* A reply token expires in well under a minute, and a 3B model doing tool-use can
  take longer than that. So the reply token only ever carries "🔎 查詢中…"; the
  real answer is delivered later via the push API, addressed to the event's
  ``userId``.
* 4GB of VRAM cannot serve two inferences at once. A single-flight lock turns a
  concurrent question into an immediate "busy" reply rather than a request that
  queues up and times out.

Run:  uvicorn local_llm.line_server:app --port 8000
(needs Ollama up, a synced snapshot, and the demo channel's token/secret in the
environment; Windows: PYTHONUTF8=1.)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading

import requests
from fastapi import BackgroundTasks, FastAPI, Header, Request, Response

from local_llm import config
from local_llm.qa.router import respond

log = logging.getLogger("line_server")

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"

# LINE 單則文字訊息上限 5000 字；留一點餘裕給截斷提示。
_MAX_TEXT = 4900
_HTTP_TIMEOUT = 10

# 地端 3B 一次只餵得動一個推論（4GB VRAM）。第二個請求不排隊，直接回「忙碌」。
_qa_lock = threading.Lock()

app = FastAPI(title="local-llm-stock-qa")


# ── LINE Messaging API ───────────────────────────────────────────────
def _post(url: str, body: dict) -> None:
    """打 LINE API；失敗只 log。webhook 已回 200，這裡再拋例外也無人接。"""
    if not config.LINE_CHANNEL_TOKEN:
        log.warning("no LINE_DEMO_CHANNEL_TOKEN; skipping %s", url)
        return
    try:
        r = requests.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {config.LINE_CHANNEL_TOKEN}"},
            timeout=_HTTP_TIMEOUT,
        )
        # reply/push 回 200，loading animation 回 202 Accepted → 一律以 2xx 為成功。
        if not 200 <= r.status_code < 300:
            log.warning("line_api_failed %s: HTTP %s %s", url, r.status_code, r.text)
    except requests.RequestException as e:
        log.warning("line_api_error %s: %s", url, e)


def _text(body: str) -> dict:
    if len(body) > _MAX_TEXT:
        body = body[: _MAX_TEXT - 1] + "…"
    return {"type": "text", "text": body}


def _reply(reply_token: str, text: str) -> None:
    _post(LINE_REPLY_URL, {"replyToken": reply_token, "messages": [_text(text)]})


def _push(user_id: str, text: str) -> None:
    _post(LINE_PUSH_URL, {"to": user_id, "messages": [_text(text)]})


def _start_loading(user_id: str, seconds: int = 60) -> None:
    """在 1:1 聊天室顯示「輸入中」動畫（秒數須為 5 的倍數，上限 60）。"""
    _post(LINE_LOADING_URL, {"chatId": user_id, "loadingSeconds": seconds})


# ── 驗章 ──────────────────────────────────────────────────────────────
def verify_signature(secret: str | None, raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256(channel_secret, 原始 body bytes) → base64，與 X-Line-Signature 常數時間比對。"""
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)


# ── QA（背景執行）────────────────────────────────────────────────────
def _answer_and_push(user_id: str, question: str) -> None:
    """跑 QA pipeline 後 push 答案。呼叫端已持有 _qa_lock，此處負責釋放。"""
    try:
        out = respond(question)
        for t in out["tool_calls"]:
            log.info("tool %s(%s)", t["name"], t["args"])
        _push(user_id, out["text"])
    except Exception:  # 地端 demo：模型/快照任一環出錯都不該讓使用者空等
        log.exception("qa_failed question=%r", question)
        _push(user_id, "抱歉，本機模型暫時無法回答，請稍後再試一次。")
    finally:
        _qa_lock.release()


# ── 端點 ──────────────────────────────────────────────────────────────
@app.get("/healthz")
def healthz() -> dict:
    """Tunnel / 啟動確認用；順便揭露是否已載入 demo channel 憑證。"""
    return {
        "ok": True,
        "model": config.LLM_MODEL,
        "credentials_loaded": bool(config.LINE_CHANNEL_TOKEN and config.LINE_CHANNEL_SECRET),
        "busy": _qa_lock.locked(),
    }


@app.post("/callback")
async def callback(
    request: Request,
    background: BackgroundTasks,
    x_line_signature: str = Header(default=""),
) -> Response:
    """LINE webhook：驗章 → 立刻回「查詢中」→ 背景跑 QA → push 答案。

    必須秒回 200，否則 LINE 判定逾時並重送（會造成重複推論）。
    """
    raw = await request.body()
    if not verify_signature(config.LINE_CHANNEL_SECRET, raw, x_line_signature):
        log.warning("reject: invalid X-Line-Signature")
        return Response(content="invalid signature", status_code=403)

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return Response(content="bad json", status_code=400)

    # events 為空 = LINE console 的 Verify 按鈕，驗章過即算通過。
    for ev in payload.get("events", []):
        _handle_event(ev, background)

    return Response(content="OK", status_code=200)


def _handle_event(ev: dict, background: BackgroundTasks) -> None:
    etype = ev.get("type")
    reply_token = ev.get("replyToken")
    user_id = (ev.get("source") or {}).get("userId")

    if etype == "follow":
        log.info("follow event userId=%s", user_id)
        if reply_token:
            _reply(reply_token, "歡迎！這是地端 LLM 台股 QA demo。\n試試看：今日、台積電走勢、什麼是殖利率")
        return

    if etype != "message" or (ev.get("message") or {}).get("type") != "text":
        return

    question = (ev["message"].get("text") or "").strip()
    if not question:
        return

    # push 需要 userId（群組/聊天室事件可能沒有）→ 沒有就不受理，避免答案無處可送。
    if not user_id:
        if reply_token:
            _reply(reply_token, "這個 demo 只支援一對一聊天喔。")
        return

    if not _qa_lock.acquire(blocking=False):
        if reply_token:
            _reply(reply_token, "⏳ 正在處理上一個問題，請稍候幾秒再問一次。")
        return

    # 鎖已持有 → 交棒給背景任務，由 _answer_and_push 的 finally 釋放。
    if reply_token:
        _reply(reply_token, "🔎 查詢中…")
    _start_loading(user_id)
    background.add_task(_answer_and_push, user_id, question)
