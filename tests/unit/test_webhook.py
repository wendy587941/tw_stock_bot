"""Unit tests for the webhook Lambda pure helpers (LINE signature, formatting)."""

import base64
import hashlib
import hmac
from decimal import Decimal

from _appmod import load_app

wh = load_app("webhook")


# ── HMAC 驗章（安全關鍵）──────────────────────────────────────────────────────
class TestVerifySignature:
    SECRET = "my-channel-secret"
    BODY = b'{"events":[]}'

    def _sig(self, secret=SECRET, body=BODY):
        mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        return base64.b64encode(mac).decode("utf-8")

    def test_valid_signature(self):
        assert wh._verify_signature(self.SECRET, self.BODY, self._sig()) is True

    def test_wrong_signature(self):
        assert wh._verify_signature(self.SECRET, self.BODY, "not-the-sig") is False

    def test_tampered_body(self):
        assert wh._verify_signature(self.SECRET, b'{"events":[1]}', self._sig()) is False

    def test_missing_secret(self):
        assert wh._verify_signature(None, self.BODY, self._sig()) is False

    def test_missing_signature(self):
        assert wh._verify_signature(self.SECRET, self.BODY, "") is False


# ── 預估到帳日（除息 + 30 天）─────────────────────────────────────────────────
class TestEstimatePayDate:
    def test_adds_30_days(self):
        assert wh._estimate_pay_date("2026-07-09") == "2026-08-08"

    def test_none(self):
        assert wh._estimate_pay_date(None) is None

    def test_invalid(self):
        assert wh._estimate_pay_date("not-a-date") is None


# ── 訊號格式化 ────────────────────────────────────────────────────────────────
class TestPct:
    def test_float(self):
        assert wh._pct({"pct_change": 1.5}) == "+1.50%"

    def test_decimal(self):
        assert wh._pct({"pct_change": Decimal("2")}) == "+2.00%"

    def test_missing(self):
        assert wh._pct({}) == ""


class TestFmtSignal:
    def test_with_name(self):
        r = {"PK": "STOCK#2330", "name": "台積電", "pct_change": 1.5}
        assert wh._fmt_signal(1, r) == "1. 2330 台積電 +1.50%"

    def test_name_equals_code(self):
        r = {"PK": "STOCK#9999", "pct_change": None}
        assert wh._fmt_signal(2, r) == "2. 9999"
