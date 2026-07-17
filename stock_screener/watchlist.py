"""觀察池雷達（規格書 6.4）. Reads config/watchlist.json and reports each
tracked stock's OBJECTIVE momentum score plus its rank change — nothing is
added to the score, the watchlist never enters the selection universe, and
this is not a buy/sell recommendation.

Rank is intra-watchlist only (a stock's standing among the user's own list),
ranked by total score desc with stock_id asc as the deterministic tie-break —
the same convention as the momentum screen. Day-over-day rank change and the
"連續墊底" (consecutive last-place) alert are derived from prior watchlist
snapshots persisted to the `triggers` table under profile='watchlist', so the
history lives in the same idempotent (date, profile) store as everything else.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from stock_screener import momentum
from stock_screener.scoring import MomentumConfig

PROFILE = "watchlist"

DEFAULT_WATCHLIST_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "watchlist.json"
)


def load_watchlist(path: Path | str = DEFAULT_WATCHLIST_PATH) -> dict:
    """Parse watchlist.json, ignoring underscore-prefixed pseudo-comment keys.
    Returns {stocks: [{stock_id, note}], consecutive_bottom_alert_days, ...}."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cfg = {k: v for k, v in raw.items() if not k.startswith("_")}
    stocks = []
    for item in cfg.get("stocks", []):
        if isinstance(item, str):
            stocks.append({"stock_id": item, "note": None})
        elif isinstance(item, dict) and item.get("stock_id"):
            stocks.append({"stock_id": str(item["stock_id"]), "note": item.get("note")})
    cfg["stocks"] = stocks
    cfg.setdefault("consecutive_bottom_alert_days", 3)
    cfg.setdefault("score_profile", "momentum")
    return cfg


@dataclass
class WatchlistEntry:
    stock_id: str
    name: str | None
    note: str | None
    total: float | None
    dimension_scores: dict
    rank: int | None            # intra-watchlist rank on this date
    prev_rank: int | None
    rank_change: int | None     # prev_rank - rank (positive = moved up)
    filter_ok: bool
    reason: str | None
    insufficient: bool
    bottom_alert: bool = False


@dataclass
class WatchlistResult:
    date: str
    entries: list = field(default_factory=list)   # list[WatchlistEntry], ranked
    n_stocks: int = 0


def _prior_dates(conn: sqlite3.Connection, before: str, limit: int) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM triggers WHERE profile = ? AND date < ? "
        "ORDER BY date DESC LIMIT ?",
        (PROFILE, before, limit),
    )]


def _ranks_on(conn: sqlite3.Connection, date: str) -> dict[str, int]:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT stock_id, rank FROM triggers WHERE profile = ? AND date = ?",
        (PROFILE, date),
    ) if r[1] is not None}


def build_watchlist_radar(conn: sqlite3.Connection, mcfg: MomentumConfig,
                          date: dt.date, wl_cfg: dict) -> WatchlistResult:
    stocks = wl_cfg["stocks"]
    ids = [s["stock_id"] for s in stocks]
    notes = {s["stock_id"]: s["note"] for s in stocks}
    if not ids:
        # asof still resolved so the report can show the right date
        dates = momentum._market_trading_dates(conn, date.isoformat(), 1)
        return WatchlistResult(date=dates[0] if dates else date.isoformat(),
                               entries=[], n_stocks=0)

    dates = momentum._market_trading_dates(conn, date.isoformat(), 1)
    asof = dates[0] if dates else date.isoformat()

    scored = momentum.score_selected(conn, mcfg, date, ids)

    # rank the ones that have a score; unscored (insufficient) go last
    scorable = [(sid, d) for sid, d in scored.items() if d["score"] is not None]
    scorable.sort(key=lambda kv: (-kv[1]["score"].total, kv[0]))
    rank_map = {sid: i for i, (sid, _) in enumerate(scorable, start=1)}

    # previous snapshot for rank change
    prior = _prior_dates(conn, asof, 1)
    prev_ranks = _ranks_on(conn, prior[0]) if prior else {}

    # consecutive-last-place alert
    alert_days = int(wl_cfg.get("consecutive_bottom_alert_days", 3))
    last_place_this = max(rank_map.values()) if rank_map else None
    recent_dates = _prior_dates(conn, asof, alert_days - 1) if alert_days > 1 else []

    entries: list[WatchlistEntry] = []
    for sid, d in scored.items():
        sc = d["score"]
        rank = rank_map.get(sid)
        prev_rank = prev_ranks.get(sid)
        change = (prev_rank - rank) if (prev_rank is not None and rank is not None) else None

        bottom_alert = False
        if rank is not None and last_place_this is not None and rank == last_place_this \
                and len(recent_dates) >= alert_days - 1:
            was_bottom_each = True
            for pd_date in recent_dates:
                pr = _ranks_on(conn, pd_date)
                if not pr or pr.get(sid) != max(pr.values()):
                    was_bottom_each = False
                    break
            bottom_alert = was_bottom_each

        entries.append(WatchlistEntry(
            stock_id=sid, name=sc.name if sc else None, note=notes.get(sid),
            total=sc.total if sc else None,
            dimension_scores=sc.dimension_scores if sc else {},
            rank=rank, prev_rank=prev_rank, rank_change=change,
            filter_ok=d["filter_ok"], reason=d["reason"],
            insufficient=d["insufficient"], bottom_alert=bottom_alert,
        ))

    # ranked first (by rank), then insufficient ones by stock_id
    entries.sort(key=lambda e: (e.rank is None, e.rank if e.rank is not None else 0, e.stock_id))
    return WatchlistResult(date=asof, entries=entries, n_stocks=len(scorable))


def persist_watchlist(conn: sqlite3.Connection, result: WatchlistResult,
                      config_version: str) -> int:
    """Idempotent per (date, profile='watchlist'). Stores intra-watchlist rank
    and score so tomorrow's run can compute rank change; factor_detail keeps
    the dimension scores and status for the report."""
    conn.execute("DELETE FROM triggers WHERE date = ? AND profile = ?",
                 (result.date, PROFILE))
    rows = []
    for e in result.entries:
        rows.append((
            result.date, PROFILE, 0, e.rank, e.total,
            json.dumps({"dimensions": e.dimension_scores, "note": e.note,
                        "filter_ok": e.filter_ok, "reason": e.reason,
                        "insufficient": e.insufficient,
                        "bottom_alert": e.bottom_alert}, ensure_ascii=False),
            None, config_version, e.stock_id,
        ))
    conn.executemany(
        """
        INSERT INTO triggers
            (date, profile, is_control_group, rank, total_score,
             factor_detail, market_regime, config_version, stock_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)
