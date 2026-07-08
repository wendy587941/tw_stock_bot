"""Unit tests for the dispatcher Lambda pure helpers."""

import datetime as dt

from _appmod import load_app

dp = load_app("dispatcher")


class TestTradingDay:
    def test_weekday(self):
        assert dp._is_trading_day(dt.date(2026, 6, 18)) is True

    def test_weekend(self):
        assert dp._is_trading_day(dt.date(2026, 6, 20)) is False
        assert dp._is_trading_day(dt.date(2026, 6, 21)) is False  # Sunday

    def test_holiday(self):
        assert dp._is_trading_day(dt.date(2026, 6, 19)) is False


def test_taipei_now_is_utc_plus_8():
    now = dp._taipei_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == dt.timedelta(hours=8)
