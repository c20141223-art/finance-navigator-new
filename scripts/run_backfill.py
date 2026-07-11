#!/usr/bin/env python3
"""Initial backfill: at least 90 trading days of history (spec 1.1/1.2).

Usage:
    python scripts/run_backfill.py [--days 90] [--end-date 2026-07-11]
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
    parser.add_argument("--days", type=int, default=None, help="Overrides config data.min_backfill_trading_days")
    parser.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD, defaults to today")
    parser.add_argument("--db-path", type=str, default=str(db.DEFAULT_DB_PATH))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config()
    db.init_db(args.db_path)

    end_date = dt.date.fromisoformat(args.end_date) if args.end_date else dt.date.today()
    client = RateLimitedClient(config.http)

    with db.get_conn(args.db_path) as conn:
        pipeline.backfill(conn, client, config, end_date=end_date, n_days=args.days)

    print(f"Backfill complete through {end_date.isoformat()}. DB: {args.db_path}")


if __name__ == "__main__":
    main()
