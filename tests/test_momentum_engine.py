"""End-to-end tests for the momentum screen on a synthetic database with
hand-computable numbers. Every expected score below can be re-derived from
config/momentum.yaml by hand — the same procedure the user's acceptance
check uses on real data."""

import datetime as dt
import json

import pytest

from stock_screener import db, momentum
from stock_screener.adjust import upsert_corporate_action
from stock_screener.scoring import load_momentum_config

SCREEN_DATE = dt.date(2026, 7, 11)


def _trading_dates(n=60, end=SCREEN_DATE):
    """n weekday dates ending at `end`, ascending."""
    dates = []
    cursor = end
    while len(dates) < n:
        if cursor.weekday() < 5:
            dates.append(cursor)
        cursor -= dt.timedelta(days=1)
    return list(reversed(dates))


DATES = _trading_dates()


def _seed_stock(conn, stock_id, name="測試", *, closes=None, volumes=None,
                days=60, trust_streak=0, foreign_daily=0, yoys=None,
                in_risk=False):
    """Insert `days` rows of price history plus institutional/revenue rows.
    closes/volumes: constants or per-day lists aligned to the LAST `days`
    trading dates."""
    dates = DATES[-days:]
    closes = closes if closes is not None else [100.0] * days
    volumes = volumes if volumes is not None else [1000] * days
    if isinstance(closes, (int, float)):
        closes = [float(closes)] * days
    if isinstance(volumes, int):
        volumes = [volumes] * days

    for d, c, v in zip(dates, closes, volumes):
        conn.execute(
            "INSERT INTO daily_price (stock_id, date, close, volume, source) VALUES (?, ?, ?, ?, 'twse')",
            (stock_id, d.isoformat(), c, v),
        )
    conn.execute(
        "INSERT OR REPLACE INTO stock_meta (stock_id, name, market, is_active) VALUES (?, ?, '上市', 1)",
        (stock_id, name),
    )
    # trust streak: positive trust_net on the newest `trust_streak` dates
    for i, d in enumerate(reversed(DATES)):
        trust = 50 if i < trust_streak else 0
        conn.execute(
            "INSERT INTO institutional (stock_id, date, foreign_net, trust_net, dealer_net, source) "
            "VALUES (?, ?, ?, ?, 0, 'twse')",
            (stock_id, d.isoformat(), foreign_daily, trust),
        )
    if yoys:  # newest first, e.g. [35, 20, 10]
        months = ["2026-06", "2026-05", "2026-04", "2026-03"]
        for ym, y in zip(months, yoys):
            conn.execute(
                "INSERT INTO monthly_revenue (stock_id, year_month, revenue, yoy, source) "
                "VALUES (?, ?, 1000, ?, 'sii')",
                (stock_id, ym, y),
            )
    if in_risk:
        # risk must be seeded on the EFFECTIVE screen date (last trading
        # date), not the requested date — 2026-07-11 is a Saturday and the
        # engine correctly screens as-of Friday 07-10.
        conn.execute(
            "INSERT INTO risk_list (date, stock_id, reason, source) VALUES (?, ?, '處置股', 'twse')",
            (DATES[-1].isoformat(), stock_id),
        )


@pytest.fixture
def screen_db(tmp_path):
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    return db_path


def test_filters(screen_db):
    mcfg = load_momentum_config()
    with db.get_conn(screen_db) as conn:
        _seed_stock(conn, "1001", "通過")                       # passes everything
        _seed_stock(conn, "1002", "太便宜", closes=9.5)          # price <= 10
        _seed_stock(conn, "1003", "太冷清", volumes=400)         # avg vol <= 500
        _seed_stock(conn, "1004", "太新", days=59)               # history < 60
        _seed_stock(conn, "1005", "處置中", in_risk=True)        # on risk list
        _seed_stock(conn, "0050", "ETF")                        # not common stock
        result = momentum.run_screen(conn, mcfg, SCREEN_DATE)

    assert result.universe_size == 6
    assert result.passed_filters == 1
    assert result.scores[0].stock_id == "1001"


def test_factor_values_and_scores_hand_computed(screen_db):
    """One stock with every factor pinned to a hand-computable value:

    - closes: 59 days at 100, last day 110 → 60MA = (59*100+110)/60
      = 6010/60 = 100.1667; bias = (110/100.1667 - 1)*100 = +9.8169% → 60分
      (range [8,20)). fundamental? revenue yoys [35, 20, 10] newest-first:
      yoy=35 → 100分 (gt 30); trend 35>20>10 consecutive months → True →
      100分; dimension capped 200→100.
    - volumes: first 40 days 1000, last 20 days: 15 days 1000 + last 5 days
      2000 → 5日均量 2000, 20日均量 = (15*1000+5*2000)/20 = 1250;
      expansion = 2000/1250 = 1.6 → 100分 (range [1.2,2.5)).
    - trust_streak = 6 → 70分 (gte 5). foreign_daily = 150/day → 近5日合計
      750 ÷ 近5日總量 5*2000=10000 → 0.075 → 60分 (gt 0.05).
    - chips = min(70+60,100)=100... wait 130 → capped 100.
      technical = min(60+100,100)=100 → capped.
      fundamental = min(100+100,100)=100.
      total = 0.30*100 + 0.35*100 + 0.35*100 = 100.
    """
    mcfg = load_momentum_config()
    closes = [100.0] * 59 + [110.0]
    volumes = [1000] * 55 + [2000] * 5
    with db.get_conn(screen_db) as conn:
        _seed_stock(conn, "1001", "滿分股", closes=closes, volumes=volumes,
                    trust_streak=6, foreign_daily=150, yoys=[35, 20, 10])
        result = momentum.run_screen(conn, mcfg, SCREEN_DATE)

    s = result.scores[0]
    f = {k: v for dim in s.factor_detail.values() for k, v in dim.items()}

    assert f["revenue_yoy"]["raw"] == 35 and f["revenue_yoy"]["score"] == 100
    assert f["revenue_trend_3m"]["raw"] is True and f["revenue_trend_3m"]["score"] == 100
    assert f["trust_buy_streak"]["raw"] == 6 and f["trust_buy_streak"]["score"] == 70
    assert f["foreign_net_ratio_5d"]["raw"] == pytest.approx(0.075)
    assert f["foreign_net_ratio_5d"]["score"] == 60
    assert f["bias_60ma"]["raw"] == pytest.approx((110 / (6010 / 60) - 1) * 100)
    assert f["bias_60ma"]["score"] == 60
    assert f["volume_expansion"]["raw"] == pytest.approx(1.6)
    assert f["volume_expansion"]["score"] == 100

    assert s.dimension_scores == {"fundamental": 100, "chips": 100, "technical": 100}
    assert s.total == 100.0


def test_partial_scores_weighted_total(screen_db):
    """Weighted-total arithmetic without any capping in play:
    fundamental = 30 (yoy 5 → gt 0 bin) + 0 (no trend) = 30
    chips = 40 (streak 3) + 0 (no foreign) = 40
    technical = 0 (bias 0% on flat closes → range[0,8) → 100!)"""
    mcfg = load_momentum_config()
    with db.get_conn(screen_db) as conn:
        _seed_stock(conn, "1001", closes=100.0, volumes=1000,
                    trust_streak=3, foreign_daily=0, yoys=[5, 6, 7])
        result = momentum.run_screen(conn, mcfg, SCREEN_DATE)

    s = result.scores[0]
    # flat closes: bias = 0% → range [0,8) → 100; volume_expansion = 1.0 → else 0
    assert s.dimension_scores["fundamental"] == 30
    assert s.dimension_scores["chips"] == 40
    assert s.dimension_scores["technical"] == 100
    assert s.total == pytest.approx(0.30 * 30 + 0.35 * 40 + 0.35 * 100)


def test_missing_revenue_scores_zero_not_crash(screen_db):
    mcfg = load_momentum_config()
    with db.get_conn(screen_db) as conn:
        _seed_stock(conn, "1001", yoys=None)
        result = momentum.run_screen(conn, mcfg, SCREEN_DATE)
    s = result.scores[0]
    assert s.dimension_scores["fundamental"] == 0


def test_trust_streak_broken_by_missing_day(screen_db):
    """A day with no institutional row breaks the streak (streak=6 seeded,
    then the row 3 days back is deleted → streak observed = 2)."""
    mcfg = load_momentum_config()
    with db.get_conn(screen_db) as conn:
        _seed_stock(conn, "1001", trust_streak=6)
        third_newest = DATES[-3]
        conn.execute(
            "DELETE FROM institutional WHERE stock_id='1001' AND date=?",
            (third_newest.isoformat(),),
        )
        result = momentum.run_screen(conn, mcfg, SCREEN_DATE)
    f = {k: v for dim in result.scores[0].factor_detail.values() for k, v in dim.items()}
    assert f["trust_buy_streak"]["raw"] == 2


def test_bias_uses_adjusted_close(screen_db):
    """An ex-dividend event mid-window must change bias_60ma: raw closes are
    flat at 100 but a corporate action with factor 100/95 inflates the
    pre-ex history, pulling the adjusted 60MA above the last close."""
    mcfg = load_momentum_config()
    ex_date = DATES[-10]
    with db.get_conn(screen_db) as conn:
        _seed_stock(conn, "1001", closes=100.0)
        upsert_corporate_action(conn, "1001", ex_date.isoformat(),
                                reference_price=95.0, prev_close=100.0, source="twse")
        result = momentum.run_screen(conn, mcfg, SCREEN_DATE)

    f = {k: v for dim in result.scores[0].factor_detail.values() for k, v in dim.items()}
    factor = 100.0 / 95.0
    # 50 pre-ex days adjusted to 100*factor, last 10 days raw 100
    ma60 = (50 * 100 * factor + 10 * 100) / 60
    expected_bias = (100 / ma60 - 1) * 100
    assert f["bias_60ma"]["raw"] == pytest.approx(expected_bias)
    assert expected_bias < 0  # below MA → else bin → 0分
    assert f["bias_60ma"]["score"] == 0


def test_top_n_control_group_and_persistence(screen_db):
    """60 passing stocks with strictly decreasing foreign ratios → ranks are
    deterministic; verify top 30 + control 31-50 land in triggers with the
    right flags and factor_detail JSON round-trips."""
    mcfg = load_momentum_config()
    with db.get_conn(screen_db) as conn:
        for i in range(60):
            stock_id = str(1001 + i)
            # foreign_daily descending: 300, 295, ... spreads ratio over bins;
            # add trust streaks to spread totals further
            _seed_stock(conn, stock_id, f"S{i}",
                        trust_streak=(10 if i < 20 else 4 if i < 40 else 0),
                        foreign_daily=300 - i * 5)
        result = momentum.run_screen(conn, mcfg, SCREEN_DATE)
        momentum.record_config_version(conn, mcfg, result.date)
        n = momentum.persist_triggers(conn, result)

        assert result.all_scored == 60
        assert n == 50  # top 30 + control 20
        rows = conn.execute(
            "SELECT rank, stock_id, is_control_group, total_score, factor_detail, config_version "
            "FROM triggers WHERE profile='momentum' ORDER BY rank"
        ).fetchall()

    assert len(rows) == 50
    assert [r[0] for r in rows] == list(range(1, 51))
    assert all(r[2] == 0 for r in rows[:30])
    assert all(r[2] == 1 for r in rows[30:])
    # totals non-increasing down the ranks
    totals = [r[3] for r in rows]
    assert all(a >= b for a, b in zip(totals, totals[1:]))
    # factor_detail JSON is complete and re-derivable
    detail = json.loads(rows[0][4])
    assert set(detail["dimensions"]) == {"fundamental", "chips", "technical"}
    assert "trust_buy_streak" in detail["factors"]["chips"]
    assert rows[0][5] == mcfg.version_hash


def test_persist_is_idempotent(screen_db):
    mcfg = load_momentum_config()
    with db.get_conn(screen_db) as conn:
        for i in range(5):
            _seed_stock(conn, str(1001 + i), foreign_daily=100 - i)
        result = momentum.run_screen(conn, mcfg, SCREEN_DATE)
        momentum.persist_triggers(conn, result)
        momentum.persist_triggers(conn, result)  # re-run same date
        count = conn.execute(
            "SELECT COUNT(*) FROM triggers WHERE profile='momentum'"
        ).fetchone()[0]
    assert count == 5  # replaced, not duplicated


def test_market_regime_breadth(screen_db):
    """Rising stock (close > MA20 > MA60) vs flat stock → 50% breadth."""
    mcfg = load_momentum_config()
    rising = [100.0 + i * 0.5 for i in range(60)]
    with db.get_conn(screen_db) as conn:
        _seed_stock(conn, "1001", closes=rising)
        _seed_stock(conn, "1002", closes=100.0)
        result = momentum.run_screen(conn, mcfg, SCREEN_DATE)
    assert result.market_regime["universe"] == 2
    assert result.market_regime["bullish_alignment_pct"] == pytest.approx(50.0)
    assert result.market_regime["index_vs_60ma"] is None  # Phase 4 明確缺項


def test_insufficient_history_raises(screen_db):
    mcfg = load_momentum_config()
    with db.get_conn(screen_db) as conn:
        _seed_stock(conn, "1001", days=30)
        with pytest.raises(RuntimeError, match="不足"):
            momentum.run_screen(conn, mcfg, SCREEN_DATE)
