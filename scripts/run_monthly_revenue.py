#!/usr/bin/env python3
"""Monthly revenue update. Run around the 10th-12th of each month (spec
1.5). The openapi endpoints return the latest published month; each row's
資料年月 decides what gets stored, so no month argument is needed.

Usage:
    python scripts/run_monthly_revenue.py
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
    parser.add_argument("--db-path", type=str, default=str(db.DEFAULT_DB_PATH))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config()
    db.init_db(args.db_path)

    client = RateLimitedClient(config.http)

    with db.get_conn(args.db_path) as conn:
        pipeline.update_monthly_revenue(conn, client, config)

    print(f"Monthly revenue update complete. DB: {args.db_path}")


if __name__ == "__main__":
    main()
