"""Phase 4 tests: 大盤指數狀態、報酬回填、觀察池雷達、HTML email 組裝、
寄件憑證紀律、TAIEX 指數解析。"""

import datetime as dt
import json
import os

import pandas as pd
import pytest

from stock_screener import (db, emailer, market_status, momentum,
                            report_email, returns, watchlist)
from stock_screener.fetchers import twse
from stock_screener.scoring import load_momentum_config


# ── TAIEX 指數解析 ───────────────────────────────────────────────────────

def test_parse_index_history_tables_shape():
    payload = json.dumps({
        "stat": "OK",
        "tables": [{
            "fields": ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"],
            "data": [
                ["115/07/01", "18,000.00", "18,100.00", "17,900.00", "18,050.00"],
                ["115/07/02", "18,050.00", "18,200.00", "18,000.00", "18,180.00"],
            ],
        }],
    })
    rows = twse.parse_index_history(payload, dt.date(2026, 7, 1))
    assert len(rows) == 2
    assert rows[0]["index_id"] == "TAIEX"
    assert rows[0]["date"] == "2026-07-01"
    assert rows[0]["close"] == 18050.0
    assert rows[1]["high"] == 18200.0


def test_parse_index_history_empty_on_no_data():
    assert twse.parse_index_history(json.dumps({"stat": "很抱歉，沒有符合條件的資料"}),
                                    dt.date(2026, 7, 1)) == []


# ── 大盤狀態 ─────────────────────────────────────────────────────────────

def _seed_index(conn, closes, start=dt.date(2026, 3, 2)):
    d = start
    got = 0
    i = 0
    while got < len(closes):
        if d.weekday() < 5:
            conn.execute("INSERT INTO index_price (index_id,date,close,source) "
                         "VALUES ('TAIEX',?,?,'twse')", (d.isoformat(), closes[i]))
            got += 1
            i += 1
        d += dt.timedelta(days=1)
    return d


def test_index_status_above_rising(tmp_path):
    db.init_db(tmp_path / "m.db")
    with db.get_conn(tmp_path / "m.db") as conn:
        closes = [10000 + i * 10 for i in range(70)]   # steadily rising
        _seed_index(conn, closes)
        asof = conn.execute("SELECT MAX(date) FROM index_price").fetchone()[0]
        st = market_status.compute_index_status(conn, asof)
    assert st["index_available"]
    assert st["index_vs_60ma_pct"] > 0        # last close above its 60MA
    assert st["ma60_direction"] == "up"


def test_index_status_insufficient_history(tmp_path):
    db.init_db(tmp_path / "m.db")
    with db.get_conn(tmp_path / "m.db") as conn:
        _seed_index(conn, [10000.0] * 30)      # < 60 bars
        asof = conn.execute("SELECT MAX(date) FROM index_price").fetchone()[0]
        st = market_status.compute_index_status(conn, asof)
    assert st["index_available"] is False
    assert st["index_60ma"] is None


def test_build_market_status_merges_breadth(tmp_path):
    db.init_db(tmp_path / "m.db")
    with db.get_conn(tmp_path / "m.db") as conn:
        _seed_index(conn, [10000 + i for i in range(65)])
        asof = conn.execute("SELECT MAX(date) FROM index_price").fetchone()[0]
        ms = market_status.build_market_status(conn, asof,
                                               {"bullish_alignment_pct": 42.5, "universe": 800})
    assert ms["bullish_alignment_pct"] == 42.5
    assert ms["breadth_universe"] == 800


# ── 報酬回填 T+5/20/60 + MFE/MAE ──────────────────────────────────────────

def test_backfill_returns_computes_forward_returns(tmp_path):
    db.init_db(tmp_path / "m.db")
    with db.get_conn(tmp_path / "m.db") as conn:
        # 65 rising bars: close = 100, 101, ... so T+5 return from bar0 = 5%
        d = dt.date(2026, 1, 1)
        dates = []
        while len(dates) < 65:
            if d.weekday() < 5:
                dates.append(d.isoformat())
            d += dt.timedelta(days=1)
        for i, dte in enumerate(dates):
            cl = 100.0 + i
            conn.execute("INSERT INTO daily_price (stock_id,date,open,high,low,close,volume,source)"
                         " VALUES ('2330',?,?,?,?,?,1000,'twse')",
                         (dte, cl, cl + 1, cl - 1, cl))
        conn.execute("INSERT INTO triggers (date,profile,stock_id) VALUES (?, 'momentum','2330')",
                     (dates[0],))
        n = returns.backfill_returns(conn)
        row = conn.execute("SELECT return_t5, return_t20, return_t60, mfe, mae "
                           "FROM triggers WHERE stock_id='2330'").fetchone()
    assert n == 1
    assert row[0] == pytest.approx(5.0, abs=0.01)     # 105/100 - 1
    assert row[1] == pytest.approx(20.0, abs=0.01)
    assert row[2] == pytest.approx(60.0, abs=0.01)
    assert row[3] == pytest.approx(21.0, abs=0.01)    # max high in next 20 = 121 → +21%
    assert row[4] == pytest.approx(0.0, abs=0.01)     # min low in next 20 = bar1 low 100 = entry → 0%


def test_backfill_returns_is_fill_once(tmp_path):
    db.init_db(tmp_path / "m.db")
    with db.get_conn(tmp_path / "m.db") as conn:
        d = dt.date(2026, 1, 1)
        dates = []
        while len(dates) < 10:
            if d.weekday() < 5:
                dates.append(d.isoformat())
            d += dt.timedelta(days=1)
        for i, dte in enumerate(dates):
            conn.execute("INSERT INTO daily_price (stock_id,date,open,high,low,close,volume,source)"
                         " VALUES ('2330',?,?,?,?,?,1000,'twse')",
                         (dte, 100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i))
        conn.execute("INSERT INTO triggers (date,profile,stock_id) VALUES (?, 'momentum','2330')",
                     (dates[0],))
        first = returns.backfill_returns(conn)   # fills t5 (5 bars ahead exist)
        second = returns.backfill_returns(conn)  # t5 now set → nothing new for it
        r5 = conn.execute("SELECT return_t5 FROM triggers").fetchone()[0]
    assert first == 1
    assert r5 is not None
    # second run makes no further change to t5 (t20/t60 still uncomputable at 10 bars)
    assert second == 0


# ── 觀察池雷達 ───────────────────────────────────────────────────────────

def test_load_watchlist_ignores_pseudo_comments(tmp_path):
    p = tmp_path / "wl.json"
    p.write_text(json.dumps({
        "_about": "說明文字", "_schema": {"x": "y"},
        "consecutive_bottom_alert_days": 4,
        "stocks": [{"stock_id": "2330", "note": "核心"}, "2317"],
    }), encoding="utf-8")
    cfg = watchlist.load_watchlist(p)
    assert "_about" not in cfg
    assert cfg["consecutive_bottom_alert_days"] == 4
    assert cfg["stocks"][0] == {"stock_id": "2330", "note": "核心"}
    assert cfg["stocks"][1] == {"stock_id": "2317", "note": None}


def _seed_scored_universe(conn, n_days=65):
    d = dt.date(2026, 2, 2)
    dates = []
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += dt.timedelta(days=1)
    for sid, slope in (("2330", 2.0), ("2454", -0.1)):
        for i, dte in enumerate(dates):
            cl = 200.0 + slope * i
            conn.execute("INSERT INTO daily_price (stock_id,date,open,high,low,close,volume,source)"
                         " VALUES (?,?,?,?,?,?,50000,'twse')",
                         (sid, dte, cl, cl * 1.01, cl * 0.99, cl))
        conn.execute("INSERT OR REPLACE INTO stock_meta (stock_id,name,market,is_active) "
                     "VALUES (?,?, '上市',1)", (sid, sid))
    return dates


def test_watchlist_radar_ranks_and_flags_insufficient(tmp_path):
    db.init_db(tmp_path / "m.db")
    mcfg = load_momentum_config()
    with db.get_conn(tmp_path / "m.db") as conn:
        dates = _seed_scored_universe(conn)
        run = dt.date.fromisoformat(dates[-1])
        wl_cfg = {"stocks": [{"stock_id": "2330", "note": None},
                             {"stock_id": "2454", "note": None},
                             {"stock_id": "0000", "note": None}],  # no data
                  "consecutive_bottom_alert_days": 3, "score_profile": "momentum"}
        res = watchlist.build_watchlist_radar(conn, mcfg, run, wl_cfg)
        n = watchlist.persist_watchlist(conn, res, mcfg.version_hash)
    by = {e.stock_id: e for e in res.entries}
    assert by["2330"].rank == 1 and by["2330"].total >= by["2454"].total
    assert by["0000"].insufficient and by["0000"].rank is None
    assert n == 3
    assert res.n_stocks == 2   # two scorable


def test_watchlist_rank_change_vs_prior(tmp_path):
    db.init_db(tmp_path / "m.db")
    mcfg = load_momentum_config()
    with db.get_conn(tmp_path / "m.db") as conn:
        dates = _seed_scored_universe(conn)
        run = dt.date.fromisoformat(dates[-1])
        # seed a prior watchlist snapshot where 2454 was #1, 2330 #2
        prior = dates[-2]
        for sid, rk in (("2454", 1), ("2330", 2)):
            conn.execute("INSERT INTO triggers (date,profile,rank,total_score,stock_id) "
                         "VALUES (?, 'watchlist',?,50,?)", (prior, rk, sid))
        wl_cfg = {"stocks": [{"stock_id": "2330", "note": None},
                             {"stock_id": "2454", "note": None}],
                  "consecutive_bottom_alert_days": 3}
        res = watchlist.build_watchlist_radar(conn, mcfg, run, wl_cfg)
    by = {e.stock_id: e for e in res.entries}
    # 2330 today #1 vs prior #2 → moved up (+1); 2454 today #2 vs prior #1 → -1
    assert by["2330"].rank_change == 1
    assert by["2454"].rank_change == -1


# ── HTML email 組裝 ──────────────────────────────────────────────────────

def _minimal_screen():
    return momentum.ScreenResult(
        date="2026-07-07", universe_size=100, passed_filters=40, all_scored=40,
        market_regime={"bullish_alignment_pct": 55.0, "universe": 40},
        config_version="abc123",
        scores=[momentum.StockScore("2330", "台積電", 88.0,
                {"fundamental": 80, "chips": 90, "technical": 95},
                {}, rank=1)],
    )


def test_build_report_has_all_blocks_and_banner():
    from stock_screener.reversal import RadarResult
    ctx = report_email.ReportContext(
        date="2026-07-07",
        market_status={"index_available": True, "index_close": 19000.0,
                       "index_60ma": 18500.0, "index_vs_60ma_pct": 2.7,
                       "ma60_direction": "up", "bullish_alignment_pct": 55.0,
                       "breadth_universe": 40},
        momentum=_minimal_screen(),
        momentum_new=[momentum.StockScore("2330", "台積電", 88.0, {}, {}, rank=1)],
        momentum_dropped=[{"stock_id": "1101", "name": "台泥", "prev_rank": 5}],
        reversal=RadarResult(date="2026-07-07", qualified=300, triggers=[], control_group=[]),
        watchlist=None,
        fetch_failures=[{"source": "tpex_institutional", "error": "HTTP 500"}],
        momentum_version="abc123", reversal_version="def456", top_n=30,
    )
    subject, html_doc, text = report_email.build_report(ctx)
    assert "① 大盤狀態" in html_doc and "② 順勢 Top 30" in html_doc
    assert "③ 反轉雷達" in html_doc and "④ 觀察池雷達" in html_doc
    assert "跌出榜" in html_doc and "台泥" in html_doc            # dropped highlighted
    assert "本日缺料" in html_doc and "tpex_institutional" in html_doc  # failure banner
    assert "不構成任何投資建議" in html_doc or "不構成任何投資建議或買賣邀約" in html_doc
    assert "⚠缺料" in subject
    assert "台積電" in text


def test_build_report_survives_empty_screen():
    empty = momentum.ScreenResult(date="2026-07-07", universe_size=0, passed_filters=0,
                                  all_scored=0, scores=[], market_regime={}, config_version="v")
    ctx = report_email.ReportContext(
        date="2026-07-07", market_status={"index_available": False, "history_len": 3},
        momentum=empty, reversal=None, watchlist=None, top_n=30)
    subject, html_doc, text = report_email.build_report(ctx)
    assert "尚無足夠指數資料" in html_doc
    assert subject.startswith("[台股選股]")


# ── 寄件憑證紀律 ─────────────────────────────────────────────────────────

def test_emailer_raises_when_secrets_missing(monkeypatch):
    for var in (emailer.ENV_USER, emailer.ENV_PASSWORD, emailer.ENV_TO):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(emailer.MissingCredentialError) as ei:
        emailer.send_report("s", "<p>x</p>")
    msg = str(ei.value)
    assert emailer.ENV_USER in msg and emailer.ENV_PASSWORD in msg and emailer.ENV_TO in msg


def test_emailer_reports_only_the_missing_one(monkeypatch):
    monkeypatch.setenv(emailer.ENV_USER, "me@gmail.com")
    monkeypatch.setenv(emailer.ENV_PASSWORD, "app-pw")
    monkeypatch.delenv(emailer.ENV_TO, raising=False)
    with pytest.raises(emailer.MissingCredentialError) as ei:
        emailer.send_report("s", "<p>x</p>")
    assert emailer.ENV_TO in str(ei.value)
    assert emailer.ENV_USER not in str(ei.value)
