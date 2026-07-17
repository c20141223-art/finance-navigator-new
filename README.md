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

### Phase 2：排雷濾網＋順勢評分（已驗收結案，2026-07-14）

驗收：抽驗美時、台積電、智伸科三檔，bins 命中、維度封頂、權重合成
全部人工覆算正確。兩項驗收時確認的特性註記：

- **同分檔位排序無鑑別意義**：排名同分時以證券代號升冪做確定性
  tie-break（僅為可重現性，不含資訊）。前段同分群為現行粗 bins＋雙因子
  封頂的參數特性；依版本紀律，調參需 ≥ 30 個觸發案例證據，不預先調整。
- **revenue_trend_3m 需累積期**：月營收來源為快照端點、無歷史可回補
  （已查證兩交易所 openapi 目錄所有端點皆無參數），此因子需每日排程
  累積約 3 個月才開始有意義，期間基本面分項實質上只剩 revenue_yoy
  單因子。

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

### Phase 3：反轉雷達（已驗收結案）

跌深 → 止跌打底 → 轉強觸發的三階段時間序列偵測，與順勢評分完全分離、
屬高風險清單。全參數集中於 `config/reversal.yaml`；RSI 用威爾德平滑、
MACD 12-26-9（`stock_screener/indicators.py`）。觸發後由單一狀態機追蹤
（初次觸發 → 確認中 → 已確認／訊號失敗，失效判定價＝觸發日 K 棒最低點）。
落庫 `triggers`（profile=reversal），對照組＝只差一個條件未過者。詳見
`stock_screener/reversal.py` 模組 docstring。

### Phase 4：輸出整合＋每日排程（規格書 1.5／第 6 節）

每日完整流程一鍵串接並寄出 HTML email 報告。

- `scripts/run_daily_pipeline.py`：日增量更新 → 順勢篩選 → 反轉偵測 →
  狀態機推進 → T+5/20/60 報酬回填 → 觀察池雷達 → 組信 → 寄送。每個環節
  獨立 try/except，失敗記 `fetch_log` 並於信中「本日缺料／環節失敗」區塊
  標注，但不中斷後續（規格書第 9 節）。每月 10–20 日另跑月營收更新
  （每日抓、upsert 冪等）。
- `stock_screener/report_email.py`：四區塊 email——① 大盤狀態（加權指數
  vs 60MA、60MA 方向、多頭排列佔比，僅標注不計分）② 順勢 Top 30（分項
  得分＋新進榜／跌出榜醒目摘要）③ 反轉雷達（狀態機清單＋失效判定價＋
  高風險警語）④ 觀察池雷達（客觀分數＋排名變化＋連續墊底警示）。單欄
  640px、行動裝置可讀。
- `stock_screener/market_status.py`：補上 Phase 2 掛帳的加權指數缺項，
  讀 `index_price`（新增 TAIEX 日線來源 `twse_index_history`）算指數 vs
  60MA 與 60MA 方向。
- `stock_screener/returns.py`：對每筆 trigger 於足夠交易日後回填
  T+5/20/60 報酬與 MFE/MAE（還原價、以百分比存、填一次冪等）。
- `stock_screener/watchlist.py` + `config/watchlist.json`：觀察池只呈現
  客觀分數與排名變化，不加分、不進選股母體。先在 `watchlist.json` 的
  `stocks` 填入代號。
- `stock_screener/emailer.py`：Gmail SMTP 寄件，憑證只走環境變數、無備援
  值、缺 secret 明確報錯。

本地乾跑（不寄信、輸出 HTML 預覽檔）：

```bash
python scripts/run_daily_pipeline.py --no-email --save-html /tmp/preview.html
```

#### Phase 4 排程與憑證設定

**1. 新增 GitHub Secrets（per-repo，不會從 stock-report 自動共享）。**
到本 repo 的 **Settings → Secrets and variables → Actions → New repository
secret**，新增三個（名稱需完全一致）：

| Secret | 內容 |
|:--|:--|
| `GMAIL_USER` | 寄件 Gmail 帳號（完整 email） |
| `GMAIL_PASSWORD` | 該帳號的 Google **應用程式密碼**（非登入密碼；於 Google 帳戶 → 安全性 → 兩步驟驗證 → 應用程式密碼產生） |
| `MAIL_TO` | 收件人 email（可逗號分隔多個） |

程式端固定讀這三個環境變數、無任何備援值；缺任一者 `run_daily_pipeline`
會在寄信環節明確報錯（並記入 `fetch_log`），不會靜默略過。

**2. 手動驗收（規格書 5 的第一步）。** 把 `daily-pipeline.yml` 合併到
`main` 後，到 **Actions → Daily pipeline → Run workflow** 手動觸發一次
（首次無快取會先回補約 25–35 分鐘）。確認收到 email、四區塊正常後，再啟用
每日排程。工作流程也會把 email HTML 存成 `email-preview` artifact 供核對。

**3. 每日準點觸發（cron-job.org → workflow_dispatch）。** 選用外部觸發
而非 GitHub 內建 `schedule` 的理由：內建 cron 為 best-effort，高負載時常
延遲 5–30 分鐘甚至略過，無法穩定命中「收盤後 17:30」。於
[cron-job.org](https://cron-job.org) 建立一個排程，台灣時間每交易日 17:30
對以下 REST API 發 POST（需一顆有 `actions:write` 權限的 GitHub PAT）：

```
POST https://api.github.com/repos/c20141223-art/finance-navigator-new/actions/workflows/daily-pipeline.yml/dispatches
Authorization: Bearer <PAT>
Accept: application/vnd.github+json
Body: {"ref": "main"}
```

（`daily-pipeline.yml` 會 checkout 開發分支執行；工作流程本體需在 `main`
上，`workflow_dispatch` 才會出現在 Actions UI 且可被 API 呼叫。）DB 以
GitHub Actions rolling cache 持久化累積歷史，不將二進位 DB 進 git。

### 目錄結構

```
config/sources.yaml       資料源端點與 rate limit 設定
config/momentum.yaml      順勢評分門檻/權重（Phase 2）
config/reversal.yaml      反轉雷達三階段參數（Phase 3）
config/watchlist.json     觀察池代號清單（Phase 4，使用者填寫）
stock_screener/
  db.py                   SQLite schema (daily_price / index_price /
                           institutional / monthly_revenue / risk_list /
                           stock_meta / corporate_action / triggers /
                           config_versions / fetch_log)
  config.py                config/sources.yaml 讀取
  http_client.py           rate-limited + retry 的 HTTP client
  adjust.py                還原股價（除權息回推調整係數）計算
  loaders.py                parse 結果 -> SQLite 的 upsert 邏輯
  pipeline.py               每日更新 / 回補流程，單一資料源失敗不影響其他來源
  fetchers/                各資料源的 fetch + parse
  scoring.py               config 驅動計分器（Phase 2）
  momentum.py              排雷濾網＋順勢評分引擎（Phase 2）
  indicators.py            威爾德 RSI / MACD 12-26-9（Phase 3）
  reversal.py              反轉雷達＋狀態機（Phase 3）
  market_status.py         大盤狀態標記（加權指數 vs 60MA 等，Phase 4）
  returns.py               T+5/20/60 報酬與 MFE/MAE 回填（Phase 4）
  watchlist.py             觀察池雷達（客觀分數＋排名變化，Phase 4）
  report_email.py          四區塊 HTML email 組裝（Phase 4）
  emailer.py               Gmail SMTP 寄件，憑證只走環境變數（Phase 4）
scripts/
  run_backfill.py           初始回補 >= 90 個交易日
  run_daily_update.py       每日增量更新（僅抓取，不含篩選/寄信）
  run_daily_pipeline.py     每日完整流程＋寄信（供 17:30 排程呼叫，Phase 4）
  run_screen.py             跑單日順勢篩選並列印排名表
  run_monthly_revenue.py    月營收更新（每月 10-20 日）
  export_screen_report.py   順勢篩選 markdown 報告
  export_reversal_report.py 反轉雷達 markdown 報告
  verify_api_samples.py     在有網路的環境執行，抓取真實回應存入
                             docs/api_samples/
.github/workflows/
  daily-pipeline.yml        每日完整流程（workflow_dispatch，Phase 4）
  run-screen-report.yml     順勢／反轉 markdown 報告驗收工作流程
docs/api_samples/          API 真實回應樣本與驗證記錄（三輪已完成）
tests/                      pytest 單元測試（99 項；涵蓋 schema、還原股價、
                             指標、反轉、報酬回填、觀察池、email 組裝等）
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
