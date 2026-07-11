#!/usr/bin/env python3
"""Daily incremental update, meant to run at 17:30 Asia/Taipei via
cron-job.org -> GitHub Actions (spec 1.5).

Usage:
    python scripts/run_daily_update.py [--date 2026-07-11]

On the 10th-12th of the month, also run scripts/run_monthly_revenue.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_screener import db, pipeline
from stock_screener.config import load_config
from stock_screener.http_client import RateLimitedClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, defaults to today")
    parser.add_argument("--db-path", type=str, default=str(db.DEFAULT_DB_PATH))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config()
    db.init_db(args.db_path)

    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    client = RateLimitedClient(config.http)

    with db.get_conn(args.db_path) as conn:
        pipeline.daily_update(conn, client, config, date=date)

    print(f"Daily update complete for {date.isoformat()}. DB: {args.db_path}")


if __name__ == "__main__":
    main()
