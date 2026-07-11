import math

from stock_screener import db
from stock_screener.adjust import compute_adj_factor, get_adjusted_prices, upsert_corporate_action


def test_compute_adj_factor():
    factor = compute_adj_factor(prev_close=100.0, reference_price=95.0)
    assert math.isclose(factor, 100.0 / 95.0)


def test_compute_adj_factor_rejects_nonpositive_reference():
    try:
        compute_adj_factor(prev_close=100.0, reference_price=0)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_get_adjusted_prices_single_ex_dividend_event(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)

    with db.get_conn(db_path) as conn:
        rows = [
            ("2330", "2026-07-08", 100.0, "twse"),
            ("2330", "2026-07-09", 100.0, "twse"),  # day before ex-date
            ("2330", "2026-07-10", 96.0, "twse"),   # ex-date
        ]
        for stock_id, date, close, source in rows:
            conn.execute(
                "INSERT INTO daily_price (stock_id, date, close, source) VALUES (?, ?, ?, ?)",
                (stock_id, date, close, source),
            )
        # Reference price on ex-date 2026-07-10, prev raw close 100.0
        upsert_corporate_action(
            conn, "2330", ex_date="2026-07-10", reference_price=95.0, prev_close=100.0, source="twse",
        )

    with db.get_conn(db_path) as conn:
        adjusted = get_adjusted_prices(conn, "2330")

    factor = 100.0 / 95.0
    by_date = adjusted.set_index("date")["close"]
    assert math.isclose(by_date["2026-07-08"], 100.0 * factor)
    assert math.isclose(by_date["2026-07-09"], 100.0 * factor)
    # On/after the ex-date, price is untouched.
    assert math.isclose(by_date["2026-07-10"], 96.0)


def test_get_adjusted_prices_two_events_compound(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)

    with db.get_conn(db_path) as conn:
        for date, close in [
            ("2026-01-05", 100.0),
            ("2026-04-05", 100.0),  # day before first ex-date
            ("2026-04-06", 95.0),   # first ex-date
            ("2026-07-05", 95.0),   # day before second ex-date
            ("2026-07-06", 90.0),   # second ex-date
        ]:
            conn.execute(
                "INSERT INTO daily_price (stock_id, date, close, source) VALUES (?, ?, ?, ?)",
                ("2330", date, close, "twse"),
            )
        upsert_corporate_action(conn, "2330", "2026-04-06", reference_price=95.0, prev_close=100.0, source="twse")
        upsert_corporate_action(conn, "2330", "2026-07-06", reference_price=90.0, prev_close=95.0, source="twse")

    with db.get_conn(db_path) as conn:
        adjusted = get_adjusted_prices(conn, "2330")

    by_date = adjusted.set_index("date")["close"]
    f1 = 100.0 / 95.0
    f2 = 95.0 / 90.0
    # Before both ex-dates: compounded by both factors.
    assert math.isclose(by_date["2026-01-05"], 100.0 * f1 * f2)
    # Between the two ex-dates: only the second factor still applies.
    assert math.isclose(by_date["2026-04-06"], 95.0 * f2)
    # After both: untouched.
    assert math.isclose(by_date["2026-07-06"], 90.0)
