"""Unit tests for the dividend_ingest Lambda pure helpers."""

from _appmod import load_app

div = load_app("dividend_ingest")


# ── 民國日期轉換 ──────────────────────────────────────────────────────────────
class TestRocToIso:
    def test_valid(self):
        assert div._roc_to_iso("1150618") == "2026-06-18"

    def test_wrong_length(self):
        assert div._roc_to_iso("115061") is None
        assert div._roc_to_iso("") is None

    def test_non_digit(self):
        assert div._roc_to_iso("abcdefg") is None

    def test_impossible_date(self):
        assert div._roc_to_iso("1151318") is None  # month 13
        assert div._roc_to_iso("1150230") is None  # 2026-02-30

    def test_none_input(self):
        assert div._roc_to_iso(None) is None


class TestRocYearToWest:
    def test_valid(self):
        assert div._roc_year_to_west("115") == "2026"

    def test_zero(self):
        assert div._roc_year_to_west("0") == "1911"

    def test_invalid(self):
        assert div._roc_year_to_west("") is None
        assert div._roc_year_to_west("abc") is None
        assert div._roc_year_to_west(None) is None


# ── 數值解析 ──────────────────────────────────────────────────────────────────
class TestToFloat:
    def test_thousands_separator(self):
        assert div._to_float("1,234.5") == 1234.5

    def test_dash_and_blank(self):
        assert div._to_float("-") is None
        assert div._to_float("") is None
        assert div._to_float("   ") is None

    def test_none(self):
        assert div._to_float(None) is None

    def test_plain(self):
        assert div._to_float("3.14") == 3.14
        assert div._to_float(" 5 ") == 5.0

    def test_garbage(self):
        assert div._to_float("--") is None
        assert div._to_float("abc") is None


# ── 現金股利加總 ──────────────────────────────────────────────────────────────
class TestCashDividend:
    _K = (
        "股東配發-盈餘分配之現金股利(元/股)",
        "股東配發-法定盈餘公積發放之現金(元/股)",
        "股東配發-資本公積發放之現金(元/股)",
    )

    def test_sums_three_fields(self):
        row = {self._K[0]: "4.5", self._K[1]: "0.5", self._K[2]: ""}
        assert div._cash_dividend(row) == 5.0

    def test_all_zero_is_none(self):
        row = {self._K[0]: "0", self._K[1]: "0", self._K[2]: "0"}
        assert div._cash_dividend(row) is None

    def test_all_missing_is_none(self):
        assert div._cash_dividend({}) is None


# ── 頻率分類 ──────────────────────────────────────────────────────────────────
class TestClassifyListedFrequency:
    def test_quarter(self):
        assert div._classify_listed_frequency("2026第1季") == div.FREQ_QUARTERLY

    def test_semiannual(self):
        assert div._classify_listed_frequency("2025半年") == div.FREQ_SEMIANNUAL

    def test_annual(self):
        assert div._classify_listed_frequency("2025年度") == div.FREQ_ANNUAL

    def test_unknown(self):
        assert div._classify_listed_frequency("無") == "unknown"
        assert div._classify_listed_frequency("") == "unknown"


# ── 殖利率排行 ────────────────────────────────────────────────────────────────
def _yield_rows():
    return [
        {"Code": "1101", "Name": "台泥", "DividendYield": "5.0", "Date": "1150618",
         "PEratio": "10", "PBratio": "1.2"},
        {"Code": "2330", "Name": "台積電", "DividendYield": "2.0", "Date": "1150618"},
        {"Code": "0000", "Name": "zero", "DividendYield": "0", "Date": "1150618"},
        {"Code": "", "Name": "nocode", "DividendYield": "3.0"},
        {"Code": "9999", "DividendYield": "-1"},
        {"Code": "3008", "Name": "大立光", "DividendYield": "4.0", "Date": "1150618"},
    ]


class TestBuildRanking:
    def test_sorts_desc_and_filters(self):
        data_date, top = div._build_ranking(_yield_rows())
        assert data_date == "2026-06-18"
        assert [r["code"] for r in top] == ["1101", "3008", "2330"]  # 0/neg/no-code dropped

    def test_carries_pe_pb(self):
        _, top = div._build_ranking(_yield_rows())
        assert top[0]["pe"] == 10.0 and top[0]["pb"] == 1.2

    def test_respects_top_n(self, monkeypatch):
        monkeypatch.setattr(div, "TOP_N", 2)
        _, top = div._build_ranking(_yield_rows())
        assert [r["code"] for r in top] == ["1101", "3008"]

    def test_empty(self):
        assert div._build_ranking([]) == (None, [])


# ── 配息維度合併 ──────────────────────────────────────────────────────────────
class TestBuildDividends:
    def _t187(self):
        newer = {
            "公司代號": "2330", "公司名稱": "台積電",
            "股東配發-盈餘分配之現金股利(元/股)": "4.5",
            "股利年度": "115", "股利所屬年(季)度": "2026第1季",
            "出表日期": "1150401", "期別": "1",
        }
        older = {  # same code, earlier issue date → must be ignored
            "公司代號": "2330", "公司名稱": "台積電",
            "股東配發-盈餘分配之現金股利(元/股)": "3.0",
            "股利年度": "114", "股利所屬年(季)度": "2025第4季",
            "出表日期": "1150101", "期別": "4",
        }
        return [older, newer]

    def _twt48u(self):
        return [
            {"Code": "2330", "Name": "台積電", "Date": "1150710", "Exdividend": "息",
             "CashDividend": ""},
            {"Code": "0056", "Name": "元大高股息", "Date": "1150715", "Exdividend": "息",
             "CashDividend": "1.2"},
            {"Code": "9999", "Name": "權證", "Date": "1150720", "Exdividend": "權",
             "CashDividend": "1"},  # 權 (not 息/權息) → skipped
        ]

    def test_merge(self):
        metas = {m["code"]: m for m in div._build_dividends(self._t187(), self._twt48u())}

        # listed stock: newest t187 cash, ex_date from TWT48U, quarter frequency
        assert metas["2330"]["cash_dividend"] == 4.5
        assert metas["2330"]["dividend_year"] == "2026"
        assert metas["2330"]["ex_date"] == "2026-07-10"
        assert metas["2330"]["frequency"] == div.FREQ_QUARTERLY
        assert metas["2330"]["pay_date"] is None  # always None (§12)

        # ETF only in TWT48U: created there, frequency overwritten by curated list
        assert metas["0056"]["ex_date"] == "2026-07-15"
        assert metas["0056"]["cash_dividend"] == 1.2
        assert metas["0056"]["frequency"] == div.FREQ_QUARTERLY

        # 權 (rights-only) row skipped entirely
        assert "9999" not in metas


# ── 頻率清單分桶 ──────────────────────────────────────────────────────────────
def _metas():
    return [
        {"code": "A", "name": "A", "ex_date": "2026-07-10", "pay_date": None, "frequency": "monthly"},
        {"code": "B", "name": "B", "ex_date": "2026-07-05", "pay_date": None, "frequency": "monthly"},
        {"code": "C", "name": "C", "ex_date": None, "pay_date": None, "frequency": "monthly"},
        {"code": "D", "name": "D", "ex_date": "2026-07-01", "pay_date": None, "frequency": "annual"},
    ]


class TestBuildFreqLists:
    def test_buckets_and_sort(self):
        out = div._build_freq_lists(_metas())
        assert set(out) == set(div.FREQ_BUCKETS)
        # ex_date ascending, None last
        assert [i["code"] for i in out["monthly"]["items"]] == ["B", "A", "C"]
        assert out["monthly"]["total"] == 3
        assert out["annual"]["total"] == 1
        assert out["quarterly"]["items"] == [] and out["quarterly"]["total"] == 0

    def test_cap(self, monkeypatch):
        monkeypatch.setattr(div, "FREQ_LIST_CAP", 1)
        out = div._build_freq_lists(_metas())
        assert [i["code"] for i in out["monthly"]["items"]] == ["B"]  # nearest ex_date
        assert out["monthly"]["total"] == 3  # total still reflects all
