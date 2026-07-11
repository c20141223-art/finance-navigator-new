"""Parser unit tests against SYNTHETIC fixtures shaped like the documented
TWSE JSON conventions. These are NOT captured live responses — see
docs/api_samples/README.md for why, and re-run this suite once real
samples are confirmed to still validate the same field names."""

import datetime as dt
import json

import pytest

from stock_screener.fetchers import twse
from stock_screener.schema_guard import SchemaMismatchError


def test_parse_daily_all_happy_path():
    payload = [
        {
            "Code": "2330", "Name": "台積電", "TradeVolume": "50000000",
            "TradeValue": "5000000000", "OpeningPrice": "1000", "HighestPrice": "1010",
            "LowestPrice": "995", "ClosingPrice": "1005", "Change": "5", "Transaction": "20000",
        }
    ]
    rows = twse.parse_daily_all(json.dumps(payload))
    assert rows == [{
        "stock_id": "2330", "name": "台積電", "open": 1000.0, "high": 1010.0,
        "low": 995.0, "close": 1005.0, "volume": 50000000, "turnover": 5000000000,
    }]


def test_parse_daily_all_missing_field_raises():
    payload = [{"Code": "2330", "Name": "台積電"}]
    with pytest.raises(SchemaMismatchError):
        twse.parse_daily_all(json.dumps(payload))


def test_parse_daily_history_happy_path():
    payload = {
        "stat": "OK",
        "fields1": ["指數", "收盤指數"],
        "data1": [["發行量加權股價指數", "17000.00"]],
        "fields2": [
            "證券代號", "證券名稱", "成交股數", "成交金額", "開盤價",
            "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數",
        ],
        "data2": [
            ["2330", "台積電", "50,000,000", "5,000,000,000", "1000.00", "1010.00", "995.00", "1005.00", "+5.00", "20000"],
        ],
    }
    rows = twse.parse_daily_history(json.dumps(payload), dt.date(2026, 7, 10))
    assert len(rows) == 1
    row = rows[0]
    assert row["stock_id"] == "2330"
    assert row["date"] == "2026-07-10"
    assert row["close"] == 1005.0
    assert row["volume"] == 50000000


def test_parse_daily_history_no_trading_day_returns_empty():
    payload = {"stat": "很抱歉，沒有符合條件的資料!"}
    rows = twse.parse_daily_history(json.dumps(payload), dt.date(2026, 7, 11))
    assert rows == []


def test_parse_institutional_happy_path():
    payload = {
        "stat": "OK",
        "fields": [
            "證券代號", "證券名稱",
            "外資買進股數", "外資賣出股數", "外資買賣超股數",
            "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
            "投信買進股數", "投信賣出股數", "投信買賣超股數",
            "自營商買賣超股數",
            "自營商買賣超股數(自行買賣)", "自營商買賣超股數(避險)",
            "三大法人買賣超股數",
        ],
        "data": [
            [
                "2330", "台積電",
                "10000000", "8000000", "2000000",
                "100000", "50000", "50000",
                "500000", "300000", "200000",
                "10000",
                "5000", "5000",
                "2210000",
            ]
        ],
    }
    rows = twse.parse_institutional(json.dumps(payload), dt.date(2026, 7, 10))
    assert rows == [{
        "stock_id": "2330", "date": "2026-07-10",
        "foreign_net": 2000000, "trust_net": 200000, "dealer_net": 10000,
    }]
