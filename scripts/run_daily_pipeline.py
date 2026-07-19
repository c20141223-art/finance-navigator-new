#!/usr/bin/env python3
"""每日完整流程（規格書 1.5 / 第 6 節）— 供 17:30（台灣時間）排程呼叫。

順序：日增量更新 → 順勢篩選 → 反轉偵測 → 狀態機推進 → T+5/20/60 報酬回填
→ 觀察池雷達 → 組信 → 寄送。每個環節獨立 try/except：任一環節失敗會記入
fetch_log 並收集到「本日缺料 / 環節失敗」清單，於信中明確標注，但不中斷後
續環節（規格書第 9 節）。每月 10–20 日另跑月營收更新（每日抓、upsert 冪等，
Phase 1 掛帳的決定）。

寄件憑證只走環境變數（GitHub Secrets）：GMAIL_USER / GMAIL_PASSWORD /
MAIL_TO，程式端無備援值，缺 secret 時 emailer 會明確報錯。

Usage:
    python scripts/run_daily_pipeline.py [--date YYYY-MM-DD] [--db-path ...]
                                         [--no-email] [--save-html PATH]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_screener import (
    db, emailer, loaders, market_status, momentum, pipeline, report_email,
    returns, reversal, watchlist, web_export,
)
from stock_screener.config import load_config
from stock_screener.http_client import RateLimitedClient
from stock_screener.scoring import load_momentum_config

logger = logging.getLogger("daily_pipeline")

MONTHLY_REVENUE_DAYS = range(10, 21)  # 每月 10–20 日執行月營收更新


class StepRunner:
    """Runs each pipeline step in isolation; records failures for the email
    banner and to fetch_log, never lets one step abort the run."""

    def __init__(self, conn, run_date: dt.date):
        self.conn = conn
        self.run_date = run_date
        self.failures: list[dict] = []

    def run(self, name: str, fn, default=None):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - deliberate isolation
            logger.error("Step %s failed: %s", name, exc)
            logger.debug("%s", traceback.format_exc())
            self.failures.append({"source": name, "error": str(exc)})
            try:
                loaders.log_fetch_result(self.conn, self.run_date, name,
                                         "failure", None, str(exc))
            except Exception:  # logging must never itself abort the run
                logger.exception("Could not write fetch_log for failed step %s", name)
            return default


def _fetch_log_failures(conn, run_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT source, error_message FROM fetch_log "
        "WHERE date = ? AND status = 'failure' ORDER BY id",
        (run_date,),
    ).fetchall()
    # de-dup by source, keep the last error
    seen: dict[str, str] = {}
    for src, err in rows:
        seen[src] = err or "（無錯誤訊息）"
    return [{"source": s, "error": e} for s, e in seen.items()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, defaults to today")
    parser.add_argument("--db-path", type=str, default=str(db.DEFAULT_DB_PATH))
    parser.add_argument("--no-email", action="store_true", help="run everything but skip sending")
    parser.add_argument("--save-html", type=str, default=None, help="also write the HTML to this path")
    parser.add_argument("--web-dir", type=str, default="web",
                        help="write the dashboard JSON under <web-dir>/data/ (Pages output)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    run_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    config = load_config()
    mcfg = load_momentum_config()
    rcfg, rversion = reversal.load_reversal_config()
    wl_cfg = watchlist.load_watchlist()
    db.init_db(args.db_path)
    client = RateLimitedClient(config.http)

    with db.get_conn(args.db_path) as conn:
        steps = StepRunner(conn, run_date)

        # 1. 日增量更新（含大盤指數、法人、排雷、除權息）
        steps.run("daily_update",
                  lambda: pipeline.daily_update(conn, client, config, date=run_date))

        # 2. 月營收（每月 10–20 日；每日抓、upsert 冪等）
        if run_date.day in MONTHLY_REVENUE_DAYS:
            steps.run("monthly_revenue",
                      lambda: pipeline.update_monthly_revenue(conn, client, config, run_date))

        # 3. 順勢篩選（含變動摘要，需在落庫前算）
        screen = steps.run("momentum_screen",
                           lambda: momentum.run_screen(conn, mcfg, run_date))
        new_entrants, dropped = [], []
        if screen is not None:
            new_entrants, dropped = steps.run(
                "momentum_variance",
                lambda: momentum.topn_variance(conn, screen, mcfg.top_n),
                default=([], [])) or ([], [])
            steps.run("momentum_persist", lambda: (
                momentum.record_config_version(conn, mcfg, screen.date),
                momentum.persist_triggers(conn, screen)))

        # 4/5. 反轉偵測 + 狀態機推進（run_reversal_scan 內含狀態機）
        radar = steps.run("reversal_scan",
                          lambda: reversal.run_reversal_scan(conn, rcfg, mcfg, run_date, rversion))
        if radar is not None:
            steps.run("reversal_persist", lambda: reversal.persist_reversal(conn, radar))

        # 6. T+5/20/60 報酬回填
        steps.run("returns_backfill", lambda: returns.backfill_returns(conn))

        # 7. 觀察池雷達
        wl_result = steps.run("watchlist_radar",
                             lambda: watchlist.build_watchlist_radar(conn, mcfg, run_date, wl_cfg))
        if wl_result is not None:
            steps.run("watchlist_persist",
                      lambda: watchlist.persist_watchlist(conn, wl_result, mcfg.version_hash))

        # 8. 大盤狀態（合併指數面與寬度面）
        asof = (screen.date if screen else
                (radar.date if radar else run_date.isoformat()))
        breadth = screen.market_regime if screen else {}
        ms = steps.run("market_status",
                      lambda: market_status.build_market_status(conn, asof, breadth),
                      default={"index_available": False, **({} if screen else {})}) or {}

        # combine step failures with per-source fetch_log failures for the banner
        banner = {f["source"]: f for f in _fetch_log_failures(conn, run_date.isoformat())}
        for f in steps.failures:
            banner.setdefault(f["source"], f)
        failures = list(banner.values())

        if screen is None:
            # nothing to report on — record and (optionally) send a minimal alert
            logger.error("順勢篩選未產生結果（可能資料不足）；仍嘗試寄出缺料通知。")

        ctx = report_email.ReportContext(
            date=asof,
            market_status=ms or {"index_available": False},
            momentum=screen if screen is not None else _empty_screen(asof, mcfg.version_hash),
            momentum_new=new_entrants, momentum_dropped=dropped,
            reversal=radar, watchlist=wl_result,
            fetch_failures=failures,
            momentum_version=mcfg.version_hash, reversal_version=rversion,
            top_n=mcfg.top_n,
        )
        subject, html_doc, text = report_email.build_report(ctx)

        # static dashboard JSON (GitHub Pages) — additive, never blocks email
        steps.run("web_export", lambda: web_export.write_web_output(
            args.web_dir, web_export.build_dashboard_data(ctx)))
        # web_export failures joined the banner too late for this run's email,
        # but are recorded in fetch_log and will show next run.

    if args.save_html:
        Path(args.save_html).write_text(html_doc, encoding="utf-8")
        logger.info("HTML written to %s", args.save_html)

    if args.no_email:
        logger.info("--no-email set; skipping send. Subject would be: %s", subject)
    else:
        recipients = emailer.send_report(subject, html_doc, text_body=text)
        logger.info("Sent to %s", recipients)

    print(f"Daily pipeline done for {run_date.isoformat()} "
          f"(asof {ctx.date}); {len(failures)} issue(s) flagged.")


def _empty_screen(asof: str, version: str):
    return momentum.ScreenResult(
        date=asof, universe_size=0, passed_filters=0, scores=[], all_scored=0,
        market_regime={}, config_version=version)


if __name__ == "__main__":
    main()
