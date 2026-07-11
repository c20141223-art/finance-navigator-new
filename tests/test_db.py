import sqlite3

from stock_screener import db


def test_init_db_creates_all_tables(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)

    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()

    expected = {
        "daily_price", "institutional", "monthly_revenue", "risk_list",
        "stock_meta", "corporate_action", "triggers", "config_versions",
        "fetch_log",
    }
    assert expected.issubset(tables)


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    db.init_db(db_path)  # must not raise


def test_daily_price_primary_key_upsert(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)

    with db.get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO daily_price (stock_id, date, close, source) VALUES (?, ?, ?, ?)",
            ("2330", "2026-07-10", 1000.0, "twse"),
        )
        conn.execute(
            """
            INSERT INTO daily_price (stock_id, date, close, source) VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_id, date) DO UPDATE SET close = excluded.close
            """,
            ("2330", "2026-07-10", 1005.0, "twse"),
        )

    with db.get_conn(db_path) as conn:
        rows = conn.execute("SELECT close FROM daily_price WHERE stock_id='2330'").fetchall()
    assert rows == [(1005.0,)]
