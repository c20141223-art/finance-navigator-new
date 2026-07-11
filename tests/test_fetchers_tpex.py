"""Parser unit tests. test_parse_daily_all_happy_path and
test_parse_daily_history_happy_path use field names/shapes CONFIRMED
against live responses captured 2026-07-11 (see
docs/api_samples/tpex_daily_all.json and tpex_daily_history.json).
parse_institutional is still an unverified guess — see module docstring
in stock_screener/fetchers/tpex.py."""

import datetime as dt
import json

import pytest

from stock_screener.fetchers import tpex
from stock_screener.schema_guard import SchemaMismatchError


def test_parse_daily_all_happy_path():
    payload = [{
        "Date": "1150709", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
        "Close": "500", "Change": "+5", "Open": "495", "High": "505", "Low": "490",
        "Average": "498", "TradingShares": "1000000", "TransactionAmount": "500000000",
        "TransactionNumber": "800", "LatestBidPrice": "499", "LatesAskPrice": "501",
        "Capitals": "100000000", "NextReferencePrice": "500", "NextLimitUp": "550",
        "NextLimitDown": "450",
    }]
    rows = tpex.parse_daily_all(json.dumps(payload))
    assert rows == [{
        "stock_id": "6488", "name": "環球晶", "open": 495.0, "high": 505.0,
        "low": 490.0, "close": 500.0, "volume": 1000000, "turnover": 500000000,
    }]


def test_parse_daily_all_missing_field_raises():
    with pytest.raises(SchemaMismatchError):
        tpex.parse_daily_all(json.dumps([{"SecuritiesCompanyCode": "6488"}]))


def test_parse_daily_history_happy_path():
    payload = {
        "tables": [{
            "title": "上櫃股票每日收盤行情(不含定價)",
            "date": "115/07/10",
            "fields": [
                "代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低",
                "成交股數  ", " 成交金額(元)", " 成交筆數 ", "最後買價",
                "最後買量<br>(張數)", "最後賣價", "最後賣量<br>(張數)",
                "發行股數 ", "次日漲停價 ", "次日跌停價",
            ],
            "data": [
                ["6488", "環球晶", "500.00", "+5.00", "495.00", "505.00", "490.00",
                 "1,000,000", "500,000,000", "800", "499.00", "10", "501.00", "10",
                 "100,000,000", "550.00", "450.00"],
            ],
        }],
        "stat": "ok",
    }
    rows = tpex.parse_daily_history(json.dumps(payload), dt.date(2026, 7, 10))
    assert rows == [{
        "stock_id": "6488", "date": "2026-07-10", "open": 495.0, "high": 505.0,
        "low": 490.0, "close": 500.0, "volume": 1000000, "turnover": 500000000,
    }]


def test_parse_daily_history_empty_table_returns_empty():
    payload = {
        "tables": [{
            "fields": ["代號", "名稱", "收盤 "],
            "data": [],
        }],
        "stat": "ok",
    }
    rows = tpex.parse_daily_history(json.dumps(payload), dt.date(2026, 7, 10))
    assert rows == []


def test_parse_daily_history_no_tables_key_returns_empty():
    rows = tpex.parse_daily_history(json.dumps({"some_other_key": []}), dt.date(2026, 7, 10))
    assert rows == []


def test_parse_daily_history_missing_price_table_raises():
    payload = {"tables": [{"fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "17000"]]}]}
    with pytest.raises(SchemaMismatchError):
        tpex.parse_daily_history(json.dumps(payload), dt.date(2026, 7, 10))
