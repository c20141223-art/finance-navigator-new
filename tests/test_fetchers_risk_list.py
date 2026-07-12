import datetime as dt
import json

import pytest

from stock_screener.fetchers import risk_list
from stock_screener.schema_guard import SchemaMismatchError

_REC = {
    "Date": "1150707", "SecuritiesCompanyCode": "3105", "CompanyName": "穩懋",
    "DispositionPeriod": "115/07/03～115/07/16",
    "DispositionReasons": "...", "DisposalCondition": "...",
}


def test_in_range_included():
    rows = risk_list.parse_tpex_disposition(json.dumps([_REC]), dt.date(2026, 7, 7), "t")
    assert rows == [{"stock_id": "3105", "date": "2026-07-07", "reason": "處置股"}]


def test_out_of_range_excluded():
    rows = risk_list.parse_tpex_disposition(json.dumps([_REC]), dt.date(2026, 6, 1), "t")
    assert rows == []


def test_unparsable_period_over_flags():
    rec = dict(_REC, DispositionPeriod="不明格式")
    rows = risk_list.parse_tpex_disposition(json.dumps([rec]), dt.date(2026, 6, 1), "t")
    assert len(rows) == 1


def test_missing_field_raises():
    with pytest.raises(SchemaMismatchError):
        risk_list.parse_tpex_disposition(json.dumps([{"foo": 1}]), dt.date(2026, 7, 7), "t")
