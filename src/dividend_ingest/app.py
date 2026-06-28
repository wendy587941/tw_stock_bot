"""Dividend Ingest Lambda — 配息資料領域擷取（Week 9）。

由 EventBridge 每日（週一至五 17:00 台北，晚於 notifier 16:30）觸發。與 OHLCV 主管線解耦，
獨立一支 Lambda 直接抓 + 算 + 寫，不走 dispatcher→SQS→worker 的逐檔 fan-out
（配息是全市場單檔批次、處理輕，無逐檔技術指標計算）。

9a：殖利率排行
  1) 交易日防呆（沿用 dispatcher/analyzer 的 _is_trading_day + MARKET_HOLIDAYS）
  2) 抓 TWSE OpenAPI BWIBBU_ALL（個股本益比/殖利率/淨值比，每日全市場 ~1078 筆）
  3) 解析（民國日期轉西元、空/非正殖利率跳過）→ 依殖利率由高到低取前 TOP_N
  4) 寫單一彙整 item YIELD#{trade_date}/RANKING（top_json 為 JSON 字串）
     —— webhook 端一次 GetItem 即拿整份排行，免 Query/排序、免 GSI（最低 TCO、低延遲、數字一致）

9b（本階段）：個股配息日 / 到帳日 / 現金股利
  5) 抓 t187ap45_L（股利分派情形，~1135 筆）→ 取每檔最新一筆現金股利金額 + 股利年度 + 期別
  6) 抓 TWT48U 除權除息預告表（~296 筆未來除息）→ 補 ex_date（除息交易日，best-effort）
  7) 兩來源在記憶體 merge → 每檔寫 DIVIDEND#{code}/META（覆寫更新；不需先讀舊值）
     —— 到帳日（pay_date）非 TWSE OpenAPI 統一欄位，一律 None，webhook 端顯示「待公告」（§12 降級）

防呆與回補：
  - 非交易日（週末/國定假日）直接跳過。event 帶 {"force": true} 繞過交易日檢查。
  - event 帶 {"trade_date": "YYYY-MM-DD"} 僅覆寫交易日防呆判斷；YIELD item 的日期一律以
    來源資料自帶的 Date 為準（BWIBBU 每筆含 Date），避免在非交易日把舊資料寫到錯的日期。
  - 殖利率（9a）與配息（9b/9c）為獨立兩段 best-effort：任一來源抓取失敗只 log WARN，
    已成功的部分照寫，不整批失敗（§7）。

9c（本階段）：配息頻率清單（月配/季配/半年配/年配）
  8) 上市公司頻率由官方「股利所屬年(季)度」推導（季→季配、半年→半年配、年度→年配；不需 12 月歷史）
  9) ETF（月配主力，不在 t187ap45）由精選名單 ETF_FREQUENCY 覆寫頻率（§12 降級，持續擴充）
  10) 依頻率分桶、除息日近→遠取前 FREQ_LIST_CAP 檔 → 寫 DIVFREQ#{freq}/LIST（webhook 一次 GetItem）
"""

import datetime as dt
import json
import os
import re
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
# TWSE OpenAPI：股利分派情形（現金股利金額/股利年度/期別，全市場 ~1135 筆）
TWSE_T187AP45 = "https://openapi.twse.com.tw/v1/opendata/t187ap45_L"
# TWSE 報表 API：除權除息預告表（未來除息日，~296 筆）。openapi v1 端點回 302，故改用報表 API。
# 回傳 {stat, fields, data:[[除權除息日期, 股票代號, 名稱, 除權息(息/權/權息), ...現金股利...]]}
TWSE_TWT48U = "https://www.twse.com.tw/exchangeReport/TWT48U?response=json"

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


_CJK_DATE_RE = re.compile(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日")


def _roc_cjk_to_iso(s: str) -> str | None:
    """民國「YYY年MM月DD日」（如 115年07月09日，TWT48U 用此格式）→ 西元 ISO。不符回 None。"""
    m = _CJK_DATE_RE.search(s or "")
    if not m:
        return None
    year = int(m.group(1)) + 1911
    try:
        return dt.date(year, int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


# ── 來源抓取 ─────────────────────────────────────────────────────────────────
def _fetch_json(url: str):
    # TWSE 報表 API 對無 UA 的請求偶會擋，帶常見 UA 提高穩定度（公開資料，無認證）
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_bwibbu() -> list[dict]:
    return _fetch_json(TWSE_BWIBBU_ALL)


def _fetch_t187ap45() -> list[dict]:
    """股利分派情形（openapi v1，list[dict]，欄位為中文鍵）。"""
    return _fetch_json(TWSE_T187AP45)


def _fetch_twt48u() -> list[dict]:
    """除權除息預告表（報表 API，回 {fields, data:[[...]]}）→ 轉成 list[dict]（欄名對值）。"""
    payload = _fetch_json(TWSE_TWT48U)
    fields = payload.get("fields") or []
    return [dict(zip(fields, row)) for row in (payload.get("data") or [])]


_HTML_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """去掉 TWT48U 現金股利欄位可能夾帶的 HTML（如 <p>待公告…</p>）。"""
    return _HTML_RE.sub("", s or "").strip()


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


# ── 配息維度計算（9b：t187ap45 現金股利 + TWT48U 除息日 merge） ───────────────
def _cash_dividend(row: dict) -> float | None:
    """現金股利（元/股）= 盈餘分配 + 法定盈餘公積 + 資本公積 三項現金加總；全 0/空 → None。"""
    total = 0.0
    seen = False
    for k in (
        "股東配發-盈餘分配之現金股利(元/股)",
        "股東配發-法定盈餘公積發放之現金(元/股)",
        "股東配發-資本公積發放之現金(元/股)",
    ):
        v = _to_float(row.get(k))
        if v is not None:
            total += v
            seen = True
    return total if (seen and total > 0) else None


def _roc_year_to_west(yr: str | None) -> str | None:
    """民國年（如 '115'）→ 西元字串 '2026'；非數字 → None。"""
    s = (yr or "").strip()
    return str(int(s) + 1911) if s.isdigit() else None


def _period_label(row: dict) -> str:
    """股利年度（民國→西元）+ 所屬年(季)度，如 '2026 第1季' / '2025 年度'；供顯示與 9c 頻率推導。"""
    year = _roc_year_to_west(row.get("股利年度")) or ""
    span = (row.get("股利所屬年(季)度") or "").strip()
    return f"{year} {span}".strip() or None


def _sort_key_t187(row: dict) -> tuple:
    """同一檔可能有多筆（多季）→ 取最新：依出表日期、期別由大到小。"""
    issue = (row.get("出表日期") or "").strip()
    period = (row.get("期別") or "").strip()
    return (issue, int(period) if period.isdigit() else 0)


# ── 配息頻率分類（9c） ────────────────────────────────────────────────────────
# 頻率桶（與 webhook 指令、DIVFREQ#{freq}/LIST 一致）。
FREQ_MONTHLY = "monthly"
FREQ_QUARTERLY = "quarterly"
FREQ_SEMIANNUAL = "semiannual"
FREQ_ANNUAL = "annual"
FREQ_BUCKETS = (FREQ_MONTHLY, FREQ_QUARTERLY, FREQ_SEMIANNUAL, FREQ_ANNUAL)

# 每個頻率清單存進 DynamoDB / 回 LINE 的上限（依除息日近→遠取前段；年配約千檔故必須截斷）。
FREQ_LIST_CAP = 30

# 精選 ETF 配息頻率名單（§12 降級策略）：ETF 收益分配不在 t187ap45（上市公司盈餘分配表），
# TWSE 亦無免費的 ETF 頻率 API → 以穩定、公開周知的名單補；持續擴充，webhook 回覆會註明「ETF 為精選名單」。
# 僅對「當期有資料（已進 META，多由 TWT48U 預告補入）」的 ETF 生效；不在此表者頻率留 unknown。
ETF_FREQUENCY = {
    # 月配（每月配息）
    "00929": FREQ_MONTHLY,  # 復華台灣科技優息
    "00934": FREQ_MONTHLY,  # 中信成長高股息
    "00936": FREQ_MONTHLY,  # 台新永續高息中小
    "00939": FREQ_MONTHLY,  # 統一台灣高息動能
    "00940": FREQ_MONTHLY,  # 元大台灣價值高息
    "00943": FREQ_MONTHLY,  # 兆豐電子高息等權
    "00944": FREQ_MONTHLY,  # 野村趨勢動能高息
    "00946": FREQ_MONTHLY,  # 群益科技高息成長
    # 季配（每季配息）
    "0056": FREQ_QUARTERLY,  # 元大高股息
    "00878": FREQ_QUARTERLY,  # 國泰永續高股息
    "00713": FREQ_QUARTERLY,  # 元大台灣高息低波
    "00900": FREQ_QUARTERLY,  # 富邦特選高股息30
    "00919": FREQ_QUARTERLY,  # 群益台灣精選高息
    # 半年配
    "0050": FREQ_SEMIANNUAL,  # 元大台灣50
    "006208": FREQ_SEMIANNUAL,  # 富邦台50
}


def _classify_listed_frequency(span: str) -> str:
    """上市公司頻率：用官方「股利所屬年(季)度」欄推導（不需 12 月歷史，最低 TCO）。

    含「季」→季配、含「半年」→半年配、「年度」→年配；其餘 unknown。
    """
    s = span or ""
    if "季" in s:
        return FREQ_QUARTERLY
    if "半年" in s:
        return FREQ_SEMIANNUAL
    if "年度" in s:
        return FREQ_ANNUAL
    return "unknown"


def _build_dividends(t187_rows: list[dict], twt48u_rows: list[dict]) -> list[dict]:
    """合併兩來源 → 每檔一份 DIVIDEND META（覆寫式重建，不需先讀舊值）。

    - t187ap45：現金股利金額 / 股利年度 / 期別（涵蓋面廣，同檔多筆取最新一筆）+ 由「所屬年(季)度」推頻率（9c）。
    - TWT48U：除息交易日 ex_date（僅未來除息；同檔多筆取最近一次），並補上市櫃 ETF 名稱/現金股利。
    - pay_date（到帳日）非 TWSE OpenAPI 統一欄位 → 一律 None，webhook 端顯示「待公告」（§12）。
    - frequency（9c）：上市公司由官方所屬季度推導，ETF 由精選名單覆寫，其餘 unknown。
    """
    metas: dict[str, dict] = {}

    # 1) t187ap45：同檔多筆 → 取最新一筆的現金股利/年度/期別；頻率由所屬季度欄推導
    latest: dict[str, dict] = {}
    for r in t187_rows:
        code = (r.get("公司代號") or "").strip()
        if not code:
            continue
        if code not in latest or _sort_key_t187(r) > _sort_key_t187(latest[code]):
            latest[code] = r
    for code, r in latest.items():
        metas[code] = {
            "code": code,
            "name": (r.get("公司名稱") or code).strip(),
            "cash_dividend": _cash_dividend(r),
            "dividend_year": _roc_year_to_west(r.get("股利年度")),
            "period": _period_label(r),
            "ex_date": None,
            "pay_date": None,  # 資料缺口 → 待公告（§12）
            "frequency": _classify_listed_frequency(r.get("股利所屬年(季)度", "")),
        }

    # 2) TWT48U：補除息日（取最近一次未來除息）；只取含現金的「息/權息」
    for r in twt48u_rows:
        if (r.get("除權息") or "").strip() not in ("息", "權息"):
            continue
        code = (r.get("股票代號") or "").strip()
        ex = _roc_cjk_to_iso(r.get("除權除息日期", ""))
        if not code or not ex:
            continue
        m = metas.get(code)
        if m is None:  # 多為 ETF（不在 t187ap45 上市公司股利表）→ 用預告表補基本資料
            cash = _to_float(_strip_html(r.get("現金股利", "")))
            m = metas[code] = {
                "code": code,
                "name": (r.get("名稱") or code).strip(),
                "cash_dividend": cash,
                "dividend_year": None,
                "period": None,
                "ex_date": None,
                "pay_date": None,
                "frequency": "unknown",  # ETF 頻率由下方精選名單覆寫
            }
        # 同檔多筆預告 → 留最近一次除息日
        if m["ex_date"] is None or ex < m["ex_date"]:
            m["ex_date"] = ex

    # 3) ETF 精選名單覆寫頻率（t187 不含 ETF；此名單對已進 META 的 ETF 生效）
    for code, m in metas.items():
        if code in ETF_FREQUENCY:
            m["frequency"] = ETF_FREQUENCY[code]

    return list(metas.values())


def _build_freq_lists(metas: list[dict]) -> dict[str, dict]:
    """依 frequency 分桶 → 每桶取除息日近→遠前 FREQ_LIST_CAP 檔。

    回 {freq: {"items": list[{code,name,ex_date,pay_date}], "total": int}}。
    排序：有 ex_date 者依日期近→遠在前，無 ex_date（待公告）排最後。
    """
    buckets: dict[str, list] = {f: [] for f in FREQ_BUCKETS}
    for m in metas:
        f = m.get("frequency")
        if f in buckets:
            buckets[f].append(
                {
                    "code": m["code"],
                    "name": m["name"],
                    "ex_date": m["ex_date"],
                    "pay_date": m["pay_date"],
                }
            )
    out: dict[str, dict] = {}
    for f, items in buckets.items():
        items.sort(key=lambda x: (x["ex_date"] is None, x["ex_date"] or ""))
        out[f] = {"items": items[:FREQ_LIST_CAP], "total": len(items)}
    return out


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


def _write_dividends(metas: list[dict], now: dt.datetime) -> int:
    """每檔一份 DIVIDEND#{code}/META，batch_writer 批次寫；回實際寫入筆數。

    None 屬性移除（DynamoDB 不收 None）；cash_dividend 轉 Decimal（比照 worker/analyzer）。
    """
    expires = int((now + dt.timedelta(days=TTL_DAYS)).timestamp())
    iso_now = now.isoformat()
    written = 0
    with table.batch_writer() as batch:
        for m in metas:
            item = {
                "PK": f"DIVIDEND#{m['code']}",
                "SK": "META",
                "name": m["name"],
                "cash_dividend": _dec(m["cash_dividend"]),
                "dividend_year": m["dividend_year"],
                "period": m["period"],
                "ex_date": m["ex_date"],
                "pay_date": m["pay_date"],
                "frequency": m.get("frequency") or "unknown",
                "updated_at": iso_now,
                "ExpiresAt": expires,
            }
            batch.put_item(Item={k: v for k, v in item.items() if v is not None})
            written += 1
    return written


def _write_freq_lists(freq_lists: dict[str, dict], now: dt.datetime) -> dict[str, int]:
    """每頻率一份彙整 item DIVFREQ#{freq}/LIST（webhook 一次 GetItem 取整份，免 Query/GSI）；回各桶總數。"""
    expires = int((now + dt.timedelta(days=TTL_DAYS)).timestamp())
    iso_now = now.isoformat()
    counts: dict[str, int] = {}
    for freq, data in freq_lists.items():
        table.put_item(
            Item={
                "PK": f"DIVFREQ#{freq}",
                "SK": "LIST",
                "items_json": json.dumps(data["items"], ensure_ascii=False),
                "count": len(data["items"]),  # 實際存入（已截斷）
                "total": data["total"],  # 該頻率全部檔數
                "generated_at": iso_now,
                "ExpiresAt": expires,
            }
        )
        counts[freq] = data["total"]
    return counts


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

    stats = {"trade_date": guard_date, "yield_count": 0, "dividend_count": 0}

    # ── 9a：殖利率排行（best-effort，與配息互不影響） ──────────────────────────
    try:
        rows = _fetch_bwibbu()
        if rows:
            data_date, top = _build_ranking(rows)
            trade_date = data_date or guard_date  # 來源無有效 Date 時退回防呆日期
            if top:
                _write_ranking(trade_date, top, now)
                stats["trade_date"] = trade_date
                stats["yield_count"] = len(top)
                print(f"yield ranking {trade_date}: top={len(top)} of {len(rows)} rows")
            else:
                print(f"WARN dividend_source_failed: no positive yield rows for {trade_date}")
        else:
            print(f"WARN dividend_source_failed: BWIBBU_ALL empty for {guard_date}")
    except Exception as e:  # noqa: BLE001 — 單一來源失敗不拖垮整批
        print(f"WARN dividend_source_failed: BWIBBU fetch/build error: {e!r}")

    # ── 9b/9c：個股配息維度 + 頻率清單（best-effort；ex_date best-effort，pay_date 待公告） ──
    try:
        t187 = _fetch_t187ap45()
        try:
            twt48u = _fetch_twt48u()
        except Exception as e:  # noqa: BLE001 — 除息預告掛掉仍可只供股利金額
            print(f"WARN dividend_source_failed: TWT48U fetch error: {e!r}")
            twt48u = []
        metas = _build_dividends(t187, twt48u)
        if metas:
            written = _write_dividends(metas, now)
            stats["dividend_count"] = written
            ex_cov = sum(1 for m in metas if m["ex_date"])
            print(
                f"dividend meta written={written} (t187={len(t187)}, twt48u={len(twt48u)}, "
                f"ex_date_coverage={ex_cov}/{written})"
            )
            # 9c：頻率分桶 → DIVFREQ#{freq}/LIST
            freq_counts = _write_freq_lists(_build_freq_lists(metas), now)
            stats["freq_counts"] = freq_counts
            print(f"freq lists: {freq_counts}")
        else:
            print("WARN dividend_source_failed: no dividend metas built")
    except Exception as e:  # noqa: BLE001
        print(f"WARN dividend_source_failed: dividend fetch/build error: {e!r}")

    stats["written"] = bool(stats["yield_count"] or stats["dividend_count"])
    return stats
