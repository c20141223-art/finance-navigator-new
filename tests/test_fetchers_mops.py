import json

import pytest

from stock_screener.fetchers import mops
from stock_screener.schema_guard import SchemaMismatchError

_REC = {
    "出表日期": "1150710", "資料年月": "11506", "公司代號": "2330",
    "公司名稱": "台積電", "產業別": "半導體業",
    "營業收入-當月營收": "263,708,845", "營業收入-上月營收": "250,000,000",
    "營業收入-去年當月營收": "207,869,772",
    "營業收入-上月比較增減(%)": "5.48", "營業收入-去年同月增減(%)": "26.86",
    "累計營業收入-當月累計營收": "1,500,000,000",
    "累計營業收入-去年累計營收": "1,200,000,000",
    "累計營業收入-前期比較增減(%)": "25.00", "備註": "",
}


def test_parse_happy_path():
    rows = mops.parse_monthly_revenue(json.dumps([_REC]), "monthly_revenue_sii")
    assert rows == [{
        "stock_id": "2330", "year_month": "2026-06", "revenue": 263708845,
        "yoy": 26.86, "mom": 5.48, "cumulative_yoy": 25.0, "announced_date": None,
    }]


def test_non_numeric_company_code_skipped():
    rec = dict(_REC, **{"公司代號": "合計"})
    assert mops.parse_monthly_revenue(json.dumps([rec]), "s") == []


def test_missing_field_raises():
    with pytest.raises(SchemaMismatchError):
        mops.parse_monthly_revenue(json.dumps([{"公司代號": "2330"}]), "s")


def test_roc_year_month():
    assert mops._roc_year_month_to_iso("11506") == "2026-06"
    assert mops._roc_year_month_to_iso("115/06") == "2026-06"
    assert mops._roc_year_month_to_iso("9912") == "2010-12"
    assert mops._roc_year_month_to_iso("garbage") is None
