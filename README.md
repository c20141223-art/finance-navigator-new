# finance-navigator-new

## 台股選股工具（`stock_screener/`）

每日自動執行的台股選股系統，Phase 1（資料層）目前狀態如下。

### 驗證狀態：三輪真實請求驗證完成，Phase 1 資料層結案（2026-07-12）

透過 `main` 上的 `Verify API samples` workflow（`workflow_dispatch`）從
GitHub Actions 對所有 13 個資料源做真實請求，逐項核對回應格式並修正
parser——全部通過，含使用者以看盤軟體抽查 2330 開高低收一致。

兩個重要口徑註記：

- **成交量口徑**：本系統採 TWSE/TPEx 官方日成交資料，比看盤軟體的
  盤中撮合量大（官方整批納入盤後定價、鉅額、零股；2330 抽查差約
  +13%）。全系統同口徑，對比率型量能因子影響大致抵銷；單日鉅額脈衝
  為已知雜訊源，留待因子歸因檢驗。詳見 `docs/api_samples/README.md`。
- **月營收快照**：openapi 端點只含已公布的公司，晚於 10–12 日窗口
  公布者會漏接，Phase 4 排程時建議延長執行區間（upsert 冪等，多跑
  無害）。

逐項狀態表、成交量鑑別記錄、破案紀錄見 `docs/api_samples/README.md`。
重新驗證：手動觸發 workflow，或

```bash
pip install -r requirements-stock-screener.txt
python scripts/verify_api_samples.py
```

### Phase 2：排雷濾網＋順勢評分（已實作，待驗收）

- `config/momentum.yaml`：全部門檻、bins、權重（規格書第 7 節結構）。
  bins 語意為由上而下第一個命中給分；`range: [a, b]` 含下界不含上界。
- `stock_screener/scoring.py`：config 載入＋通用計分器（維度內加總後
  以 100 封頂，再依權重合成總分）。
- `stock_screener/momentum.py`：排雷濾網（流動性/價格下限/排雷名單/
  資料完整性/僅普通股）＋六因子計算＋排名＋triggers 落庫（Top 30 與
  對照組 31–50 名，`factor_detail` JSON 含每個因子的原始值與得分，
  供人工覆算；同日重跑冪等取代）。
- `scripts/run_screen.py`：跑單日篩選並列印完整排名表（含分項得分與
  因子原始值）。
- 各回看窗口的精確定義（交易日、連買中斷、還原價 60MA 等）見
  `stock_screener/momentum.py` 模組 docstring——人工覆算以此為準。

```bash
# 需先有 >= 60 個交易日資料（scripts/run_backfill.py）
python scripts/run_screen.py --date 2026-07-14
```

### 目錄結構

```
config/sources.yaml       資料源端點與 rate limit 設定
config/momentum.yaml      順勢評分門檻/權重（Phase 2）
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
docs/api_samples/          API 真實回應樣本與驗證記錄（三輪已完成）
tests/                      pytest 單元測試（涵蓋 schema 驗證、還原股價
                             計算、rate limiter、pipeline 容錯）
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
