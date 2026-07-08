"""Unit tests for the notifier Lambda pure helpers."""

import datetime as dt

from _appmod import load_app

nt = load_app("notifier")


class TestTradingDay:
    def test_weekday(self):
        assert nt._is_trading_day(dt.date(2026, 6, 18)) is True

    def test_weekend(self):
        assert nt._is_trading_day(dt.date(2026, 6, 20)) is False

    def test_holiday(self):
        assert nt._is_trading_day(dt.date(2026, 6, 19)) is False


_FACTS = {"advancers": 10, "decliners": 5, "unchanged": 2, "breadth_pct": 58.82}


class TestBuildMessage:
    def test_contains_header_stats_and_body(self):
        msg = nt._build_message("2026-06-18", "盤勢摘要本文", _FACTS)
        assert "2026-06-18" in msg
        assert "漲 10" in msg and "跌 5" in msg and "平 2" in msg
        assert "市場廣度 58.82%" in msg
        assert "盤勢摘要本文" in msg

    def test_truncates_overlong_body(self):
        long_body = "字" * 6000
        msg = nt._build_message("2026-06-18", long_body, _FACTS)
        assert len(msg) <= nt.LINE_TEXT_LIMIT
        assert msg.endswith("…（全文見資料庫）")
