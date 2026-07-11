import pytest

from stock_screener.fetchers.common import find_column, find_price_table
from stock_screener.schema_guard import SchemaMismatchError


def test_find_column_matches_by_keyword():
    fields = ["證券代號", "證券名稱", "外資買賣超股數", "外資自營商買賣超股數", "投信買賣超股數"]
    idx = find_column(fields, ("外資", "買賣超"), must_not_contain=("自營商",), source="test")
    assert fields[idx] == "外資買賣超股數"


def test_find_column_no_match_raises():
    fields = ["證券代號", "證券名稱"]
    with pytest.raises(SchemaMismatchError):
        find_column(fields, ("收盤價",), source="test")


def test_find_column_ambiguous_raises():
    fields = ["外資買賣超股數", "外資買賣超股數(調整後)"]
    with pytest.raises(SchemaMismatchError):
        find_column(fields, ("外資", "買賣超"), source="test")


def test_find_price_table_locates_correct_numbered_table():
    payload = {
        "fields1": ["指數", "收盤指數"],
        "data1": [["加權指數", "17000"]],
        "fields2": ["證券代號", "證券名稱", "收盤價"],
        "data2": [["2330", "台積電", "1000"]],
    }
    fields, data = find_price_table(payload, source="test")
    assert fields == ["證券代號", "證券名稱", "收盤價"]
    assert data == [["2330", "台積電", "1000"]]


def test_find_price_table_missing_raises():
    payload = {"fields1": ["指數", "收盤指數"], "data1": [["加權指數", "17000"]]}
    with pytest.raises(SchemaMismatchError):
        find_price_table(payload, source="test")
