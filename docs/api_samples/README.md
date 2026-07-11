# API 樣本狀態

**目前狀態：尚未驗證。** 這個目錄應該存放 `scripts/verify_api_samples.py`
實際打過的每個資料源的原始回應，但此專案的資料層是在一個對外網路白名單
（不含 twse.com.tw / tpex.org.tw / mops.twse.com.tw）的沙箱環境中開發的，
無法在該環境內完成規格書第 9 節要求的「每個 API 端點在實作前先實際請求
驗證格式」。

## 待辦（Phase 1 驗收前必做）

在有實際對外網路的環境（GitHub Actions runner 或本機）執行：

```bash
pip install -r requirements.txt
python scripts/verify_api_samples.py
```

這會把以下每個來源的真實回應存成檔案：

- `twse_daily_all.json` / `twse_daily_history.json`
- `tpex_daily_all.json` / `tpex_daily_history.json`
- `twse_institutional.json`
- `tpex_institutional.json`
- `twse_ex_rights.json`
- `twse_disposition.json`
- `tpex_disposition.html`（或 `.json`，視實際回應而定）
- `isin_listed.html` / `isin_otc.html`
- `mops_monthly_revenue_sii.html` / `mops_monthly_revenue_otc.html`

之後請對照 `stock_screener/fetchers/*.py` 內各 `parse_*` 函式預期的欄位名
稱，確認是否相符。已知風險最高、最可能對不上的兩個地方：

1. **`stock_screener/fetchers/tpex.py` 的 `_AADATA_COLUMNS`** — TPEx 舊式
   日期查詢端點慣例回傳 `{"aaData": [[...]]}`，欄位「沒有」名稱、純靠
   位置對應，目前的欄位順序是憑印象猜測，務必用真實樣本核對。
2. **`config/sources.yaml` 裡的 `twse_disposition` / `tpex_disposition`**
   — 處置股/注意股公告端點的路徑與回應格式不確定性最高。

所有 parser 在欄位對不上時會拋出 `SchemaMismatchError` 並清楚列出缺少
哪些欄位、實際收到哪些欄位，而不會靜默產生錯誤資料——但這只能抓到「明顯
對不上」的情況，抓不到「欄位存在但語意不同」這種更隱微的錯誤，所以人工
核對這一步不能省。
