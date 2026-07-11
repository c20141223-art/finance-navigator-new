import datetime as dt
import json

import pytest

from stock_screener.fetchers import tpex
from stock_screener.schema_guard import SchemaMismatchError


def test_parse_daily_all_happy_path():
    payload = [{
        "Code": "6488", "Name": "環球晶", "Close": "500", "Open": "495",
        "High": "505", "Low": "490", "TradingVolume": "1000000",
        "TransactionAmount": "500000000",
    }]
    rows = tpex.parse_daily_all(json.dumps(payload))
    assert rows == [{
        "stock_id": "6488", "name": "環球晶", "open": 495.0, "high": 505.0,
        "low": 490.0, "close": 500.0, "volume": 1000000, "turnover": 500000000,
    }]


def test_parse_daily_all_missing_field_raises():
    with pytest.raises(SchemaMismatchError):
        tpex.parse_daily_all(json.dumps([{"Code": "6488"}]))


def test_parse_daily_history_happy_path():
    row = ["6488", "環球晶", "500", "+5", "495", "505", "490",
           "1000000", "500000000", "800", "499", "10", "501", "10", "100000000",
           "500", "550", "450"]
    assert len(row) == len(tpex._AADATA_COLUMNS)
    payload = {"aaData": [row]}
    rows = tpex.parse_daily_history(json.dumps(payload), dt.date(2026, 7, 10))
    assert rows == [{
        "stock_id": "6488", "date": "2026-07-10", "open": 495.0, "high": 505.0,
        "low": 490.0, "close": 500.0, "volume": 1000000, "turnover": 500000000,
    }]


def test_parse_daily_history_wrong_column_count_raises():
    payload = {"aaData": [["6488", "環球晶", "500"]]}
    with pytest.raises(SchemaMismatchError):
        tpex.parse_daily_history(json.dumps(payload), dt.date(2026, 7, 10))


def test_parse_daily_history_no_data_key_returns_empty():
    rows = tpex.parse_daily_history(json.dumps({"some_other_key": []}), dt.date(2026, 7, 10))
    assert rows == []
