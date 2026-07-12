import datetime as dt

from stock_screener.dateutil_tw import parse_roc_date


def test_slash_format():
    assert parse_roc_date("115/07/03") == dt.date(2026, 7, 3)


def test_kanji_format():
    assert parse_roc_date("115年07月13日") == dt.date(2026, 7, 13)


def test_seven_digit_format():
    assert parse_roc_date("1150703") == dt.date(2026, 7, 3)


def test_garbage_returns_none():
    assert parse_roc_date("") is None
    assert parse_roc_date("n/a") is None
    assert parse_roc_date("115/13/99") is None
