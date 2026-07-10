"""Unit tests for the analyzer Lambda pure helpers.

Includes the regression test for the "-100%" decliner bug: stocks whose
closing price is 0 (halted / no trade) must be excluded, not treated as a
-100% move that poisons the losers board and breadth counts.
"""

import datetime as dt
from decimal import Decimal

from _appmod import load_app

az = load_app("analyzer")


# ── 交易日判斷 ────────────────────────────────────────────────────────────────
class TestTradingDay:
    def test_weekday(self):
        assert az._is_trading_day(dt.date(2026, 6, 18)) is True  # Thursday

    def test_weekend(self):
        assert az._is_trading_day(dt.date(2026, 6, 20)) is False  # Saturday

    def test_holiday_on_weekday(self):
        # 2026-06-19 is a Friday but a holiday → still not a trading day
        assert az._is_trading_day(dt.date(2026, 6, 19)) is False


class TestPrevTradingDay:
    def test_crosses_weekend_and_holiday(self):
        # Mon 2026-06-22 → back past Sun/Sat/Fri-holiday → Thu 2026-06-18
        assert az._prev_trading_day(dt.date(2026, 6, 22)) == dt.date(2026, 6, 18)

    def test_simple_previous(self):
        assert az._prev_trading_day(dt.date(2026, 6, 18)) == dt.date(2026, 6, 17)


class TestPrevDayWithData:
    """The previous close must come from a day that *has data*, not from the
    calendar. 2026-07-10 was a Friday closed for a typhoon — it can never be in
    the hand-maintained holiday list, so the calendar happily points at it."""

    def _stub(self, monkeypatch, days_with_data):
        monkeypatch.setattr(
            az, "_query_day", lambda d: [{"PK": "STOCK#2330"}] if d in days_with_data else []
        )

    def test_skips_typhoon_day_with_no_data(self, monkeypatch):
        self._stub(monkeypatch, {"2026-07-09"})
        # Mon 2026-07-13: calendar prev is Fri 07-10 (typhoon, no data) → must fall to 07-09
        prev_date, items = az._prev_day_with_data(dt.date(2026, 7, 13))
        assert prev_date == "2026-07-09"
        assert items

    def test_normal_case_takes_immediate_previous(self, monkeypatch):
        self._stub(monkeypatch, {"2026-07-08", "2026-07-07"})
        prev_date, _ = az._prev_day_with_data(dt.date(2026, 7, 9))
        assert prev_date == "2026-07-08"

    def test_crosses_weekend_without_querying_it(self, monkeypatch):
        asked = []

        def _q(d):
            asked.append(d)
            return [{"PK": "STOCK#2330"}] if d == "2026-07-09" else []

        monkeypatch.setattr(az, "_query_day", _q)
        prev_date, _ = az._prev_day_with_data(dt.date(2026, 7, 13))  # Monday
        assert prev_date == "2026-07-09"
        # Sat 07-11 / Sun 07-12 are filtered by the calendar, never queried.
        assert "2026-07-11" not in asked and "2026-07-12" not in asked

    def test_gives_up_after_lookback_window(self, monkeypatch):
        self._stub(monkeypatch, set())
        assert az._prev_day_with_data(dt.date(2026, 7, 13)) == (None, [])


# ── 小工具 ────────────────────────────────────────────────────────────────────
def test_code_of():
    assert az._code_of({"PK": "STOCK#2330"}) == "2330"


def test_f_decimal_to_float():
    assert az._f(Decimal("1.5")) == 1.5
    assert az._f(None) is None
    assert az._f(3) == 3


# ── 訊號計算（含 -100% bug 迴歸測試）─────────────────────────────────────────
def _today():
    return [
        {"PK": "STOCK#1111", "close": 110.0, "volume": 1000.0, "name": "AAA"},  # +10%
        {"PK": "STOCK#2222", "close": 90.0, "volume": 5000.0, "name": "BBB"},   # -10%
        {"PK": "STOCK#3333", "close": 100.0, "volume": 2000.0, "name": "CCC"},  # 0%
        {"PK": "STOCK#4444", "close": 0.0, "volume": 9999.0, "name": "DEAD"},   # halted → drop
        {"PK": "STOCK#5555", "close": 50.0, "volume": 3000.0, "name": "EEE"},   # no prev → pct None
    ]


def _prev():
    return [
        {"PK": "STOCK#1111", "close": 100.0},
        {"PK": "STOCK#2222", "close": 100.0},
        {"PK": "STOCK#3333", "close": 100.0},
    ]


class TestBuildFacts:
    def test_excludes_zero_close(self):
        """Regression: close<=0 must never become a -100% decliner."""
        f = az._build_facts(_today(), _prev(), "2026-06-18")
        codes_anywhere = {
            r["code"]
            for key in ("top_gainers", "top_losers", "most_active")
            for r in f[key]
        }
        assert "4444" not in codes_anywhere
        assert f["decliners"] == 1  # only 2222, NOT inflated by the halted stock
        assert f["total_count"] == 4  # 4444 dropped; 5555 kept (close>0)

    def test_breadth_and_counts(self):
        f = az._build_facts(_today(), _prev(), "2026-06-18")
        assert (f["advancers"], f["decliners"], f["unchanged"]) == (1, 1, 1)
        assert f["breadth_pct"] == 33.33  # 1 / 3 rated

    def test_rankings(self):
        f = az._build_facts(_today(), _prev(), "2026-06-18")
        assert f["top_gainers"][0]["code"] == "1111"
        assert f["top_losers"][0]["code"] == "2222"
        assert f["most_active"][0]["code"] == "2222"  # highest volume among close>0

    def test_no_prev_pct_is_none_and_uncounted(self):
        f = az._build_facts(_today(), _prev(), "2026-06-18")
        eee = next(r for r in f["most_active"] if r["code"] == "5555")
        assert eee["pct_change"] is None


# ── 格式化 ────────────────────────────────────────────────────────────────────
class TestFormatting:
    def test_fmt_pct(self):
        assert az._fmt_pct(None) == "—"
        assert az._fmt_pct(1.5) == "+1.50%"
        assert az._fmt_pct(-2.0) == "-2.00%"

    def test_fmt_row_pct(self):
        r = {"code": "2330", "name": "台積電", "pct_change": 1.5, "close": 100.0, "volume": 1200}
        assert az._fmt_row(r, "pct") == "2330 台積電 +1.50%（收 100）"

    def test_fmt_row_volume(self):
        r = {"code": "2330", "name": "台積電", "pct_change": 1.5, "close": 100.0, "volume": 1200}
        assert az._fmt_row(r, "vol") == "2330 台積電 成交量 1,200（收 100）"

    def test_fmt_row_name_equals_code(self):
        r = {"code": "9999", "name": "9999", "pct_change": None, "close": 5.0, "volume": 1}
        assert az._fmt_row(r, "pct").startswith("9999 ")


def test_fallback_summary_is_grounded():
    f = az._build_facts(_today(), _prev(), "2026-06-18")
    text = az._fallback_summary(f)
    assert isinstance(text, str)
    assert "2026-06-18" in text
    assert "上漲 1" in text and "下跌 1" in text
    assert "漲幅領先" in text and "跌幅領先" in text and "成交量領先" in text
    assert "Bedrock" in text  # honest disclaimer that it's the non-AI fallback
