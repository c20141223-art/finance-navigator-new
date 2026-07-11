#!/usr/bin/env python3
"""Monthly revenue update. Run around the 10th-12th of each month once MOPS
has published the prior month's aggregate revenue file (spec 1.5).

Usage:
    python scripts/run_monthly_revenue.py [--month 2026-06]
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
    parser.add_argument("--month", type=str, default=None, help="YYYY-MM, defaults to previous month")
    parser.add_argument("--db-path", type=str, default=str(db.DEFAULT_DB_PATH))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config()
    db.init_db(args.db_path)

    if args.month:
        year, mon = (int(x) for x in args.month.split("-"))
        month = dt.date(year, mon, 1)
    else:
        today = dt.date.today()
        first_of_this_month = today.replace(day=1)
        month = (first_of_this_month - dt.timedelta(days=1)).replace(day=1)

    client = RateLimitedClient(config.http)

    with db.get_conn(args.db_path) as conn:
        pipeline.update_monthly_revenue(conn, client, config, month)

    print(f"Monthly revenue update complete for {month.strftime('%Y-%m')}. DB: {args.db_path}")


if __name__ == "__main__":
    main()
