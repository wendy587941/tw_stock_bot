"""Dividend Ingest Lambda — 配息資料領域擷取（Week 9）。

由 EventBridge 每日（週一至五 17:00 台北，晚於 notifier 16:30）觸發。與 OHLCV 主管線解耦，
獨立一支 Lambda 直接抓 + 算 + 寫，不走 dispatcher→SQS→worker 的逐檔 fan-out
（配息是全市場單檔批次、處理輕，無逐檔技術指標計算）。

9a（本階段）：殖利率排行
  1) 交易日防呆（沿用 dispatcher/analyzer 的 _is_trading_day + MARKET_HOLIDAYS）
  2) 抓 TWSE OpenAPI BWIBBU_ALL（個股本益比/殖利率/淨值比，每日全市場 ~1078 筆）
  3) 解析（民國日期轉西元、空/非正殖利率跳過）→ 依殖利率由高到低取前 TOP_N
  4) 寫單一彙整 item YIELD#{trade_date}/RANKING（top_json 為 JSON 字串）
     —— webhook 端一次 GetItem 即拿整份排行，免 Query/排序、免 GSI（最低 TCO、低延遲、數字一致）

防呆與回補：
  - 非交易日（週末/國定假日）直接跳過。event 帶 {"force": true} 繞過交易日檢查。
  - event 帶 {"trade_date": "YYYY-MM-DD"} 僅覆寫交易日防呆判斷；排行 item 的日期一律以
    來源資料自帶的 Date 為準（BWIBBU 每筆含 Date），避免在非交易日把舊資料寫到錯的日期。

9b/9c（後續波次）：個股配息日/到帳日（t187ap45 + 除息預告）、配息頻率清單，見規劃書 §13。
"""

import datetime as dt
import json
import os
import urllib.request
from decimal import Decimal

import boto3

HOT_TABLE = os.environ["HOT_TABLE"]
# 殖利率排行取前 N（與 analyzer 的訊號 TOP_N=5 分開，配息排行預設 20）
TOP_N = int(os.environ.get("DIVIDEND_TOP_N", "20"))

# 國定假日等非交易日（YYYY-MM-DD，逗號分隔），由 Terraform env 注入，與 dispatcher/analyzer 共用同一份清單。
MARKET_HOLIDAYS = {
    d.strip() for d in os.environ.get("MARKET_HOLIDAYS", "").split(",") if d.strip()
}

# 配息 item 比照 hot store 約 1 年後由 TTL 自動刪除，控成本。
TTL_DAYS = 400

# TWSE OpenAPI：個股日 本益比/殖利率/淨值比（每日全市場一次回傳）
TWSE_BWIBBU_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"

TPE = dt.timezone(dt.timedelta(hours=8))

table = boto3.resource("dynamodb").Table(HOT_TABLE)


# ── 日期/交易日工具（與 dispatcher/analyzer 一致的判斷邏輯） ──────────────────
def _is_trading_day(d: dt.date) -> bool:
    """週末（六/日）或列在假日清單 → 非交易日。"""
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in MARKET_HOLIDAYS


def _roc_to_iso(roc: str) -> str | None:
    """民國 YYYMMDD（如 1150618）→ 西元 ISO（2026-06-18）。格式不符回 None。

    西元年 = 前 3 碼民國年 + 1911。供本領域所有來源（BWIBBU / t187ap45）共用。
    """
    s = (roc or "").strip()
    if len(s) != 7 or not s.isdigit():
        return None
    year = int(s[:3]) + 1911
    month, day = s[3:5], s[5:7]
    try:
        return dt.date(year, int(month), int(day)).isoformat()
    except ValueError:
        return None


# ── 來源抓取 ─────────────────────────────────────────────────────────────────
def _fetch_bwibbu() -> list[dict]:
    req = urllib.request.Request(TWSE_BWIBBU_ALL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _to_float(v) -> float | None:
    """來源數字字串 → float；空字串 / '-' / 非數字 → None。"""
    s = str(v).strip().replace(",", "") if v is not None else ""
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ── 殖利率排行計算（grounding：webhook 只讀預算結果） ────────────────────────
def _build_ranking(rows: list[dict]) -> tuple[str | None, list[dict]]:
    """從 BWIBBU 全市場列表算殖利率排行。回 (data_date_iso, top_list)。

    data_date 取自來源資料自帶 Date（民國），作為 YIELD# 的權威日期。
    """
    data_date = None
    ranked: list[dict] = []
    for r in rows:
        code = (r.get("Code") or "").strip()
        if not code:
            continue
        y = _to_float(r.get("DividendYield"))
        if y is None or y <= 0:  # 空/0/非正殖利率不入排行
            continue
        if data_date is None:
            data_date = _roc_to_iso(r.get("Date", ""))
        ranked.append(
            {
                "code": code,
                "name": (r.get("Name") or code).strip(),
                "yield": y,
                "pe": _to_float(r.get("PEratio")),
                "pb": _to_float(r.get("PBratio")),
            }
        )

    ranked.sort(key=lambda x: x["yield"], reverse=True)
    return data_date, ranked[:TOP_N]


# ── 寫回 DynamoDB ────────────────────────────────────────────────────────────
def _dec(v):
    """float → Decimal（DynamoDB 不收 float）；None 維持 None。"""
    return Decimal(str(round(v, 4))) if v is not None else None


def _write_ranking(trade_date: str, top: list[dict], now: dt.datetime) -> None:
    """單一彙整 item：整份排行存成 JSON 字串（webhook 一次 GetItem 取得，免 Query/GSI）。

    JSON 字串內以 float 即可（不入 DynamoDB number 型別）；外層 item 屬性無 Decimal 需求。
    """
    expires = int((now + dt.timedelta(days=TTL_DAYS)).timestamp())
    table.put_item(
        Item={
            "PK": f"YIELD#{trade_date}",
            "SK": "RANKING",
            "top_json": json.dumps(top, ensure_ascii=False),
            "count": len(top),
            "generated_at": now.isoformat(),
            "ExpiresAt": expires,
        }
    )


# ── 進入點 ──────────────────────────────────────────────────────────────────
def handler(event, context):
    event = event or {}
    now = dt.datetime.now(TPE)
    guard_date = event.get("trade_date") or now.strftime("%Y-%m-%d")
    force = bool(event.get("force"))

    d = dt.date.fromisoformat(guard_date)
    if not force and not _is_trading_day(d):
        print(f"skip: {guard_date} is not a trading day")
        return {"trade_date": guard_date, "written": False, "skipped": "non_trading_day"}

    rows = _fetch_bwibbu()
    if not rows:
        print(f"abort: BWIBBU_ALL returned empty for {guard_date}")
        return {"trade_date": guard_date, "written": False, "skipped": "empty_source"}

    data_date, top = _build_ranking(rows)
    # 來源若無有效 Date（理論上不會），退回防呆日期，至少落地不掉資料
    trade_date = data_date or guard_date

    if not top:
        print(f"WARN dividend_source_failed: no positive yield rows for {trade_date}")
        return {"trade_date": trade_date, "written": False, "yield_count": 0}

    _write_ranking(trade_date, top, now)

    print(f"dividend yield ranking {trade_date}: top={len(top)} of {len(rows)} rows")
    return {"trade_date": trade_date, "written": True, "yield_count": len(top)}
