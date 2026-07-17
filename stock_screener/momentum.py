"""排雷濾網 + 順勢評分引擎（規格書第 2、3 節）.

Design decisions that matter for manual re-verification (驗收方式是人工
覆算分項得分, so every window definition must be unambiguous):

- "交易日" = the market-wide distinct dates present in daily_price, newest
  first. All lookback windows (5日均量, 近5日外資佔比, 連買天數, 60MA)
  count these market trading dates, not calendar days.
- 資料完整性 filter: a stock must have a daily_price row on EVERY one of
  the last `min_history_days` market trading dates. This simultaneously
  implements spec 1.4's new-listing exclusion, and deliberately also
  excludes stocks suspended within the window (conservative, documented).
- Technical factors (bias_60ma) use back-adjusted closes via the same
  corporate-action math as stock_screener.adjust (cross-checked by test);
  volume factors use raw volume in 張; the min_price filter uses the raw
  (as-traded) close.
- trust_buy_streak: walk market trading dates backward from the screen
  date; a day with trust_net > 0 extends the streak, anything else
  (<=0 or no row — the institutional report omits inactive stocks) breaks
  it.
- foreign_net_ratio_5d: sum of foreign_net over the last 5 market trading
  dates (missing rows count 0) ÷ the stock's total volume over its rows in
  those dates. Both sides are 張, so the ratio is unit-consistent.
- revenue factors use the latest available months per stock (最新可得,
  spec 1.4); revenue_trend_3m requires the 3 newest months to be
  consecutive calendar months AND strictly increasing YoY.

- Ranking tie-break: rows sort by total score descending, then stock_id
  ascending (lexicographic). The secondary key exists ONLY to make ranks
  deterministic and reproducible across runs — it carries no informational
  meaning, so among equal totals the rank ordering has no discriminative
  value. This matters in practice: coarse bins + two-factor dimension caps
  make full marks reachable, so clusters of tied totals near the top are
  an expected property of the current parameters (spec discipline: no
  parameter tweaks without >= 30 trigger cases of evidence).

Persistence is idempotent per (date, profile): re-running a screen for the
same date replaces its trigger rows.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field

import pandas as pd

from stock_screener.scoring import MomentumConfig, score_dimension

PROFILE = "momentum"


@dataclass
class StockScore:
    stock_id: str
    name: str | None
    total: float
    dimension_scores: dict          # dim -> capped score
    factor_detail: dict             # dim -> factor -> {raw, score}
    rank: int | None = None
    is_control_group: bool = False


@dataclass
class ScreenResult:
    date: str                       # effective trading date screened
    universe_size: int              # stocks examined before filters
    passed_filters: int
    scores: list[StockScore] = field(default_factory=list)   # ranked, top_n + control only
    all_scored: int = 0
    market_regime: dict = field(default_factory=dict)
    config_version: str = ""


# ── data loading ────────────────────────────────────────────────────────

def _market_trading_dates(conn: sqlite3.Connection, upto: str, n: int) -> list[str]:
    """Last n market-wide trading dates <= upto, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT date FROM daily_price WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (upto, n),
    ).fetchall()
    return [r[0] for r in rows]


def _load_frames(conn: sqlite3.Connection, dates: list[str]) -> dict:
    placeholders = ",".join("?" * len(dates))
    prices = pd.read_sql_query(
        f"SELECT stock_id, date, close, volume FROM daily_price WHERE date IN ({placeholders})",
        conn, params=dates,
    )
    institutional = pd.read_sql_query(
        f"SELECT stock_id, date, foreign_net, trust_net FROM institutional WHERE date IN ({placeholders})",
        conn, params=dates,
    )
    actions = pd.read_sql_query(
        "SELECT stock_id, ex_date, adj_factor FROM corporate_action", conn,
    )
    revenue = pd.read_sql_query(
        "SELECT stock_id, year_month, yoy FROM monthly_revenue", conn,
    )
    meta = pd.read_sql_query("SELECT stock_id, name FROM stock_meta", conn)
    risk = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT stock_id FROM risk_list WHERE date = ?", (dates[0],)
        )
    }
    return {
        "prices": prices, "institutional": institutional, "actions": actions,
        "revenue": revenue, "meta": meta, "risk": risk,
    }


def _adjusted_close(price_rows: pd.DataFrame, actions: pd.DataFrame) -> pd.Series:
    """Back-adjusted close for one stock's rows (ascending by date), same
    semantics as stock_screener.adjust.get_adjusted_prices: every action
    whose ex_date is after a row's date multiplies that row's close."""
    adjusted = price_rows["close"].astype(float).copy()
    for _, action in actions.iterrows():
        mask = price_rows["date"] < action["ex_date"]
        adjusted.loc[mask] *= action["adj_factor"]
    return adjusted


# ── filters (spec section 2) ────────────────────────────────────────────

def _is_common_stock(stock_id: str) -> bool:
    return len(stock_id) == 4 and stock_id.isdigit() and not stock_id.startswith("0")


def apply_filters(stock_id: str, rows: pd.DataFrame, dates: list[str],
                  risk: set, filters: dict) -> tuple[bool, str | None]:
    """Returns (passed, reason-if-failed). `rows` = the stock's daily_price
    rows within the loaded window, ascending by date."""
    if filters.get("common_stock_only", True) and not _is_common_stock(stock_id):
        return False, "非普通股"
    if len(rows) < int(filters["min_history_days"]):
        return False, f"歷史不足（{len(rows)}/{filters['min_history_days']} 交易日）"
    if stock_id in risk:
        return False, "當日在排雷名單"
    last_close = rows["close"].iloc[-1]
    if not last_close > float(filters["min_price"]):
        return False, f"收盤價 {last_close} 未達下限"
    avg5 = rows["volume"].iloc[-5:].mean()
    if not avg5 > float(filters["min_avg_volume_5d"]):
        return False, f"5日均量 {avg5:.0f} 張未達下限"
    return True, None


# ── factors (spec section 3.1) ──────────────────────────────────────────

def compute_factors(stock_id: str, rows: pd.DataFrame, adjusted: pd.Series,
                    inst_by_date: dict, revenue_rows: pd.DataFrame,
                    dates: list[str]) -> dict:
    """Raw factor values for one stock. rows ascending by date; adjusted is
    the back-adjusted close aligned to rows; inst_by_date maps date ->
    (foreign_net, trust_net); dates = market trading dates newest first."""
    factors: dict = {}

    # F1 revenue_yoy: latest available month's YoY
    if len(revenue_rows):
        factors["revenue_yoy"] = revenue_rows["yoy"].iloc[0]
    else:
        factors["revenue_yoy"] = None

    # F2 revenue_trend_3m: 3 newest months consecutive AND strictly rising YoY
    trend = False
    if len(revenue_rows) >= 3:
        months = revenue_rows["year_month"].iloc[:3].tolist()   # newest first
        yoys = revenue_rows["yoy"].iloc[:3].tolist()
        if all(y is not None for y in yoys) and _consecutive_months(months):
            trend = yoys[0] > yoys[1] > yoys[2]
    factors["revenue_trend_3m"] = trend

    # C1 trust_buy_streak
    streak = 0
    for d in dates:  # newest first
        rec = inst_by_date.get(d)
        if rec is not None and rec[1] is not None and rec[1] > 0:
            streak += 1
        else:
            break
    factors["trust_buy_streak"] = streak

    # C2 foreign_net_ratio_5d
    last5 = dates[:5]
    foreign_sum = sum(
        (inst_by_date.get(d) or (0, 0))[0] or 0 for d in last5
    )
    vol_sum = rows[rows["date"].isin(last5)]["volume"].sum()
    factors["foreign_net_ratio_5d"] = (foreign_sum / vol_sum) if vol_sum else None

    # T1 bias_60ma (%) on adjusted closes
    ma60 = adjusted.iloc[-60:].mean()
    factors["bias_60ma"] = (adjusted.iloc[-1] / ma60 - 1.0) * 100.0 if ma60 else None

    # T2 volume_expansion: 5日均量 / 20日均量 (raw volume, 張)
    ma5 = rows["volume"].iloc[-5:].mean()
    ma20 = rows["volume"].iloc[-20:].mean()
    factors["volume_expansion"] = (ma5 / ma20) if ma20 else None

    return factors


def _consecutive_months(months_newest_first: list[str]) -> bool:
    """['2026-06', '2026-05', '2026-04'] -> True."""
    def prev(ym: str) -> str:
        y, m = (int(x) for x in ym.split("-"))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        return f"{y:04d}-{m:02d}"
    a, b, c = months_newest_first
    return prev(a) == b and prev(b) == c


# ── market regime marker (stored on triggers; display is Phase 4) ───────

def compute_market_regime(per_stock: dict) -> dict:
    """全市場多頭排列（還原收盤 > 20MA > 60MA）股票佔比，以通過資料完整性
    的普通股為母體。加權指數 vs 60MA 需要指數歷史，屬 Phase 4 資料補充，
    此處明確標注缺項而非省略。"""
    total = 0
    aligned = 0
    for stock_id, (rows, adjusted) in per_stock.items():
        total += 1
        ma20 = adjusted.iloc[-20:].mean()
        ma60 = adjusted.iloc[-60:].mean()
        if adjusted.iloc[-1] > ma20 > ma60:
            aligned += 1
    return {
        "bullish_alignment_pct": round(100.0 * aligned / total, 2) if total else None,
        "universe": total,
        "index_vs_60ma": None,  # Phase 4: 需另抓加權指數歷史
    }


# ── engine ──────────────────────────────────────────────────────────────

def run_screen(conn: sqlite3.Connection, mcfg: MomentumConfig,
               date: dt.date) -> ScreenResult:
    window = int(mcfg.filters["min_history_days"])
    dates = _market_trading_dates(conn, date.isoformat(), window)
    if len(dates) < window:
        raise RuntimeError(
            f"資料庫僅有 {len(dates)} 個交易日資料，不足 min_history_days={window}，"
            "請先執行回補（scripts/run_backfill.py）"
        )
    asof = dates[0]

    frames = _load_frames(conn, dates)
    names = dict(zip(frames["meta"]["stock_id"], frames["meta"]["name"]))

    inst_grouped: dict[str, dict] = {}
    for stock_id, g in frames["institutional"].groupby("stock_id"):
        inst_grouped[stock_id] = {
            r.date: (r.foreign_net, r.trust_net) for r in g.itertuples()
        }
    revenue_grouped = {
        stock_id: g.sort_values("year_month", ascending=False).reset_index(drop=True)
        for stock_id, g in frames["revenue"].groupby("stock_id")
    }
    actions_grouped = {
        stock_id: g for stock_id, g in frames["actions"].groupby("stock_id")
    }
    empty_actions = frames["actions"].iloc[0:0]

    universe_size = 0
    passed: dict[str, tuple[pd.DataFrame, pd.Series]] = {}
    for stock_id, rows in frames["prices"].groupby("stock_id"):
        universe_size += 1
        rows = rows.sort_values("date").reset_index(drop=True)
        ok, _reason = apply_filters(stock_id, rows, dates, frames["risk"], mcfg.filters)
        if not ok:
            continue
        adjusted = _adjusted_close(rows, actions_grouped.get(stock_id, empty_actions))
        passed[stock_id] = (rows, adjusted)

    market_regime = compute_market_regime(passed)

    scores: list[StockScore] = []
    for stock_id, (rows, adjusted) in passed.items():
        raw_factors = compute_factors(
            stock_id, rows, adjusted,
            inst_grouped.get(stock_id, {}),
            revenue_grouped.get(stock_id, frames["revenue"].iloc[0:0]),
            dates,
        )
        dim_scores: dict = {}
        detail: dict = {}
        total = 0.0
        for dim in mcfg.dimensions:
            dim_score, dim_detail = score_dimension(dim, raw_factors)
            dim_scores[dim.name] = dim_score
            detail[dim.name] = dim_detail
            total += dim.weight * dim_score
        scores.append(StockScore(
            stock_id=stock_id, name=names.get(stock_id),
            total=round(total, 4), dimension_scores=dim_scores,
            factor_detail=detail,
        ))

    # deterministic ranking: total desc, stock_id asc on ties
    scores.sort(key=lambda s: (-s.total, s.stock_id))
    control_lo, control_hi = mcfg.control_group_rank
    kept: list[StockScore] = []
    for i, s in enumerate(scores, start=1):
        s.rank = i
        if i <= mcfg.top_n:
            kept.append(s)
        elif control_lo <= i <= control_hi:
            s.is_control_group = True
            kept.append(s)
        elif i > control_hi:
            break

    return ScreenResult(
        date=asof, universe_size=universe_size, passed_filters=len(passed),
        scores=kept, all_scored=len(scores), market_regime=market_regime,
        config_version=mcfg.version_hash,
    )


# ── scoring for an explicit stock set (watchlist radar, spec 6.4) ────────

def score_selected(conn: sqlite3.Connection, mcfg: MomentumConfig,
                   date: dt.date, stock_ids: list[str]) -> dict[str, dict]:
    """Compute the objective momentum score for a specific set of stocks,
    regardless of whether they pass the 排雷 filters — the watchlist shows
    the user's own tracked names, not a filtered universe. Returns
    stock_id -> {score: StockScore | None, filter_ok: bool, reason: str|None,
    insufficient: bool}. `insufficient` means < min_history_days of data, so
    factors like 60MA can't be computed and no score is produced."""
    window = int(mcfg.filters["min_history_days"])
    dates = _market_trading_dates(conn, date.isoformat(), window)
    if not dates:
        return {sid: {"score": None, "filter_ok": False, "reason": "資料庫無資料",
                      "insufficient": True} for sid in stock_ids}

    frames = _load_frames(conn, dates)
    names = dict(zip(frames["meta"]["stock_id"], frames["meta"]["name"]))
    inst_grouped: dict[str, dict] = {}
    for stock_id, g in frames["institutional"].groupby("stock_id"):
        inst_grouped[stock_id] = {r.date: (r.foreign_net, r.trust_net) for r in g.itertuples()}
    revenue_grouped = {
        stock_id: g.sort_values("year_month", ascending=False).reset_index(drop=True)
        for stock_id, g in frames["revenue"].groupby("stock_id")
    }
    actions_grouped = {sid: g for sid, g in frames["actions"].groupby("stock_id")}
    empty_actions = frames["actions"].iloc[0:0]
    prices_grouped = {sid: g for sid, g in frames["prices"].groupby("stock_id")}

    out: dict[str, dict] = {}
    for sid in stock_ids:
        rows = prices_grouped.get(sid)
        if rows is None or len(rows) < window:
            out[sid] = {"score": None, "filter_ok": False,
                        "reason": f"歷史不足（{0 if rows is None else len(rows)}/{window} 交易日）",
                        "insufficient": True}
            continue
        rows = rows.sort_values("date").reset_index(drop=True)
        filter_ok, reason = apply_filters(sid, rows, dates, frames["risk"], mcfg.filters)
        adjusted = _adjusted_close(rows, actions_grouped.get(sid, empty_actions))
        raw_factors = compute_factors(
            sid, rows, adjusted, inst_grouped.get(sid, {}),
            revenue_grouped.get(sid, frames["revenue"].iloc[0:0]), dates,
        )
        dim_scores, detail, total = {}, {}, 0.0
        for dim in mcfg.dimensions:
            dim_score, dim_detail = score_dimension(dim, raw_factors)
            dim_scores[dim.name] = dim_score
            detail[dim.name] = dim_detail
            total += dim.weight * dim_score
        out[sid] = {
            "score": StockScore(
                stock_id=sid, name=names.get(sid), total=round(total, 4),
                dimension_scores=dim_scores, factor_detail=detail),
            "filter_ok": filter_ok, "reason": reason, "insufficient": False,
        }
    return out


# ── persistence (spec section 5) ────────────────────────────────────────

def topn_variance(conn: sqlite3.Connection, result: ScreenResult,
                  top_n: int) -> tuple[list, list]:
    """Compare today's Top-N against the most recent PRIOR momentum snapshot
    stored in `triggers`. Returns (new_entrants, dropped) where new_entrants
    is the list[StockScore] of today's Top-N stocks absent from the prior
    Top-N, and dropped is [{stock_id, name, prev_rank}] for prior Top-N
    stocks not in today's Top-N. Call BEFORE persisting today's rows so the
    prior date is genuinely the previous run."""
    prior = conn.execute(
        "SELECT DISTINCT date FROM triggers WHERE profile = ? AND date < ? "
        "ORDER BY date DESC LIMIT 1",
        (PROFILE, result.date),
    ).fetchone()
    if not prior:
        return [], []
    prev_rows = conn.execute(
        "SELECT stock_id, rank FROM triggers "
        "WHERE profile = ? AND date = ? AND is_control_group = 0 AND rank <= ?",
        (PROFILE, prior[0], top_n),
    ).fetchall()
    prev_ranks = {r[0]: r[1] for r in prev_rows}
    prev_names = dict(conn.execute("SELECT stock_id, name FROM stock_meta"))

    today_top = [s for s in result.scores if not s.is_control_group and s.rank <= top_n]
    today_ids = {s.stock_id for s in today_top}

    new_entrants = [s for s in today_top if s.stock_id not in prev_ranks]
    dropped = [
        {"stock_id": sid, "name": prev_names.get(sid), "prev_rank": rk}
        for sid, rk in sorted(prev_ranks.items(), key=lambda kv: kv[1])
        if sid not in today_ids
    ]
    return new_entrants, dropped


def record_config_version(conn: sqlite3.Connection, mcfg: MomentumConfig,
                          date: str, note: str = "") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO config_versions (version_hash, date, note) VALUES (?, ?, ?)",
        (mcfg.version_hash, date, note or "momentum.yaml"),
    )


def persist_triggers(conn: sqlite3.Connection, result: ScreenResult) -> int:
    """Idempotent per (date, profile): replaces any previous rows for the
    same screen date so a re-run can't duplicate. Follow-up return columns
    (return_t5/t20/t60, mfe, mae) stay NULL here — backfilled later per
    spec 5.1."""
    conn.execute(
        "DELETE FROM triggers WHERE date = ? AND profile = ?",
        (result.date, PROFILE),
    )
    payload = [
        (
            result.date, PROFILE, int(s.is_control_group), s.rank, s.total,
            json.dumps(
                {"dimensions": s.dimension_scores, "factors": s.factor_detail},
                ensure_ascii=False,
            ),
            json.dumps(result.market_regime, ensure_ascii=False),
            result.config_version, s.stock_id,
        )
        for s in result.scores
    ]
    conn.executemany(
        """
        INSERT INTO triggers
            (date, profile, is_control_group, rank, total_score,
             factor_detail, market_regime, config_version, stock_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)
