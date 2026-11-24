# 試跑指南

以下步驟都假設已依 [README 的快速開始](../README.md#快速開始)啟動資料庫、套用遷移並載入模擬主資料。各功能的設計見 [系統設計](DESIGN.md)，交付範圍見 [PLAN.md](PLAN.md)。

## 試跑完整匯入

1. 上傳 [C40 有效樣本](../samples/files/MES_OLD_C40_1151103.csv)，確認入庫 **6 筆**。
2. 上傳 [C40 異常樣本](../samples/files/MES_OLD_C40_1151102.csv)，確認入庫 **0 筆**。下載 Excel 錯誤報告，核對第 **41** 列的量測值空白和第 **78** 列的非數字。
3. 再上傳有效樣本。歷史會顯示「重複上傳」及原始匯入連結，檢驗紀錄筆數維持不變。

程式介接時，`POST /imports?file_name=...` 每次接收一個 `application/octet-stream` 檔案，單檔上限為 5 MiB。

## 試跑批號追溯

在「批號追溯」查 `L261102A001`，往上游可找到 `ABF-3619` 和 `CORE-5456`。從 `ABF-3619` 往下游可查到六個使用它的生產批。`L261102A001` 從 B20 雷射鑽孔出站至 B60 電鍍銅進站的等待約 **3.73 小時**，上限為 **8 小時**。期間查詢可使用台北時間 **2026-11-02 00:00 至 2026-11-05 00:00**。

## 試跑製程管制

上傳 [C40 有效樣本](../samples/files/MES_OLD_C40_1151103.csv)後，開啟「製程管制」，輸入 `CORE_LW`、`LIN-01`，將基準點數設成 **5**。圖上會有 6 筆量測，前 5 筆用來算管制界線，第 6 筆用來監測。

I-MR 管制圖由兩張圖組成。I 圖看每筆量測值，MR 圖看相鄰兩筆相差多少。I 圖標示超出 3 個標準差及連續 8 點在中心線同側的訊號，MR 圖標示相鄰變動超出管制上限的訊號。

可獨立重跑模擬情境。腳本用產生器的乾淨量測與事件答案，以機台、項目、影響批號及偏移方向核對訊號：

```powershell
uv run python -m scripts.evaluate_spc
```

固定 seed 42、中型資料及前 20 點基準下，雷射孔徑漂移 **3/3 段**、ABF 介電層厚度突變 **1/1 段**觸發預期方向的 I 圖訊號。命中表示受影響批號內至少一點觸發訊號，評估範圍限於這兩組序列。

## 試跑自訂匯入範本（P9）

從同一批模擬資料產生一份不同表頭的報表：

```powershell
uv run python -m generator.vendor_report --out out/vendor-report.csv
```

在「匯入範本」選取報表，填入表頭第 **2** 列，再按「預覽表頭」。核對欄位、將日期選成「日/月/年 時:分:秒」，填名稱並儲存。核准後，回到「上傳匯入」選擇該範本及同一份 CSV。只要既有量測仍未入庫，就會新增 6 筆；已存在的相同量測可重用原紀錄。

若要啟用「本機 AI 建議」，在主機直接啟動 API 前設定 `INSPECTION_TEMPLATE_MODEL_URL`（例如 `http://127.0.0.1:8080/v1`）與 `INSPECTION_TEMPLATE_MODEL`。系統只送出表頭文字，建議仍需逐欄核對與核准。核准資料保留版本和核准者，以單機使用者輸入記名。

## 試跑固定版面 PDF（P10）

如果沿用先前的 Docker 映像，先執行 `docker compose up -d --build`。再產生一份模擬化驗報告：

```powershell
uv run --locked python -m generator.lab_report --out out/lab-report.pdf
```

開啟 [本機匯入頁](http://127.0.0.1:8000/ui/?tab=upload)，保留「自動辨識」後上傳。內附範例含 6 筆量測。這些量測與 C40 有效樣本相同，已入庫的相同值會重用既有紀錄。重新上傳相同 PDF 時，匯入歷史會連回第一次處理。

固定格式必須有可擷取的文字，每頁有 v1 標題、欄位與頁尾。錯誤報告使用 PDF 擷取後的連續文字列號。掃描件、加密檔和其他版面超出此範圍。

## 連接本機 MCP

將下面的命令和參數填入 MCP 客戶端的本機伺服器設定。客戶端會啟動此程序，並透過標準輸入輸出（stdio）交換查詢。

```text
command: uv
args: --directory <專案絕對路徑> run --locked python -m inspection_trace.mcp_server
```

可先呼叫 `trace_lot`，傳入 `lot_no=L261102A001`、`direction=up`，確認回傳原料批及追溯層數。上傳[匯入樣本](#試跑完整匯入)後，可再呼叫 `query_records` 或 `spc_status` 核對量測 ID 和訊號。工具、回傳限制與稽核規則見 [系統設計](DESIGN.md#8-mcp-伺服器)。

## 建立自訂模擬情境（P11）

1. 執行 `docker compose up -d --build`，在工作頁選「模擬情境」。
2. 開啟「ABF 載板製程」並點「複製為自訂情境」。改預設名稱、機台數量或每片量測點數。站點可新增、移除及調整順序。
3. 選規模並按「預覽與驗證」。畫面會顯示阻擋錯誤、警告、原始批數、預估量測筆數及報表格式。
4. 按「儲存預設」，重新預覽後勾選確認。按「產生資料集」並下載 ZIP。

ZIP 包含 `truth/`、`files/`、`manifest.csv`、`summary.json`、情境快照和答案清單。相同設定、規模與亂數種子可重現量測和事件，ZIP 及 XLSX 的檔案時間可能不同。每次產生使用獨立目錄。

可匯出 JSON 設定檔，或把 JSON、現有情境 TOML 和選填的髒資料 TOML 載入為草稿。移除自訂預設會保留已產生的資料集及封存的設定。「載入內建 ABF」可回到原始預設。

Docker 使用 `scenarios` 資料卷，直接執行 API 時預設使用 `out/scenarios/`，也可設定 `INSPECTION_SCENARIO_ROOT`。產生只寫入情境儲存區。若要用新資料驗證匯入、批號追溯與管制圖，可用 `uv run python -m inspection_trace.seed --truth-dir <解壓後的 truth 目錄> --check` 預覽，再執行同一指令去掉 `--check` 並確認，最後上傳 `files/` 的報表。不同情境若重用相同主資料代碼，請使用各自的驗收資料庫。

每個站點可選一種既有報表來源。機台 CSV 支援計數項目，其餘三種支援連續量測。連續量測的點數由情境統一指定，計數項目每片一筆。異常沿用原有六種事件，無檢驗項目的站點仍會產生進出站紀錄。[情境資料與限制](DESIGN.md#12-模擬情境管理p11)

## 匯出展示資料

產生給靜態展示頁使用的 JSON。腳本會載入 `samples/` 的模擬主資料，透過匯入流程上傳全部樣本檔，再用與 REST API 相同的查詢服務取得匯入結果、錯誤報告、批號追溯、等待時間與管制圖。所有寫入都在臨時 schema 內進行，結束後回滾，不會改動現有資料。

```powershell
uv run python -m scripts.export_demo --out out/inspection-trace-demo.json
```

只需要資料庫已啟動並完成遷移，不需要先載入主資料。全部樣本的判定都符合答案時，結束代碼為 0。

模擬情境的展示資料另外匯出。腳本用與網頁「模擬情境」頁相同的預覽驗證，計算內建 ABF 預設在小、中、大型規模下的結果，以及幾種修改範例（抽樣片數超過每批片數、目標值超出規格上限、資料量超過上限、關閉原料突變）。不需要資料庫：

```powershell
uv run python -m scripts.export_scenario_demo --out out/inspection-trace-scenarios.json
```
