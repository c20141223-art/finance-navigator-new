"""反轉雷達（規格書第 4 節）. Completely separate from the momentum profile.
High-risk list, NOT a buy/sell recommendation.

Time-series structure, evaluated on the ADJUSTED (back-adjusted) OHLC series
so corporate actions can't fake a drawdown or a breakout:

  Stage 0 資格   : passes the momentum 排雷 filters AND close is >= 25% below
                   its 60-trading-day adjusted high.
  Stage 1 打底   : (a) no new wave low in the last `no_new_low_days` bars, and
                   (b) recent volume shrank below the down-leg average × ratio.
  Stage 2 轉強   : >= `require_n_of` of {volume_breakout, macd_converge,
                   rsi_cross} fire on the evaluated bar, AND that bar passes
                   the candle-quality check (filters long-upper-shadow fakes).

A bar triggers only if Stage0 ∧ Stage1 ∧ Stage2. 對照組 (control group,
spec 5.2) = a bar that passes all gates but exactly one — "只差一個條件未過".

State machine (spec 4.4) — ONE machine implements both the positive
(confirm) and negative (fail) paths, replaying the bars after the trigger:

  invalidation price = the trigger bar's adjusted LOW.
  triggered(Day1) → confirming(Day2-3) → confirmed  |  failed  |  watch
    - close < invalidation price (any day)      → failed   (terminal)
    - volume < trigger volume × retreat_ratio   → watch    (量能退回)
      during the confirm window
    - held above 20MA through the confirm window → confirmed
  failed is terminal and permanently recorded (spec 4.4 / 5.1).

RSI uses Wilder smoothing; MACD uses 12-26-9 (stock_screener.indicators),
matching Taiwan charting-software convention.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from stock_screener import momentum
from stock_screener.indicators import macd, wilder_rsi

PROFILE = "reversal"

DEFAULT_REVERSAL_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "reversal.yaml"
)


def load_reversal_config(path: Path | str = DEFAULT_REVERSAL_CONFIG_PATH) -> tuple[dict, str]:
    """Returns (config dict, 12-char version hash of the raw file)."""
    content = Path(path).read_bytes()
    return yaml.safe_load(content), hashlib.sha256(content).hexdigest()[:12]

# The five boolean gates whose conjunction is a trigger. Control-group
# membership = exactly one of these is False (spec 5.2 "只差一個條件未過").
GATE_NAMES = ("drawdown", "no_new_low", "vol_shrink", "n_of_signals", "candle_quality")


@dataclass
class BarFrame:
    """One stock's adjusted OHLC + raw volume, ascending by date."""
    dates: list[str]
    open: pd.Series
    high: pd.Series
    low: pd.Series
    close: pd.Series
    volume: pd.Series


@dataclass
class TriggerEval:
    is_trigger: bool
    is_control_group: bool
    gates: dict                      # gate name -> bool
    signals: dict                    # signal name -> {fired: bool, ...detail}
    detail: dict                     # raw numbers for audit
    failed_gate: str | None = None   # set when is_control_group


@dataclass
class ReversalState:
    stock_id: str
    trigger_date: str
    invalidation_price: float
    trigger_volume: float
    state: str                       # triggered/confirming/confirmed/failed/watch
    transitions: list = field(default_factory=list)   # [(date, state), ...]
    signals: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


# ── adjusted OHLC loading ────────────────────────────────────────────────

def load_bar_frame(conn: sqlite3.Connection, stock_id: str,
                   upto: str, n: int) -> BarFrame | None:
    """Last n trading bars <= upto for one stock, back-adjusted. Volume raw."""
    raw = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM daily_price "
        "WHERE stock_id = ? AND date <= ? ORDER BY date DESC LIMIT ?",
        conn, params=(stock_id, upto, n),
    )
    if raw.empty:
        return None
    raw = raw.sort_values("date").reset_index(drop=True)
    actions = pd.read_sql_query(
        "SELECT ex_date, adj_factor FROM corporate_action WHERE stock_id = ?",
        conn, params=(stock_id,),
    )
    return _to_bar_frame(raw, actions)


def _to_bar_frame(raw: pd.DataFrame, actions: pd.DataFrame) -> BarFrame:
    mult = pd.Series(1.0, index=raw.index)
    for _, a in actions.iterrows():
        mult.loc[raw["date"] < a["ex_date"]] *= a["adj_factor"]
    return BarFrame(
        dates=raw["date"].tolist(),
        open=(raw["open"].astype(float) * mult).reset_index(drop=True),
        high=(raw["high"].astype(float) * mult).reset_index(drop=True),
        low=(raw["low"].astype(float) * mult).reset_index(drop=True),
        close=(raw["close"].astype(float) * mult).reset_index(drop=True),
        volume=raw["volume"].astype(float).reset_index(drop=True),
    )


# ── stage evaluation at a given bar index ────────────────────────────────

def evaluate_bar(bf: BarFrame, i: int, rcfg: dict) -> TriggerEval:
    """Evaluate whether bar `i` (0-based into bf) is a reversal trigger.
    Requires >= 60 bars of history ending at i."""
    q = rcfg["qualify"]
    base = rcfg["base_building"]
    trig = rcfg["trigger"]

    win_high = 60
    if i < win_high - 1:
        return TriggerEval(False, False, {}, {}, {"reason": "歷史不足 60 日"})

    hi_seg = bf.high.iloc[i - win_high + 1:i + 1]
    idx_high = hi_seg.idxmax()                      # index of 60-day high
    high_60 = bf.high.iloc[idx_high]
    close_i = bf.close.iloc[i]
    drawdown = (high_60 - close_i) / high_60 if high_60 else 0.0

    # down-leg = 60d-high day .. wave-low day
    low_seg = bf.low.iloc[idx_high:i + 1]
    idx_low = low_seg.idxmin()
    wave_low = bf.low.iloc[idx_low]

    # Stage 0
    g_drawdown = drawdown >= q["drawdown_from_60d_high"]

    # Stage 1a: last N bars made no new low vs the low set before that window
    n_nl = base["no_new_low_days"]
    recent_low = bf.low.iloc[i - n_nl + 1:i + 1].min()
    prior_low = bf.low.iloc[idx_high:i - n_nl + 1].min() if i - n_nl + 1 > idx_high else wave_low
    g_no_new_low = recent_low >= prior_low

    # Stage 1b: recent avg volume < down-leg avg volume × ratio
    downleg_vol = bf.volume.iloc[idx_high:idx_low + 1].mean()
    recent_vol = bf.volume.iloc[i - n_nl + 1:i + 1].mean()
    g_vol_shrink = bool(downleg_vol) and recent_vol < downleg_vol * base["volume_shrink_ratio"]

    # Stage 2 signals
    sig = trig["signals"]
    s1 = _sig_volume_breakout(bf, i, sig["volume_breakout"])
    s2 = _sig_macd_converge(bf, i, sig["macd_converge"])
    s3 = _sig_rsi_cross(bf, i, sig["rsi_cross"])
    fired = sum(s["fired"] for s in (s1, s2, s3))
    g_n_signals = fired >= trig["require_n_of"]

    # candle quality on trigger bar
    hi, lo, cl = bf.high.iloc[i], bf.low.iloc[i], bf.close.iloc[i]
    candle = (cl - lo) / (hi - lo) if hi > lo else 0.0
    g_candle = candle > trig["candle_quality_min"]

    gates = {
        "drawdown": bool(g_drawdown),
        "no_new_low": bool(g_no_new_low),
        "vol_shrink": bool(g_vol_shrink),
        "n_of_signals": bool(g_n_signals),
        "candle_quality": bool(g_candle),
    }
    passed = sum(gates.values())
    is_trigger = passed == len(GATE_NAMES)
    is_control = passed == len(GATE_NAMES) - 1
    failed_gate = next((n for n in GATE_NAMES if not gates[n]), None) if is_control else None

    detail = {
        "drawdown": round(float(drawdown), 4),
        "high_60": round(float(high_60), 4),
        "wave_low": round(float(wave_low), 4),
        "recent_low": round(float(recent_low), 4),
        "prior_low": round(float(prior_low), 4),
        "downleg_avg_vol": round(float(downleg_vol), 1) if downleg_vol else None,
        "recent_avg_vol": round(float(recent_vol), 1),
        "signals_fired": int(fired),
        "candle_quality": round(float(candle), 4),
    }
    return TriggerEval(is_trigger, is_control, gates,
                       {"volume_breakout": s1, "macd_converge": s2, "rsi_cross": s3},
                       detail, failed_gate)


def _sig_volume_breakout(bf: BarFrame, i: int, cfg: dict) -> dict:
    ma_n = cfg["reclaim_ma"]
    vol_ma20 = bf.volume.iloc[i - 20 + 1:i + 1].mean() if i >= 19 else None
    ma20 = bf.close.iloc[i - ma_n + 1:i + 1].mean() if i >= ma_n - 1 else None
    vol = bf.volume.iloc[i]
    close = bf.close.iloc[i]
    fired = (
        vol_ma20 is not None and ma20 is not None
        and vol > vol_ma20 * cfg["vol_multiple"] and close >= ma20
    )
    return {"fired": bool(fired),
            "vol": float(vol), "vol_ma20": round(float(vol_ma20), 1) if vol_ma20 else None,
            "close": round(float(close), 4), "ma20": round(float(ma20), 4) if ma20 else None}


def _sig_macd_converge(bf: BarFrame, i: int, cfg: dict) -> dict:
    m = macd(bf.close, cfg["fast"], cfg["slow"], cfg["signal"])
    osc = m["osc"]
    if pd.isna(osc.iloc[i]) or i < 1:
        return {"fired": False, "osc": None}
    cd = cfg["converge_days"]
    # negative→positive turn: OSC crossed up through 0 on this bar
    turned = osc.iloc[i] > 0 >= osc.iloc[i - 1]
    # OSC magnitude shrinking for `converge_days` consecutive bars while negative
    converging = False
    if i >= cd and not osc.iloc[i - cd:i + 1].isna().any():
        seg = osc.iloc[i - cd:i + 1].tolist()   # cd+1 values
        # while below zero, |osc| strictly decreasing each of the last cd steps
        converging = all(seg[k] < 0 for k in range(len(seg) - 1)) and \
            all(abs(seg[k + 1]) < abs(seg[k]) for k in range(len(seg) - 1))
    fired = bool(turned or converging)
    return {"fired": fired, "osc": round(float(osc.iloc[i]), 4),
            "osc_prev": round(float(osc.iloc[i - 1]), 4),
            "turned_positive": bool(turned), "converging": bool(converging)}


def _sig_rsi_cross(bf: BarFrame, i: int, cfg: dict) -> dict:
    rf = wilder_rsi(bf.close, cfg["fast"])
    rs = wilder_rsi(bf.close, cfg["slow"])
    if i < 1 or pd.isna(rf.iloc[i]) or pd.isna(rs.iloc[i]) or pd.isna(rf.iloc[i - 1]):
        return {"fired": False, "rsi_fast": None, "rsi_slow": None}
    crossed = rf.iloc[i] > rs.iloc[i] and rf.iloc[i - 1] <= rs.iloc[i - 1]
    # emerged from oversold: fast RSI was below exit_zone within the last few
    # bars and is now above it
    zone = cfg["exit_zone"]
    look = rf.iloc[max(0, i - 3):i + 1]
    left_oversold = (look.min() < zone) and (rf.iloc[i] >= zone)
    fired = bool(crossed and left_oversold)
    return {"fired": fired,
            "rsi_fast": round(float(rf.iloc[i]), 2), "rsi_slow": round(float(rs.iloc[i]), 2),
            "rsi_fast_prev": round(float(rf.iloc[i - 1]), 2),
            "crossed": bool(crossed), "left_oversold": bool(left_oversold)}


# ── state machine (spec 4.4) ─────────────────────────────────────────────

def run_state_machine(bf: BarFrame, trigger_idx: int, rcfg: dict) -> ReversalState:
    """Replay bars after the trigger and return the current state. One machine,
    both paths: breaking the invalidation price → failed (terminal); holding
    above the 20MA through the confirm window → confirmed; a volume collapse
    during confirmation → watch."""
    conf = rcfg["confirm"]
    inval = float(bf.low.iloc[trigger_idx])
    trig_vol = float(bf.volume.iloc[trigger_idx])
    ma_n = conf["hold_above_ma"]

    state = "triggered"
    transitions = [(bf.dates[trigger_idx], state)]
    for j in range(trigger_idx + 1, len(bf.dates)):
        close = float(bf.close.iloc[j])
        vol = float(bf.volume.iloc[j])
        day_no = j - trigger_idx + 1     # trigger day = Day 1
        ma = bf.close.iloc[j - ma_n + 1:j + 1].mean() if j >= ma_n - 1 else None

        if close < inval:
            state = "failed"
            transitions.append((bf.dates[j], state))
            break   # terminal

        in_confirm = day_no <= conf["days"] + 1   # Day 2..(days+1)
        if in_confirm:
            if vol < trig_vol * conf["volume_retreat_ratio"]:
                state = "watch"
                transitions.append((bf.dates[j], state))
                continue
            if ma is not None and close >= ma:
                if state != "confirming":
                    state = "confirming"
                    transitions.append((bf.dates[j], state))
        else:
            # past the confirm window and still alive → confirmed once
            if state in ("confirming", "triggered", "watch"):
                if ma is not None and close >= ma:
                    state = "confirmed"
                    transitions.append((bf.dates[j], state))
    return ReversalState(
        stock_id="", trigger_date=bf.dates[trigger_idx],
        invalidation_price=round(inval, 4), trigger_volume=trig_vol,
        state=state, transitions=transitions,
    )


# ── history scan (test helper + conceptual daily path) ───────────────────

def scan_first_trigger(bf: BarFrame, rcfg: dict, start: int = 59) -> int | None:
    """First bar index (>= start) that is a trigger. None if none."""
    for i in range(start, len(bf.dates)):
        if evaluate_bar(bf, i, rcfg).is_trigger:
            return i
    return None


# ── daily radar scan over the DB ─────────────────────────────────────────

@dataclass
class RadarResult:
    date: str
    qualified: int                   # passed momentum 排雷 filters
    triggers: list = field(default_factory=list)      # list[dict]
    control_group: list = field(default_factory=list)
    config_version: str = ""


def run_reversal_scan(conn: sqlite3.Connection, rcfg: dict, mcfg,
                      date: dt.date, config_version: str) -> RadarResult:
    """Scan the whole market for bars triggering ON `date`. Reuses the momentum
    排雷 filters for the qualify precondition (spec 4.1)."""
    window = int(mcfg.filters["min_history_days"])
    dates = momentum._market_trading_dates(conn, date.isoformat(), 60)
    if not dates:
        raise RuntimeError("資料庫無資料")
    asof = dates[0]

    # candidate universe: stocks with a row on asof
    candidates = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_id FROM daily_price WHERE date = ?", (asof,)
    )]
    risk = {r[0] for r in conn.execute(
        "SELECT DISTINCT stock_id FROM risk_list WHERE date = ?", (asof,)
    )}
    names = dict(conn.execute("SELECT stock_id, name FROM stock_meta"))

    triggers, control = [], []
    qualified = 0
    for stock_id in candidates:
        bf = load_bar_frame(conn, stock_id, asof, 60)
        if bf is None or len(bf.dates) < window:
            continue
        # reuse momentum 排雷 filter (price/vol/risk/history/common-stock)
        price_rows = pd.DataFrame({"close": bf.close, "volume": bf.volume,
                                   "date": bf.dates})
        ok, _ = momentum.apply_filters(stock_id, price_rows, bf.dates, risk, mcfg.filters)
        if not ok:
            continue
        qualified += 1
        ev = evaluate_bar(bf, len(bf.dates) - 1, rcfg)
        if ev.is_trigger:
            st = run_state_machine(bf, len(bf.dates) - 1, rcfg)
            triggers.append(_event_row(stock_id, names.get(stock_id), asof, ev, st))
        elif ev.is_control_group:
            control.append(_event_row(stock_id, names.get(stock_id), asof, ev, None))

    return RadarResult(date=asof, qualified=qualified, triggers=triggers,
                       control_group=control, config_version=config_version)


def _event_row(stock_id, name, date, ev: TriggerEval, st: ReversalState | None) -> dict:
    return {
        "stock_id": stock_id, "name": name, "date": date,
        "gates": ev.gates, "signals": ev.signals, "detail": ev.detail,
        "failed_gate": ev.failed_gate,
        "state": st.state if st else None,
        "invalidation_price": st.invalidation_price if st else None,
    }


# ── persistence (spec 5.1) ───────────────────────────────────────────────

def persist_reversal(conn: sqlite3.Connection, result: RadarResult) -> int:
    """Idempotent per (date, profile). factor_detail carries gates + signals +
    raw numbers so a trigger can be re-derived by hand; reversal_state holds
    the state-machine outcome."""
    conn.execute("DELETE FROM triggers WHERE date = ? AND profile = ?",
                 (result.date, PROFILE))
    rows = []
    for is_control, items in ((0, result.triggers), (1, result.control_group)):
        for it in items:
            rows.append((
                result.date, PROFILE, is_control, None, None,
                json.dumps({"gates": it["gates"], "signals": it["signals"],
                            "detail": it["detail"], "failed_gate": it["failed_gate"]},
                           ensure_ascii=False),
                None, result.config_version, it["stock_id"],
                it.get("state"),
            ))
    conn.executemany(
        """
        INSERT INTO triggers
            (date, profile, is_control_group, rank, total_score,
             factor_detail, market_regime, config_version, stock_id, reversal_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)
