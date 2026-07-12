# finance-navigator-new

## 台股選股工具（`stock_screener/`）

每日自動執行的台股選股系統，Phase 1（資料層）目前狀態如下。

### 驗證狀態：兩輪真實請求驗證完成（2026-07-11 / 07-12）

透過 `main` 上的 `Verify API samples` workflow（`workflow_dispatch`）從
GitHub Actions 對所有資料源做真實請求，逐項核對回應格式並修正 parser。
第二輪套用姊妹專案 stock-report 已驗證數月的 headers 配方後，TWSE 系
網域全數打通。

- **9 個來源已用真實樣本（含資料列）核對通過**：日行情（上市/上櫃、
  最新日/歷史日）、T86 三大法人、除權息預告、上市處置股、ISIN 基本資料。
- **3 個來源已改用交易所官方 swagger 目錄記載的端點**（TPEx 三大法人、
  TPEx 處置股、月營收彙總——原 MOPS 靜態檔路徑已下架 404，月營收改用
  兩交易所 openapi 的同一份報表），schema 依 swagger 實作、有單元測試，
  真實樣本會在下次 workflow 觸發或首次正式排程時自動補上；欄位若有出入
  會以 `SchemaMismatchError` 明確報錯並記錄於 `fetch_log`，不會靜默
  出錯。

逐項狀態表與破案紀錄（第一輪為何被擋、TWSE/TPEx 官網改版對格式的影響）
見 `docs/api_samples/README.md`。重新驗證：手動觸發 workflow，或

```bash
pip install -r requirements-stock-screener.txt
python scripts/verify_api_samples.py
```

### 目錄結構

```
config/sources.yaml       資料源端點與 rate limit 設定
stock_screener/
  db.py                   SQLite schema (daily_price / institutional /
                           monthly_revenue / risk_list / stock_meta /
                           corporate_action / triggers / config_versions /
                           fetch_log)
  config.py                config/sources.yaml 讀取
  http_client.py           rate-limited + retry 的 HTTP client
  adjust.py                還原股價（除權息回推調整係數）計算
  loaders.py                parse 結果 -> SQLite 的 upsert 邏輯
  pipeline.py               每日更新 / 回補流程，單一資料源失敗不影響其他來源
  fetchers/                各資料源的 fetch + parse
scripts/
  run_backfill.py           初始回補 >= 90 個交易日
  run_daily_update.py       每日增量更新（供 17:30 排程呼叫）
  run_monthly_revenue.py    每月 10-12 日執行的月營收更新
  verify_api_samples.py     在有網路的環境執行，抓取真實回應存入
                             docs/api_samples/
docs/api_samples/          API 真實回應樣本（待補，見上方限制說明）
tests/                      pytest 單元測試（35 tests，涵蓋 schema 驗證、
                             還原股價計算、rate limiter、pipeline 容錯）
```

### 還原股價設計

`daily_price` 只存原始（未還原）OHLC，不做任何覆寫。除權息事件記錄在
`corporate_action`（stock_id, ex_date, reference_price, prev_close,
adj_factor）。還原價由 `stock_screener.adjust.get_adjusted_prices()`
在查詢時即時計算：對每一筆歷史資料，乘上所有「除權息日晚於該日期」的
調整係數乘積（標準向前復權作法）。技術指標一律應該用這支函式取得的
還原價序列計算，不可直接用 `daily_price` 的原始收盤價。

### 執行方式

```bash
# 初次建置：回補至少 90 個交易日
python scripts/run_backfill.py

# 每日增量更新
python scripts/run_daily_update.py

# 每月 10-12 日另外執行
python scripts/run_monthly_revenue.py
```

### 測試

```bash
pip install -r requirements-stock-screener.txt
python -m pytest tests/ -v
```

### 版本紀律

所有參數調整＝修改 `config/*.yaml` + git commit，commit message 需寫明
調整理由與依據數據。單一因子調整需 ≥ 30 個觸發案例支撐。新參數僅適用
未來觸發，不回溯重算歷史紀錄。（此段適用於 Phase 2 之後的評分 config，
Phase 1 尚未有評分邏輯。）

---

## `etf_iopv_app.py`（既有、與選股工具無關）

Streamlit 部署的 ETF IOPV 應用，見 `Dockerfile` / `fly.toml`。與上述台股
選股工具是兩個獨立專案，共用同一個 repo。
