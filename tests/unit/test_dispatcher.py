"""Unit tests for the dispatcher Lambda pure helpers."""

import contextlib
import datetime as dt
import io
import json

import pytest
from _appmod import load_app

dp = load_app("dispatcher")


# A real 2330 row as MI_INDEX returns it (2026-07-09: close 2415, down 50).
ROW_2330 = [
    "2330", "台積電", "34,681,018", "201,698", "84,397,735,035",
    "2,450.00", "2,460.00", "2,415.00", "2,415.00",
    "<p style=' color:green'>-</p>", "50.00",
    "2,415.00", "1,268", "2,420.00", "35", "24.53",
]


def _payload(date="20260709", rows=None, stat="OK"):
    """Minimal MI_INDEX response containing the per-stock table."""
    body = {"stat": stat}
    if stat == "OK":
        body["date"] = date
        body["tables"] = [
            {"fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "24,000"]]},
            {
                "fields": ["證券代號", "證券名稱", "成交股數"],
                "data": rows if rows is not None else [ROW_2330],
            },
        ]
    return body


@contextlib.contextmanager
def _fake_urlopen(payload):
    yield io.BytesIO(json.dumps(payload).encode("utf-8"))


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


class TestParseChange:
    def test_down_is_negative(self):
        assert dp._parse_change("<p style=' color:green'>-</p>", "50.00") == "-50.0000"

    def test_up_is_positive(self):
        assert dp._parse_change("<p style=' color:red'>+</p>", "12.5") == "12.5000"

    def test_ex_dividend_marker_treated_as_non_negative(self):
        # 'X' marks 除權息, not a direction; magnitude still applies.
        assert dp._parse_change("<p>X</p>", "3.00") == "3.0000"

    def test_flat(self):
        assert dp._parse_change("<p> </p>", "0.00") == "0.0000"

    def test_strips_thousands_separator(self):
        assert dp._parse_change("<p>-</p>", "1,234.5") == "-1234.5000"

    def test_unparsable_returns_blank(self):
        assert dp._parse_change("<p>+</p>", "") == ""
        assert dp._parse_change("<p>+</p>", "--") == ""


class TestNormalize:
    def test_maps_columns_to_legacy_field_names(self):
        item = dp._normalize(ROW_2330)
        assert item == {
            "Code": "2330",
            "Name": "台積電",
            "TradeVolume": "34,681,018",
            "TradeValue": "84,397,735,035",
            "OpeningPrice": "2,450.00",
            "HighestPrice": "2,460.00",
            "LowestPrice": "2,415.00",
            "ClosingPrice": "2,415.00",
            "Change": "-50.0000",
            "Transaction": "201,698",
        }

    def test_short_row_rejected(self):
        assert dp._normalize(["2330", "台積電"]) is None

    def test_blank_code_rejected(self):
        assert dp._normalize(["  "] + [""] * 10) is None


class TestFetchDay:
    def test_returns_source_date_and_rows(self, monkeypatch):
        monkeypatch.setattr(
            dp.urllib.request, "urlopen", lambda *a, **k: _fake_urlopen(_payload())
        )
        source_date, rows = dp._fetch_day(dt.date(2026, 7, 9))
        assert source_date == "2026-07-09"
        assert [r["Code"] for r in rows] == ["2330"]

    def test_market_closed_returns_none(self, monkeypatch):
        # Typhoon day: TWSE answers "很抱歉，沒有符合條件的資料!" with no date/tables.
        closed = _payload(stat="很抱歉，沒有符合條件的資料!")
        monkeypatch.setattr(
            dp.urllib.request, "urlopen", lambda *a, **k: _fake_urlopen(closed)
        )
        assert dp._fetch_day(dt.date(2026, 7, 10)) is None

    def test_skips_tables_that_are_not_the_stock_table(self, monkeypatch):
        only_index = {
            "stat": "OK",
            "date": "20260709",
            "tables": [{"fields": ["指數", "收盤指數"], "data": [["加權", "1"]]}],
        }
        monkeypatch.setattr(
            dp.urllib.request, "urlopen", lambda *a, **k: _fake_urlopen(only_index)
        )
        assert dp._fetch_day(dt.date(2026, 7, 9)) is None

    def test_requests_the_asked_for_date(self, monkeypatch):
        seen = {}

        def _capture(req, *a, **k):
            seen["url"] = req.full_url
            return _fake_urlopen(_payload())

        monkeypatch.setattr(dp.urllib.request, "urlopen", _capture)
        dp._fetch_day(dt.date(2026, 7, 9))
        assert "date=20260709" in seen["url"]


class _SqsSpy:
    def __init__(self):
        self.entries = []

    def send_message_batch(self, QueueUrl, Entries):  # noqa: N803 — boto3 kwarg name
        assert len(Entries) <= 10  # SQS hard limit
        self.entries.extend(Entries)
        return {}


@pytest.fixture
def sqs_spy(monkeypatch):
    spy = _SqsSpy()
    monkeypatch.setattr(dp, "sqs", spy)
    return spy


class TestHandler:
    def test_dispatches_with_source_reported_date(self, monkeypatch, sqs_spy):
        monkeypatch.setattr(
            dp, "_fetch_day", lambda d: ("2026-07-09", [dp._normalize(ROW_2330)])
        )
        result = dp.handler({"trade_date": "2026-07-09"}, None)

        assert result == {"trade_date": "2026-07-09", "dispatched": 1}
        body = json.loads(sqs_spy.entries[0]["MessageBody"])
        assert body["TradeDate"] == "2026-07-09"
        assert body["ClosingPrice"] == "2,415.00"

    def test_batches_in_tens(self, monkeypatch, sqs_spy):
        rows = []
        for i in range(25):
            row = list(ROW_2330)
            row[0] = f"{1000 + i}"
            rows.append(dp._normalize(row))
        monkeypatch.setattr(dp, "_fetch_day", lambda d: ("2026-07-09", rows))

        assert dp.handler({"trade_date": "2026-07-09"}, None)["dispatched"] == 25
        assert len(sqs_spy.entries) == 25

    def test_typhoon_day_dispatches_nothing(self, monkeypatch, sqs_spy):
        """Regression: 2026-07-10 was a weekday closed for a typhoon.

        It can never appear in the hand-maintained MARKET_HOLIDAYS list, so the
        source — not the calendar — has to be what stops the run.
        """
        monkeypatch.setattr(dp, "_fetch_day", lambda d: None)
        result = dp.handler({"trade_date": "2026-07-10"}, None)

        assert result["skipped"] == "market_closed"
        assert result["dispatched"] == 0
        assert sqs_spy.entries == []

    def test_stale_source_never_stamps_todays_date(self, monkeypatch, sqs_spy):
        """Regression: the old code stamped wall-clock date onto whatever the
        source returned, silently shifting every record one trading day late."""
        monkeypatch.setattr(
            dp, "_fetch_day", lambda d: ("2026-07-09", [dp._normalize(ROW_2330)])
        )
        result = dp.handler({"trade_date": "2026-07-10"}, None)

        assert result["skipped"] == "source_date_mismatch"
        assert result["source_date"] == "2026-07-09"
        assert sqs_spy.entries == []

    def test_empty_rows_abort(self, monkeypatch, sqs_spy):
        monkeypatch.setattr(dp, "_fetch_day", lambda d: ("2026-07-09", []))
        result = dp.handler({"trade_date": "2026-07-09"}, None)

        assert result["skipped"] == "empty_source"
        assert sqs_spy.entries == []

    def test_weekend_short_circuits_before_http(self, monkeypatch, sqs_spy):
        def _boom(d):
            raise AssertionError("must not hit the network on a weekend")

        monkeypatch.setattr(dp, "_fetch_day", _boom)
        result = dp.handler({"trade_date": "2026-07-11"}, None)  # Saturday

        assert result["skipped"] == "non_trading_day"
        assert sqs_spy.entries == []

    def test_force_bypasses_holiday_short_circuit(self, monkeypatch, sqs_spy):
        monkeypatch.setattr(
            dp, "_fetch_day", lambda d: ("2026-06-19", [dp._normalize(ROW_2330)])
        )
        # 2026-06-19 is in MARKET_HOLIDAYS; force lets a backfill through.
        result = dp.handler({"trade_date": "2026-06-19", "force": True}, None)
        assert result["dispatched"] == 1
