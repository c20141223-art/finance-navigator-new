# API 樣本狀態

**兩輪真實驗證完成（2026-07-11 / 07-12，GitHub Actions `Verify API
samples` workflow）。** 第二輪套用 stock-report 的 headers 配方後，TWSE
系網域全數打通，所有核心資料源的欄位格式已用真實樣本核對並修正。

## 最終逐項狀態

| 來源 | 狀態 | 驗證依據 |
|---|---|---|
| `twse_daily_all` | ✅ 樣本核對通過 | 1,369 筆真實資料完整解析 |
| `twse_daily_history` | ✅ 樣本核對後修正 | 回應已改為 `tables` 包裝格式（跟 TPEx 改版後一致），parser 重寫；台積電 07/07 收盤 2440 等數值可對看盤軟體抽查 |
| `twse_institutional`（T86） | ✅ 樣本核對後修正 | 外資欄位實名為「外陸資買賣超股數(不含外資自營商)」，關鍵字比對已修正；14,526 筆解析成功 |
| `twse_ex_rights`（TWT49U） | ✅ 樣本核對後修正 | 回傳的是未來除權息「預告區間」，每列有自己的 資料日期——parser 改用每列日期當 ex_date |
| `twse_disposition` | ✅ 樣本核對後修正 | 每列含 處置起迄時間 區間，parser 改為只納入查詢日落在區間內的股票 |
| `tpex_daily_all` | ✅ 樣本核對通過 | 10,093 筆（第一輪已修正欄名） |
| `tpex_daily_history` | ✅ 樣本核對通過 | 第二輪抓到 1,012 筆有資料的回應，數值層級確認 |
| `isin_listed` / `isin_otc` | ✅ 樣本核對後修正 | 站方已改用 UTF-8（非舊 Big5）；表頭在資料列第 0 列需自行提升；上市 31,021 筆（含 ETF/權證）、台積電＝半導體業 ✓ |
| `tpex_institutional` | ⚠️ schema 已確認、待真實樣本 | 第二輪證實舊猜測路徑錯誤（回首頁）；正確路徑 `/tpex_3insti_daily_trading` 取自官方 swagger（`_tpex_openapi_swagger.json`），parser 依 swagger 欄名實作（欄名有不規則空格，已做空格不敏感比對） |
| `tpex_disposition` | ⚠️ schema 已確認、待真實樣本 | 舊 print 頁 404、新頁面 JS 渲染無表格；改用 swagger 中的 `/tpex_disposal_information` JSON 端點 |
| `monthly_revenue_sii` / `_otc` | ⚠️ schema 已確認、待真實樣本 | 原 MOPS 靜態檔（nas/t21）404，該發布路徑已下架。改用兩交易所 openapi 的月營收彙總表（`/opendata/t187ap05_L`、`/mopsfin_t187ap05_O`），兩者中文欄名完全一致，且都在已驗證打通的網域上——不需要用到 data.gov.tw |

## 破案紀錄：第一輪為什麼被擋

第一輪的 UA 帶自我識別 URL（`+https://github.com/...`），TWSE 的機器人
防護按此特徵攔截（攔截頁或無 Location 的 307——已用本機測試排除
redirect-following 的嫌疑）。第二輪改用 stock-report 驗證過的瀏覽器型
UA + Referer 配方後全數放行。此配方現為 `fetchers/*.py` 內建預設。

## 殘餘事項（不阻擋 Phase 1 定稿）

三個 ⚠️ 來源的端點路徑與欄位 schema 都取自交易所官方 swagger 目錄
（非猜測），parser 依此實作並有單元測試，但尚未抓過真實回應。它們會在
下一次 workflow 觸發或第一次正式排程執行時自動留下真實樣本
（`verify_api_samples.py` 已納入這些端點），屆時若欄位有出入，
`SchemaMismatchError` 會明確報錯並記錄於 `fetch_log`，不會靜默出錯。

`_twse_openapi_swagger.json` / `_tpex_openapi_swagger.json` 為兩站完整
API 目錄，日後找新資料源時先查這兩份，不要再猜端點。
