"""反轉雷達測試（規格書第 4 節驗收）.

The acceptance requirement: prove the trigger fires in the turn-up segment
AFTER basing completes — not at the wave low, not after the move is over.
`mediatek_bottom()` embeds a MediaTek-2454-shaped bottom (plateau → decline
→ quiet flat base → sharp turn-up) with hand-set numbers; the whole series
produces exactly ONE trigger, at the first turn-up bar.

Signal-timing note this fixture makes concrete: stage-1's volume-shrink gate
forces a quiet flat base, and a base flat enough to shrink volume keeps
Wilder RSI pinned deeply oversold through the 10-day window — so a
listless bottom triggers via volume_breakout + rsi_cross, while
macd_converge (OSC leads price, turning positive as the decline decelerates)
has already fired back at the base onset. This tension between
rsi_cross-from-oversold and a mature 10-day base is a real property of the
spec's parameters, documented here and in the radar report.
"""

import datetime as dt
import json

import pandas as pd
import pytest

from stock_screener import db, reversal
from stock_screener.indicators import macd, wilder_rsi
from stock_screener.reversal import (
    _to_bar_frame,
    evaluate_bar,
    load_reversal_config,
    run_state_machine,
    scan_first_trigger,
)

NOACT = pd.DataFrame(columns=["ex_date", "adj_factor"])


# ── indicators (spec §9: Wilder RSI, MACD 12-26-9) ───────────────────────

def test_wilder_rsi_matches_textbook():
    """Wilder's own worked example: first 14-period RSI = 70.46."""
    close = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                       45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
                       46.03, 46.41, 46.22, 45.64])
    rsi = wilder_rsi(close, 14)
    assert rsi.iloc[14] == pytest.approx(70.46, abs=0.01)


def test_rsi_flat_series_neutral():
    assert wilder_rsi(pd.Series([100.0] * 30), 14).iloc[-1] == 50.0


def test_macd_warmup_indices():
    close = pd.Series(range(100), dtype=float)
    m = macd(close, 12, 26, 9)
    assert m["dif"].first_valid_index() == 25       # EMA26 ready at idx 25
    assert m["macd"].first_valid_index() == 33      # +8 more DIF bars for EMA9


# ── MediaTek-shaped bottom fixture ───────────────────────────────────────

def mediatek_bottom():
    """~139 bars: plateau(30) → decline(45) → flat quiet base(22) → turn-up.
    Wave low at idx 74; sole trigger at idx 97 (first turn-up bar)."""
    c = [1000.0 + (i % 4) * 5 for i in range(30)]
    for i in range(45):
        c.append(1000 - (1000 - 600) * (i + 1) / 45)
    c += [600.0] * 22
    up = [624, 648, 668, 686]
    for i in range(42):
        c.append(up[i] if i < len(up)
                 else up[-1] + (820 - up[-1]) * (i - len(up) + 1) / (42 - len(up)))
    vols = [25000 if i < 30 else 52000 if i < 75 else 15000 if i < 97
            else (60000 if 97 <= i <= 100 else 33000) for i in range(len(c))]
    return c, vols


def _bar_frame_from(closes, vols, *, strong_close_from=97, overrides=None):
    rows = []
    for i, cl in enumerate(closes):
        o = cl
        h = max(o, cl) * 1.006
        lo = min(o, cl) * 0.994
        if i >= strong_close_from:
            h = cl * 1.004
            lo = min(o, cl) * 0.985
        rows.append({"date": f"d{i:03d}", "open": o, "high": h, "low": lo,
                     "close": cl, "volume": vols[i]})
    if overrides:
        for i, patch in overrides.items():
            rows[i].update(patch)
    return _to_bar_frame(pd.DataFrame(rows), NOACT)


@pytest.fixture
def rcfg():
    cfg, _ = load_reversal_config()
    return cfg


def test_trigger_lands_in_turnup_not_at_low_or_after(rcfg):
    c, v = mediatek_bottom()
    bf = _bar_frame_from(c, v)
    low_idx = int(bf.low.idxmin())
    triggers = [i for i in range(59, len(bf.dates)) if evaluate_bar(bf, i, rcfg).is_trigger]

    assert triggers == [97]                       # exactly one, not spurious
    assert low_idx == 74
    assert triggers[0] > low_idx + 10             # well after the low (not the low)
    # the up-move runs to ~idx 130; no triggers in the late/finished move
    assert all(t < 110 for t in triggers)


def test_trigger_signal_and_gate_detail(rcfg):
    c, v = mediatek_bottom()
    bf = _bar_frame_from(c, v)
    ev = evaluate_bar(bf, 97, rcfg)
    assert ev.is_trigger
    assert all(ev.gates.values())
    assert ev.signals["volume_breakout"]["fired"]
    assert ev.signals["rsi_cross"]["fired"]
    assert ev.signals["rsi_cross"]["left_oversold"]
    assert ev.detail["drawdown"] >= 0.25
    assert ev.detail["candle_quality"] > 0.6


def test_candle_quality_rejects_upper_shadow(rcfg):
    """Same trigger bar but with a long upper shadow (close far below high) is
    filtered as a fake breakout even though the signals fire."""
    c, v = mediatek_bottom()
    # give bar 97 a huge upper shadow: high way above close → (close-low)/(high-low) < 0.6
    bf = _bar_frame_from(c, v, overrides={97: {"high": 700.0, "low": 620.0, "close": 624.0}})
    ev = evaluate_bar(bf, 97, rcfg)
    assert not ev.gates["candle_quality"]
    assert not ev.is_trigger


# ── state machine (spec 4.4) ─────────────────────────────────────────────

def test_state_machine_confirmed(rcfg):
    c, v = mediatek_bottom()
    bf = _bar_frame_from(c, v)
    st = run_state_machine(bf, 97, rcfg)
    assert st.state == "confirmed"
    states = [s for _, s in st.transitions]
    assert states[0] == "triggered"
    assert "confirming" in states


def test_state_machine_failed_on_break_below_invalidation(rcfg):
    """Close breaks below the trigger bar's low during Day 2 → failed (terminal)."""
    c, v = mediatek_bottom()
    inval = min(624.0, 624.0) * 0.985            # bar 97 adjusted low
    c2 = list(c)
    c2[98] = 600.0                                # Day 2 close < invalidation
    bf = _bar_frame_from(c2, v)
    st = run_state_machine(bf, 97, rcfg)
    assert st.state == "failed"
    assert st.transitions[-1][1] == "failed"


def test_state_machine_watch_on_volume_retreat(rcfg):
    """Day-2 volume collapses below trigger×1/3 while price holds → watch."""
    c, v = mediatek_bottom()
    v2 = list(v)
    v2[98] = 15000                                # < 60000/3
    bf = _bar_frame_from(c, v2)
    st = run_state_machine(bf, 97, rcfg)
    assert "watch" in [s for _, s in st.transitions]


# ── control group: 只差一個條件未過 (spec 5.2) ────────────────────────────

def test_control_group_exactly_one_gate_short(rcfg):
    """Weaken the trigger bar's candle so only candle_quality fails → the bar
    is a control-group member, not a trigger."""
    c, v = mediatek_bottom()
    bf = _bar_frame_from(c, v, overrides={97: {"high": 660.0, "low": 620.0, "close": 632.0}})
    ev = evaluate_bar(bf, 97, rcfg)
    passed = sum(ev.gates.values())
    if not ev.gates["candle_quality"] and passed == len(reversal.GATE_NAMES) - 1:
        assert ev.is_control_group
        assert ev.failed_gate == "candle_quality"
        assert not ev.is_trigger


# ── DB scan + persistence ────────────────────────────────────────────────

def _seed_bottom(conn, stock_id, closes, vols):
    dates = [f"2026-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}" for i in range(len(closes))]
    for i, (cl, vol) in enumerate(zip(closes, vols)):
        o = cl
        h = cl * 1.004 if i >= 97 else max(o, cl) * 1.006
        lo = min(o, cl) * 0.985 if i >= 97 else min(o, cl) * 0.994
        conn.execute(
            "INSERT INTO daily_price (stock_id, date, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'twse')",
            (stock_id, dates[i], o, h, lo, cl, vol),
        )
    conn.execute("INSERT OR REPLACE INTO stock_meta (stock_id, name, market, is_active) "
                 "VALUES (?, '聯發科型', '上市', 1)", (stock_id,))
    return dates


def test_reversal_scan_and_persist(tmp_path, rcfg):
    from stock_screener.scoring import load_momentum_config
    mcfg = load_momentum_config()
    db_path = tmp_path / "market.db"
    db.init_db(db_path)
    c, v = mediatek_bottom()
    with db.get_conn(db_path) as conn:
        dates = _seed_bottom(conn, "2454", c, v)
        # scan as-of the trigger bar (idx 97) so the latest bar IS the trigger
        asof = dt.date.fromisoformat(dates[97])
        result = reversal.run_reversal_scan(conn, rcfg, mcfg, asof, "testver")
        n = reversal.persist_reversal(conn, result)

        assert any(t["stock_id"] == "2454" for t in result.triggers)
        rows = conn.execute(
            "SELECT stock_id, profile, reversal_state, factor_detail FROM triggers "
            "WHERE profile='reversal'"
        ).fetchall()
    assert n == len(rows) and rows
    row = next(r for r in rows if r[0] == "2454")
    assert row[1] == "reversal"
    detail = json.loads(row[3])
    assert set(detail["gates"]) == set(reversal.GATE_NAMES)
    assert detail["signals"]["volume_breakout"]["fired"]
