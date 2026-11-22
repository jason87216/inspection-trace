# Inspection Trace

Inspection Trace 將分散的製程檢驗報表轉成可查詢的量測紀錄，並依批號追溯原料、下游生產批及進出站時間。專案使用自行生成的模擬資料，可透過 Docker Compose 在本機重現。

## 功能

| 功能 | 說明 |
| --- | --- |
| 檢驗報表匯入 | 支援 Big5 舊系統 CSV、UTF-8 機台 CSV、Excel 站點寬表與 Excel 人工表單 |
| 資料驗證與重傳控制 | 正規化日期、數值及單位，核對主資料。發現阻擋錯誤時整份退件，並保留來源列、欄與錯誤代碼 |
| 錯誤報告與紀錄查詢 | 查看匯入歷史、下載 Excel 錯誤報告。依批號、站點、機台、檢驗項目與量測時間查詢，支援分頁及超規篩選 |
| 批號追溯與 Q-time | 追查上游原料、下游生產批及分批關係。依進出站紀錄計算兩站間的等待時間 |
| 統計製程管制 | 依檢驗項目與機台顯示個別值及移動全距，標記超出管制界線或連續同側的訊號 |
| MCP 查詢 | AI 客戶端可透過本機連線查批號、等待時間、量測、管制訊號、分組統計與匯入錯誤 |
| 模擬情境（P11） | 透過網頁調整製程、量測和事件，驗證後產生可下載的模擬資料集 |
| 匯入評估 | 使用模擬資料的答案清單驗證檔案判定、錯誤偵測與自動修正 |

技術堆疊為 Python 3.12、FastAPI、SQLAlchemy、Alembic、PostgreSQL 17、openpyxl，以及原生 HTML、CSS 和 JavaScript。測試與品質檢查使用 pytest、Ruff 和 GitHub Actions。

## 快速開始

需要 Docker Compose、Python 3.12 與 [uv](https://docs.astral.sh/uv/)。首次執行時，從專案根目錄以 PowerShell 初始化本機設定和模擬主資料：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv sync --locked
docker compose up -d --wait db
uv run alembic upgrade head
uv run python -m inspection_trace.seed --yes
docker compose up -d --build app
```

使用 Bash 時，可用 `test -f .env || cp .env.example .env` 建立設定，其餘指令相同。`seed` 支援 `--check` 唯讀預覽，並於有受保護的主資料差異時拒絕整批套用。

- [工作頁](http://127.0.0.1:8000/)可上傳報表與查詢資料。
- [API 文件](http://127.0.0.1:8000/docs)列出匯入、查詢、批號追溯和 Q-time 端點。
- [健康檢查](http://127.0.0.1:8000/health)驗證應用程式能連到資料庫。

日常使用時，`docker compose stop` 停止服務並保留資料庫資料，`docker compose up -d` 重新啟動。如果資料庫的主機連接埠被占用，在 `.env` 同步更新 `POSTGRES_PORT` 與 `DATABASE_URL`；工作頁的主機連接埠由 `APP_PORT` 控制。

## 試跑完整匯入

1. 上傳 [C40 有效樣本](samples/files/MES_OLD_C40_1151103.csv)，確認入庫 **6 筆**。
2. 上傳 [C40 異常樣本](samples/files/MES_OLD_C40_1151102.csv)，確認入庫 **0 筆**。下載 Excel 錯誤報告，核對第 **41** 列的量測值空白和第 **78** 列的非數字。
3. 再上傳有效樣本。歷史會顯示「重複上傳」及原始匯入連結，檢驗紀錄筆數維持不變。

程式介接時，`POST /imports?file_name=...` 每次接收一個 `application/octet-stream` 檔案，單檔上限為 5 MiB。

## 試跑自訂匯入範本（P9）

從同一批模擬資料產生一份不同表頭的報表：

```powershell
uv run python -m generator.vendor_report --out out/vendor-report.csv
```

在「匯入範本」選取報表，填入表頭第 **2** 列，再按「預覽表頭」。核對欄位、將日期選成「日/月/年 時:分:秒」，填名稱並儲存。核准後，回到「上傳匯入」選擇該範本及同一份 CSV。只要既有量測仍未入庫，就會新增 6 筆；已存在的相同量測可重用原紀錄。

若要啟用「本機 AI 建議」，在主機直接啟動 API 前設定 `INSPECTION_TEMPLATE_MODEL_URL`（例如 `http://127.0.0.1:8080/v1`）與 `INSPECTION_TEMPLATE_MODEL`。系統只送出表頭文字，建議仍需逐欄核對與核准。核准資料保留版本和核准者，以單機使用者輸入記名。

## 試跑固定版面 PDF（P10）

依「快速開始」同步依賴、完成資料庫遷移並載入範例主資料。如果沿用先前的 Docker 映像，執行 `docker compose up -d --build`。再產生一份模擬化驗報告：

```powershell
uv run --locked python -m generator.lab_report --out out/lab-report.pdf
```

開啟 [本機匯入頁](http://127.0.0.1:8000/ui/?tab=upload)，保留「自動辨識」後上傳。內附範例含 6 筆量測。這些量測與 C40 有效樣本相同，已入庫的相同值會重用既有紀錄。重新上傳相同 PDF 時，匯入歷史會連回第一次處理。

固定格式必須有可擷取的文字，每頁有 v1 標題、欄位與頁尾。錯誤報告使用 PDF 擷取後的連續文字列號。掃描件、加密檔和其他版面超出此範圍。

## 試跑批號追溯

在「批號追溯」查 `L261102A001`，往上游可找到 `ABF-3619` 和 `CORE-5456`。從 `ABF-3619` 往下游可查到六個使用它的生產批。`L261102A001` 從 B20 雷射鑽孔出站至 B60 電鍍銅進站的等待約 **3.73 小時**，上限為 **8 小時**。期間查詢可使用台北時間 **2026-11-02 00:00 至 2026-11-05 00:00**。

## 試跑製程管制

上傳 [C40 有效樣本](samples/files/MES_OLD_C40_1151103.csv)後，開啟「製程管制」，輸入 `CORE_LW`、`LIN-01`，將基準點數設成 **5**。圖上會有 6 筆量測，前 5 筆用來算管制界線，第 6 筆用來監測。

I-MR 管制圖由兩張圖組成。I 圖看每筆量測值，MR 圖看相鄰兩筆相差多少。I 圖標示超出 3 個標準差及連續 8 點在中心線同側的訊號，MR 圖標示相鄰變動超出管制上限的訊號。

可獨立重跑模擬情境。腳本用產生器的乾淨量測與事件答案，以機台、項目、影響批號及偏移方向核對訊號：

```powershell
uv run python -m scripts.evaluate_spc
```

固定 seed 42、中型資料及前 20 點基準下，雷射孔徑漂移 **3/3 段**、ABF 介電層厚度突變 **1/1 段**觸發預期方向的 I 圖訊號。命中表示受影響批號內至少一點觸發訊號，評估範圍限於這兩組序列。

## 匯入偵測率

```powershell
uv run python scripts/evaluate_detection.py
```

評估會在 PostgreSQL 的獨立 schema（資料表命名空間）重跑實際匯入，完成後回滾。可用 `--samples` 指向其他產生器輸出。內附小樣本（seed 42）共 **21 份檔案**，11 份入庫、8 份退件和 2 份重傳皆符合答案。

| 錯誤代碼 | 預期 | 命中 | 漏抓 | 誤報 |
| --- | ---: | ---: | ---: | ---: |
| `E_DUPLICATE_IN_FILE` | 2 | 2 | 0 | 0 |
| `E_LOT_FORMAT` | 1 | 1 | 0 | 0 |
| `E_NOT_NUMERIC` | 1 | 1 | 0 | 0 |
| `E_OUT_OF_PHYSICAL_RANGE` | 3 | 3 | 0 | 0 |
| `E_REQUIRED_MISSING` | 3 | 3 | 0 | 0 |
| `E_UNIT_UNKNOWN` | 1 | 1 | 0 | 0 |
| **合計** | **11** | **11** | **0** | **0** |

命中須同時符合答案的列、欄與錯誤代碼。這批樣本的召回率為 **100%**，預期可入庫的檔案有 **11/11 項**自動修正通過驗證。

## 開發與測試

先啟動 PostgreSQL 並完成遷移，再執行：

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run alembic check
```

只跑無資料庫測試時，使用 `uv run pytest -m 'not integration'`。若要在主機直接開發 API，可先停止 app 容器，再執行 `uv run uvicorn inspection_trace.main:app --host 127.0.0.1 --port 8000`。

GitHub Actions 會額外建置 Docker 應用映像，用該映像初始化臨時資料庫並啟動服務，確認健康檢查通過、C40 樣本入庫 6 筆且零錯誤。

## 連接本機 MCP

先依快速開始啟動 PostgreSQL、套用遷移並載入模擬主資料。將下面的命令和參數填入 MCP 客戶端的本機伺服器設定。客戶端會啟動此程序，並透過標準輸入輸出（stdio）交換查詢。

```text
command: uv
args: --directory <專案絕對路徑> run --locked python -m inspection_trace.mcp_server
```

可先呼叫 `trace_lot`，傳入 `lot_no=L261102A001`、`direction=up`，確認回傳原料批及追溯層數。使用 P6 的匯入樣本後，可再呼叫 `query_records` 或 `spc_status` 核對量測 ID 和訊號。工具、回傳限制與稽核規則見 [系統設計](docs/DESIGN.md#8-mcp-伺服器)。

## 異常調查評估

先啟動資料庫、執行 `uv run alembic upgrade head` 並載入與答案檔同一批的模擬主資料和量測。下面第一個指令執行 Q-time 規則基線，第二個指令使用本機支援工具呼叫的模型。請將 API 位址與模型名稱換成你實際運行的設定。

```powershell
uv run python -m scripts.evaluate_investigation --output out/investigation-baseline.json
uv run python -m scripts.evaluate_investigation --base-url http://127.0.0.1:8080/v1 --model MODEL_NAME --output out/investigation-model.json
```

評估時模型只收到案例批號、調查範本與它實際查到的資料，並且每案例最多呼叫工具 8 次。報告分開列出根因命中、具可核對證據的命中、無效引用、格式錯誤及平均工具呼叫數。

下表是調整證據評分規則前的本機對照。使用中型樣本（seed 42），在獨立的 PostgreSQL 匯入 78 份模擬報表，從漂移、原料突變、槽液老化及 Q-time 超時各取一案。Qwen3-8B 使用 llama.cpp 的工具呼叫模式，兩組皆使用相同的批號及事件答案。

| 方法 | 根因與對象命中 | 具證據命中 | 平均工具呼叫 | 無充分證據的結論 | 格式錯誤 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Q-time 規則基線 | 1/4 | 1/4 | 1.00 | 0 | 0 |
| Qwen3-8B Q4_K_M | 0/4 | 0/4 | 2.75 | 3 | 1 |

規則基線命中 Q-time 超時。Qwen3-8B 在原料突變案例辨認出事件類型，但把生產批號填成原料批號。四案各只代表一種情境，這組分數用來揭露輸出與引用問題。可用 `--events` 指向另一份事件答案檔，並在資料庫載入相對應的模擬報表。

若使用遠端 HTTPS 模型，需明確帶上 `--allow-remote-model`，並將憑證放入 `INSPECTION_MODEL_API_KEY` 環境變數。評估僅使用模擬資料。 [工具呼叫格式](https://platform.openai.com/docs/guides/function-calling)

## 建立自訂模擬情境（P11）

1. 執行 `docker compose up -d --build`，在工作頁選「模擬情境」。
2. 開啟「ABF 載板製程」並點「複製為自訂情境」。改預設名稱、機台數量或每片量測點數。站點可新增、移除及調整順序。
3. 選規模並按「預覽與驗證」。畫面會顯示阻擋錯誤、警告、原始批數、預估量測筆數及報表格式。
4. 按「儲存預設」，重新預覽後勾選確認。按「產生資料集」並下載 ZIP。

ZIP 包含 `truth/`、`files/`、`manifest.csv`、`summary.json`、情境快照和答案清單。相同設定、規模與亂數種子可重現量測和事件，ZIP 及 XLSX 的檔案時間可能不同。每次產生使用獨立目錄。

可匯出 JSON 設定檔，或把 JSON、現有情境 TOML 和選填的髒資料 TOML 載入為草稿。移除自訂預設會保留已產生的資料集及封存的設定。「載入內建 ABF」可回到原始預設。

Docker 使用 `scenarios` 資料卷，直接執行 API 時預設使用 `out/scenarios/`，也可設定 `INSPECTION_SCENARIO_ROOT`。產生只寫入情境儲存區。若要用新資料驗證匯入、批號追溯與管制圖，可用 `uv run python -m inspection_trace.seed --truth-dir <解壓後的 truth 目錄> --check` 預覽，再執行同一指令去掉 `--check` 並確認，最後上傳 `files/` 的報表。不同情境若重用相同主資料代碼，請使用各自的驗收資料庫。

每個站點可選一種既有報表來源。機台 CSV 支援計數項目，其餘三種支援連續量測。連續量測的點數由情境統一指定，計數項目每片一筆。異常沿用原有六種事件，無檢驗項目的站點仍會產生進出站紀錄。 [情境資料與限制](docs/DESIGN.md#12-模擬情境管理p11)

## 文件與範圍

- [系統設計與資料模型](docs/DESIGN.md)
- [架構決策紀錄](docs/DECISIONS.md)
- [交付範圍與後續規劃](docs/PLAN.md)
- [模擬資料產生方式](generator/README.md)
- [產生器設計](docs/GENERATOR_DESIGN.md)

[samples/files](samples/files/) 存放可上傳的示範檔及答案清單。[samples/truth](samples/truth/) 提供初始化的模擬主資料與量測答案。AI 調查、範本生成和 PDF 擷取列於後續規劃。
