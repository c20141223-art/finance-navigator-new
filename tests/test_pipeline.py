import datetime as dt

from stock_screener import db, pipeline
from stock_screener.config import load_config
from stock_screener.http_client import RequestOutcome


def test_one_failing_source_does_not_crash_daily_update(tmp_path, monkeypatch):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    config = load_config()

    def boom(*args, **kwargs):
        raise RuntimeError("network exploded")

    def ok_empty_list(*args, **kwargs):
        return RequestOutcome(ok=True, status_code=200, text="[]")

    def ok_empty_dict(*args, **kwargs):
        return RequestOutcome(ok=True, status_code=200, text="{}")

    monkeypatch.setattr("stock_screener.fetchers.twse.fetch_daily_all_raw", boom)
    monkeypatch.setattr("stock_screener.fetchers.tpex.fetch_daily_all_raw", ok_empty_list)
    monkeypatch.setattr("stock_screener.fetchers.twse.fetch_institutional_raw", ok_empty_dict)
    monkeypatch.setattr("stock_screener.fetchers.tpex.fetch_institutional_raw", ok_empty_list)
    monkeypatch.setattr("stock_screener.fetchers.twse.fetch_disposition_raw", ok_empty_dict)
    monkeypatch.setattr(
        "stock_screener.fetchers.risk_list.fetch_tpex_disposition_raw",
        lambda *a, **k: RequestOutcome(ok=True, status_code=200, content=b"<html></html>"),
    )
    monkeypatch.setattr("stock_screener.fetchers.twse.fetch_ex_rights_raw", ok_empty_dict)

    client = object()  # fetchers are monkeypatched, no real client needed
    date = dt.date(2026, 7, 10)

    with db.get_conn(db_path) as conn:
        pipeline.daily_update(conn, client, config, date=date)  # must not raise
        log = conn.execute(
            "SELECT source, status FROM fetch_log WHERE source='twse_daily_all'"
        ).fetchone()

    assert log == ("twse_daily_all", "failure")
