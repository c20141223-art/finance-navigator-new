import datetime as dt

from stock_screener import db, loaders


def test_upsert_daily_price_converts_shares_to_lots(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    rows = [{"stock_id": "2330", "date": "2026-07-10", "open": 1000, "high": 1010,
             "low": 995, "close": 1005, "volume": 50000000, "turnover": 5_000_000_000}]
    with db.get_conn(db_path) as conn:
        n = loaders.upsert_daily_price(conn, rows, source="twse")
        assert n == 1
        stored = conn.execute(
            "SELECT volume FROM daily_price WHERE stock_id='2330' AND date='2026-07-10'"
        ).fetchone()
    assert stored[0] == 50000  # 股 -> 張


def test_upsert_daily_price_is_idempotent(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    rows = [{"stock_id": "2330", "date": "2026-07-10", "close": 1005, "volume": 1000}]
    with db.get_conn(db_path) as conn:
        loaders.upsert_daily_price(conn, rows, source="twse")
        loaders.upsert_daily_price(conn, rows, source="twse")
        count = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
    assert count == 1


def test_upsert_daily_price_skips_rows_without_close(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    rows = [{"stock_id": "2330", "date": "2026-07-10", "close": None}]
    with db.get_conn(db_path) as conn:
        n = loaders.upsert_daily_price(conn, rows, source="twse")
    assert n == 0


def test_log_fetch_result_records_row(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    with db.get_conn(db_path) as conn:
        loaders.log_fetch_result(conn, dt.date(2026, 7, 10), "twse_daily_all", "failure", None, "boom")
        row = conn.execute("SELECT source, status, error_message FROM fetch_log").fetchone()
    assert row == ("twse_daily_all", "failure", "boom")


def test_update_active_flags_marks_missing_stock_inactive(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    with db.get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_meta (stock_id, name, market, is_active, missing_days) "
            "VALUES ('9999', 'delisted-ish', '上市', 1, 4)"
        )
        loaders.update_active_flags(conn, dt.date(2026, 7, 10), inactive_after_missing_days=5)
        row = conn.execute(
            "SELECT missing_days, is_active FROM stock_meta WHERE stock_id='9999'"
        ).fetchone()
    assert row == (5, 0)


def test_update_active_flags_resets_counter_when_seen(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    with db.get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO stock_meta (stock_id, name, market, is_active, missing_days) "
            "VALUES ('2330', 'TSMC', '上市', 1, 3)"
        )
        conn.execute(
            "INSERT INTO daily_price (stock_id, date, close, source) VALUES ('2330', '2026-07-10', 1000, 'twse')"
        )
        loaders.update_active_flags(conn, dt.date(2026, 7, 10), inactive_after_missing_days=5)
        row = conn.execute(
            "SELECT missing_days, is_active, last_seen_date FROM stock_meta WHERE stock_id='2330'"
        ).fetchone()
    assert row == (0, 1, "2026-07-10")


def test_upsert_institutional_converts_shares_to_lots(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    rows = [{"stock_id": "2330", "date": "2026-07-10",
             "foreign_net": 2000000, "trust_net": -1500, "dealer_net": None}]
    with db.get_conn(db_path) as conn:
        loaders.upsert_institutional(conn, rows, source="twse")
        stored = conn.execute(
            "SELECT foreign_net, trust_net, dealer_net FROM institutional "
            "WHERE stock_id='2330'"
        ).fetchone()
    assert stored == (2000, -2, None)  # 股 -> 張
