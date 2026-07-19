"""Static dashboard data export (GitHub Pages). Serializes the SAME four
blocks the email carries into a small JSON file the front-end dashboard reads
— no backend, no external service. The email remains the primary channel;
this is purely additive.

`build_dashboard_data` takes the already-assembled report context so the web
view and the email can never drift, and `write_web_output` writes:

    <web_dir>/data/latest.json        always the newest run
    <web_dir>/data/<YYYY-MM-DD>.json  dated archive (idempotent overwrite)
    <web_dir>/data/index.json         {"dates": [...newest first...], "latest": ...}

The front-end fetches these with RELATIVE paths, so the same files work
whatever subfolder Pages serves them from.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from stock_screener.report_email import ReportContext

SCHEMA_VERSION = 1
_SIG_NAME = {"volume_breakout": "帶量突破", "macd_converge": "MACD", "rsi_cross": "RSI"}


def build_dashboard_data(ctx: ReportContext) -> dict:
    new_ids = {s.stock_id for s in ctx.momentum_new}

    momentum_rows = []
    for s in ctx.momentum.scores:
        if s.is_control_group:
            continue
        factors = {k: v for dim in s.factor_detail.values() for k, v in dim.items()}
        momentum_rows.append({
            "rank": s.rank,
            "stock_id": s.stock_id,
            "name": s.name or "",
            "total": round(s.total, 2),
            "fundamental": round(s.dimension_scores.get("fundamental", 0), 1),
            "chips": round(s.dimension_scores.get("chips", 0), 1),
            "technical": round(s.dimension_scores.get("technical", 0), 1),
            "is_new": s.stock_id in new_ids,
            "factors": {k: {"raw": _num(v.get("raw")), "score": round(v.get("score", 0), 1)}
                        for k, v in factors.items()},
        })

    reversal_block = None
    if ctx.reversal is not None:
        triggers = []
        for t in ctx.reversal.triggers:
            fired = {k: bool(v.get("fired")) for k, v in t["signals"].items()}
            triggers.append({
                "stock_id": t["stock_id"],
                "name": (t.get("name") or "")[:12],
                "state": t.get("state"),
                "invalidation_price": _num(t.get("invalidation_price")),
                "drawdown_pct": round(t["detail"].get("drawdown", 0) * 100, 1),
                "candle_quality": _num(t["detail"].get("candle_quality")),
                "signals": fired,
                "signal_summary": "＋".join(_SIG_NAME[k] for k, v in fired.items() if v) or "—",
            })
        reversal_block = {
            "qualified": ctx.reversal.qualified,
            "trigger_count": len(ctx.reversal.triggers),
            "control_count": len(ctx.reversal.control_group),
            "triggers": triggers,
        }

    watchlist_block = None
    if ctx.watchlist is not None:
        entries = []
        for e in ctx.watchlist.entries:
            entries.append({
                "stock_id": e.stock_id,
                "name": e.name or "",
                "note": e.note,
                "total": _num(e.total),
                "rank": e.rank,
                "prev_rank": e.prev_rank,
                "rank_change": e.rank_change,
                "fundamental": round(e.dimension_scores.get("fundamental", 0), 1) if e.dimension_scores else None,
                "chips": round(e.dimension_scores.get("chips", 0), 1) if e.dimension_scores else None,
                "technical": round(e.dimension_scores.get("technical", 0), 1) if e.dimension_scores else None,
                "filter_ok": e.filter_ok,
                "insufficient": e.insufficient,
                "bottom_alert": e.bottom_alert,
            })
        watchlist_block = {"n_stocks": ctx.watchlist.n_stocks, "entries": entries}

    return {
        "schema_version": SCHEMA_VERSION,
        "date": ctx.date,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "versions": {"momentum": ctx.momentum_version, "reversal": ctx.reversal_version},
        "fetch_failures": ctx.fetch_failures,
        "market_status": ctx.market_status,
        "momentum": {
            "top_n": ctx.top_n,
            "universe_size": ctx.momentum.universe_size,
            "passed_filters": ctx.momentum.passed_filters,
            "all_scored": ctx.momentum.all_scored,
            "new_entrants": [{"stock_id": s.stock_id, "name": s.name or ""} for s in ctx.momentum_new],
            "dropped": ctx.momentum_dropped,
            "rows": momentum_rows,
        },
        "reversal": reversal_block,
        "watchlist": watchlist_block,
    }


def write_web_output(web_dir: str | Path, data: dict) -> Path:
    """Write latest.json, the dated archive, and refresh index.json. Returns
    the data directory path."""
    data_dir = Path(web_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    (data_dir / "latest.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    date = data.get("date") or dt.date.today().isoformat()
    (data_dir / f"{date}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    index_path = data_dir / "index.json"
    dates: list[str] = []
    if index_path.exists():
        try:
            dates = json.loads(index_path.read_text(encoding="utf-8")).get("dates", [])
        except (json.JSONDecodeError, OSError):
            dates = []
    if date not in dates:
        dates.append(date)
    dates = sorted(set(dates), reverse=True)
    index_path.write_text(
        json.dumps({"latest": dates[0] if dates else date, "dates": dates},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return data_dir


def _num(v):
    """JSON-safe number: keep None, coerce numpy/pandas scalars to float."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return round(f, 4)
