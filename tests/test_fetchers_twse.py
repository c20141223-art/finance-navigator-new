"""Parser unit tests. Fixtures mirror the field names confirmed against
live samples captured 2026-07-12 (docs/api_samples/*.json)."""

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


def test_parse_daily_history_tables_shape():
    """The shape live MI_INDEX responses actually use (2026-07 sample)."""
    payload = {
        "stat": "OK",
        "date": "20260707",
        "tables": [
            {"title": "價格指數", "fields": ["指數", "收盤指數"],
             "data": [["發行量加權股價指數", "17000.00"]]},
            {"title": "每日收盤行情",
             "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額",
                         "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差"],
             "data": [["2330", "台積電", "31,400,854", "132,943", "77,617,188,273",
                        "2,480.00", "2,500.00", "2,440.00", "2,440.00",
                        "<p style= color:green>-</p>", "20.00"]]},
        ],
    }
    rows = twse.parse_daily_history(json.dumps(payload), dt.date(2026, 7, 7))
    assert rows == [{
        "stock_id": "2330", "date": "2026-07-07", "open": 2480.0, "high": 2500.0,
        "low": 2440.0, "close": 2440.0, "volume": 31400854, "turnover": 77617188273,
    }]


def test_parse_daily_history_legacy_numbered_pairs_still_supported():
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
    assert rows[0]["close"] == 1005.0


def test_parse_daily_history_no_trading_day_returns_empty():
    payload = {"stat": "很抱歉，沒有符合條件的資料!"}
    rows = twse.parse_daily_history(json.dumps(payload), dt.date(2026, 7, 11))
    assert rows == []


def test_parse_institutional_happy_path():
    """Field names as in the live T86 sample (docs/api_samples/
    twse_institutional.json): the foreign column is 外陸資...(不含外資自營商)."""
    payload = {
        "stat": "OK",
        "fields": [
            "證券代號", "證券名稱",
            "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
            "外陸資買賣超股數(不含外資自營商)",
            "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
            "投信買進股數", "投信賣出股數", "投信買賣超股數",
            "自營商買賣超股數",
            "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)",
            "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)",
            "三大法人買賣超股數",
        ],
        "data": [
            [
                "2330", "台積電",
                "10,000,000", "8,000,000", "2,000,000",
                "100,000", "50,000", "50,000",
                "500,000", "300,000", "200,000",
                "10,000",
                "3,000", "1,000", "2,000",
                "9,000", "1,000", "8,000",
                "2,260,000",
            ]
        ],
    }
    rows = twse.parse_institutional(json.dumps(payload), dt.date(2026, 7, 10))
    assert rows == [{
        "stock_id": "2330", "date": "2026-07-10",
        "foreign_net": 2000000, "trust_net": 200000, "dealer_net": 10000,
    }]


def test_parse_ex_rights_uses_per_row_date():
    payload = {
        "stat": "OK",
        "fields": [
            "資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價",
            "權值+息值", "權/息", "漲停價格", "跌停價格", "開盤競價基準",
            "減除股利參考價", "詳細資料",
        ],
        "data": [
            ["115年07月13日", "1907", "永豐餘", "27.75", "26.75", "1.000000", "息",
             "29.40", "24.10", "26.75", "26.75", "..."],
        ],
    }
    rows = twse.parse_ex_rights(json.dumps(payload), dt.date(2026, 7, 7))
    assert rows == [{
        "stock_id": "1907", "ex_date": "2026-07-13",
        "reference_price": 26.75, "prev_close": 27.75,
    }]


def test_parse_disposition_filters_by_period():
    payload = {
        "stat": "OK",
        "fields": ["編號", "公布日期", "證券代號", "證券名稱", "累計",
                    "處置條件", "處置起迄時間", "處置措施", "處置內容", "備註"],
        "data": [
            [1, "115/07/02", "3105", "穩懋", 1, "連續三次",
             "115/07/03～115/07/16", "分盤交易", "...", ""],
        ],
    }
    in_range = twse.parse_disposition(json.dumps(payload), dt.date(2026, 7, 7))
    assert in_range == [{"stock_id": "3105", "date": "2026-07-07", "reason": "處置股"}]
    out_of_range = twse.parse_disposition(json.dumps(payload), dt.date(2026, 6, 1))
    assert out_of_range == []
