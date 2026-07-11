# finance-navigator-new

## 台股選股工具（`stock_screener/`）

每日自動執行的台股選股系統，Phase 1（資料層）目前狀態如下。

### ⚠️ 已知限制：第一輪真實驗證完成，Phase 1 尚未定稿

2026-07-11 透過 `.github/workflows/verify-api-samples.yml`（在 `main` 上，
`workflow_dispatch` 手動觸發，執行內容針對這個 feature branch）跑過一次
`scripts/verify_api_samples.py`，對 11 個資料源都打了真實請求。結果：

- **已確認並修正**：`tpex_daily_all`、`tpex_daily_history`（TPEx 官網
  2024-10-27 改版後回應格式整個變了，原本假設的舊式 `aaData` 格式是錯的，
  已重寫成真正的 `tables` 格式）。
- **端點可用但當次沒驗到欄位內容**：`twse_institutional`（T86）——網址
  和回應外層格式正確，但查詢日剛好沒有資料。
- **仍然打不通**：`twse_daily_all` / `isin_listed` / `isin_otc` /
  `mops_monthly_revenue_*` 被 TWSE 網站的機器人防護規則擋下（回攔截頁）；
  `twse_daily_history` / `twse_disposition` / `twse_ex_rights` 回
  HTTP 307；`tpex_institutional` 端點名稱錯誤（回首頁樣板）；
  `tpex_disposition` 舊網址 404，已換新網址但未驗證。

完整逐項結果、已知原因、下一輪待辦，見 `docs/api_samples/README.md`。
**在這些項目全部確認之前，不能視為 Phase 1 定稿**——尤其是 TWSE 網站的
機器人防護規則會不會持續擋掉正式排程的請求，是需要另外討論對策的風險，
不是單純改程式碼就能解決的問題。

再次驗證：

```bash
pip install -r requirements-stock-screener.txt
python scripts/verify_api_samples.py
```

或直接在 GitHub 手動觸發 `Verify API samples` workflow。所有 parser 在
欄位對不上時會拋出 `SchemaMismatchError` 並清楚列出缺少哪些欄位，不會
靜默產生錯誤資料。

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
