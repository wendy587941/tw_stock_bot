"""Read-only DuckDB queries over the local marts snapshot (Week 13, Stage 1).

Six tool functions the LLM can call (function-calling, Stage 3). They **only
read** the local Parquet snapshot produced by ``scripts/sync_snapshot.py`` and
**never recompute** anything — every indicator was already calculated by the
cloud analyzer/dbt layer. This mirrors the whole project's "ETL computes,
query side only reads" design.

"latest" everywhere means "the newest ``trade_date`` present in that snapshot"
(§5 of the plan) so the on-prem side never re-implements a trading-day calendar.

Run ``scripts/sync_snapshot.py`` first; otherwise these raise a clear error
pointing at the missing snapshot file.
"""

from __future__ import annotations

import datetime as _dt

import duckdb

from local_llm import config


def _path(name: str) -> str:
    p = config.SNAPSHOT_DIR / f"{name}.parquet"
    if not p.exists():
        raise FileNotFoundError(
            f"snapshot '{name}.parquet' not found under {config.SNAPSHOT_DIR}. "
            "Run `python scripts/sync_snapshot.py` first."
        )
    return p.as_posix()


def _jsonify(v):
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def _rows(sql: str, params: list) -> list[dict]:
    con = duckdb.connect()
    try:
        res = con.execute(sql, params)
        cols = [d[0] for d in res.description]
        return [{c: _jsonify(v) for c, v in zip(cols, row)} for row in res.fetchall()]
    finally:
        con.close()


def _latest_date(name: str) -> str | None:
    con = duckdb.connect()
    try:
        d = con.execute(f"SELECT max(trade_date) FROM read_parquet('{_path(name)}')").fetchone()[0]
        return d.isoformat() if isinstance(d, (_dt.date, _dt.datetime)) else d
    finally:
        con.close()


def _resolve(name: str, date: str) -> str | None:
    """'latest' → newest trade_date in that snapshot; otherwise pass through."""
    return _latest_date(name) if date == "latest" else date


# ── 6 個唯讀工具（對應規劃書 §5）────────────────────────────────────────────
def get_stock_ohlcv(code: str, days: int = 20) -> list[dict]:
    """近 N 個交易日的 OHLCV + MA5/MA20 + 漲跌幅（最新在前）。"""
    sql = f"""
        SELECT trade_date, open_price, high_price, low_price, close_price,
               volume, prev_close, pct_change, ma5, ma20
        FROM read_parquet('{_path("ohlcv")}')
        WHERE code = ?
        ORDER BY trade_date DESC
        LIMIT ?
    """
    return _rows(sql, [str(code), int(days)])


def get_market_breadth(date: str = "latest") -> dict | None:
    """指定（預設最新）交易日的漲跌平家數與市場廣度。"""
    d = _resolve("market_breadth", date)
    if d is None:
        return None
    rows = _rows(
        f"SELECT * FROM read_parquet('{_path('market_breadth')}') WHERE trade_date = ?", [d]
    )
    return rows[0] if rows else None


def get_top_movers(date: str = "latest", kind: str = "gainer", top_n: int = 10) -> list[dict]:
    """當日漲幅（gainer）或跌幅（loser）前段。"""
    d = _resolve("top_movers", date)
    if d is None:
        return []
    sql = f"""
        SELECT trade_date, rank_no, code, name, close_price, pct_change, volume, mover_type
        FROM read_parquet('{_path("top_movers")}')
        WHERE trade_date = ? AND mover_type = ?
        ORDER BY rank_no
        LIMIT ?
    """
    return _rows(sql, [d, str(kind), int(top_n)])


def get_signals(date: str = "latest", kind: str | None = None, top_n: int = 10) -> list[dict]:
    """當日訊號排行；kind 可選 gainer/loser/active，None 則回全部。"""
    d = _resolve("signals", date)
    if d is None:
        return []
    where = "trade_date = ?"
    params: list = [d]
    if kind:
        where += " AND signal_type = ?"
        params.append(str(kind))
    params.append(int(top_n))
    sql = f"""
        SELECT trade_date, signal_type, rank_no, code, name,
               close_price, volume, pct_change, score
        FROM read_parquet('{_path("signals")}')
        WHERE {where}
        ORDER BY signal_type, rank_no
        LIMIT ?
    """
    return _rows(sql, params)


def get_yield_ranking(top_n: int = 10) -> list[dict]:
    """最新交易日的殖利率排行前 N。"""
    d = _latest_date("yield")
    if d is None:
        return []
    sql = f"""
        SELECT trade_date, rank_no, code, name, dividend_yield, pe_ratio, pb_ratio
        FROM read_parquet('{_path("yield")}')
        WHERE trade_date = ?
        ORDER BY rank_no
        LIMIT ?
    """
    return _rows(sql, [d, int(top_n)])


def get_dividend(code: str) -> dict | None:
    """個股配息維度：除息日 / 現金股利 / 頻率（缺值為 None，代表待公告）。"""
    rows = _rows(
        f"SELECT * FROM read_parquet('{_path('dividend')}') WHERE code = ?", [str(code)]
    )
    return rows[0] if rows else None
