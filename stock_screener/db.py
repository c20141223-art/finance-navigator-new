"""SQLite schema and connection helpers for the Taiwan stock screener.

Design notes
------------
- `daily_price` always stores the *raw* (as-reported) OHLCV. We never
  overwrite it with adjusted values, because adjusted price depends on the
  set of corporate actions known *at query time* and must stay reproducible.
- `corporate_action` records ex-rights/ex-dividend reference prices. Adjusted
  (back-adjusted) prices are derived on demand by `stock_screener.adjust`,
  not pre-baked into `daily_price`.
- `fetch_log` is not in the spec's suggested table list but is needed to
  satisfy "任一資料源失敗不可讓整個 pipeline 崩潰，缺料日在報告中明確標注"
  — it's the durable record of which source succeeded/failed on which date.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "market.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_price (
    stock_id    TEXT    NOT NULL,
    date        TEXT    NOT NULL,   -- ISO 'YYYY-MM-DD'
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL    NOT NULL,
    volume      INTEGER,            -- 張 (1000 股)
    turnover    INTEGER,            -- 成交金額
    source      TEXT    NOT NULL,   -- 'twse' / 'tpex'
    PRIMARY KEY (stock_id, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_price_date ON daily_price(date);

CREATE TABLE IF NOT EXISTS institutional (
    stock_id    TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    foreign_net INTEGER,            -- 外資買賣超（張）
    trust_net   INTEGER,            -- 投信買賣超（張）
    dealer_net  INTEGER,            -- 自營商買賣超（張）
    source      TEXT    NOT NULL,
    PRIMARY KEY (stock_id, date)
);
CREATE INDEX IF NOT EXISTS idx_institutional_date ON institutional(date);

CREATE TABLE IF NOT EXISTS monthly_revenue (
    stock_id        TEXT    NOT NULL,
    year_month      TEXT    NOT NULL,   -- 'YYYY-MM', 西元年月
    revenue         INTEGER,            -- 單月營收（千元）
    yoy             REAL,               -- 單月營收年增率 (%)
    mom             REAL,               -- 單月營收月增率 (%)
    cumulative_yoy  REAL,               -- 累計營收年增率 (%)
    announced_date  TEXT,               -- 公告日期（若來源有提供）
    source          TEXT    NOT NULL,
    PRIMARY KEY (stock_id, year_month)
);

CREATE TABLE IF NOT EXISTS risk_list (
    date        TEXT    NOT NULL,
    stock_id    TEXT    NOT NULL,
    reason      TEXT    NOT NULL,   -- '處置股' / '全額交割' / '注意股'
    source      TEXT    NOT NULL,
    PRIMARY KEY (date, stock_id, reason)
);

CREATE TABLE IF NOT EXISTS stock_meta (
    stock_id        TEXT    PRIMARY KEY,
    name            TEXT,
    market          TEXT,           -- '上市' / '上櫃'
    industry        TEXT,
    listed_date     TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    last_seen_date  TEXT,
    missing_days    INTEGER NOT NULL DEFAULT 0
);

-- 除權除息參考價，用於回推還原股價調整係數 (見 stock_screener/adjust.py)
CREATE TABLE IF NOT EXISTS corporate_action (
    stock_id        TEXT    NOT NULL,
    ex_date         TEXT    NOT NULL,
    reference_price REAL    NOT NULL,   -- 除權息參考價
    prev_close      REAL    NOT NULL,   -- 除權息前一交易日收盤價（原始）
    adj_factor      REAL    NOT NULL,   -- prev_close / reference_price
    source          TEXT    NOT NULL,
    PRIMARY KEY (stock_id, ex_date)
);

-- 觸發紀錄。Schema 於 Phase 1 建立，實際寫入邏輯屬於 Phase 2/3。
CREATE TABLE IF NOT EXISTS triggers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    stock_id        TEXT    NOT NULL,
    profile         TEXT    NOT NULL,   -- 'momentum' / 'reversal'
    is_control_group INTEGER NOT NULL DEFAULT 0,
    rank            INTEGER,
    total_score     REAL,
    factor_detail   TEXT,               -- JSON: 各因子原始值與得分
    market_regime   TEXT,               -- 當日大盤狀態標記
    config_version  TEXT,               -- config_versions.version_hash
    return_t5       REAL,
    return_t20      REAL,
    return_t60      REAL,
    mfe             REAL,
    mae             REAL,
    reversal_state  TEXT                -- 反轉 profile 最終狀態
);
CREATE INDEX IF NOT EXISTS idx_triggers_date ON triggers(date);
CREATE INDEX IF NOT EXISTS idx_triggers_stock ON triggers(stock_id);

CREATE TABLE IF NOT EXISTS config_versions (
    version_hash    TEXT    PRIMARY KEY,
    date            TEXT    NOT NULL,
    note            TEXT
);

-- 每日各資料源抓取結果，用於「缺料日明確標注」與重試判斷
CREATE TABLE IF NOT EXISTS fetch_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,
    source          TEXT    NOT NULL,   -- e.g. 'twse_daily_all'
    status          TEXT    NOT NULL,   -- 'success' / 'failure' / 'partial'
    record_count    INTEGER,
    error_message   TEXT,
    fetched_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_date ON fetch_log(date, source);
"""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn(db_path: Path | str = DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
