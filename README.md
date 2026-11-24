# Inspection Trace

把散落在各站的製程檢驗報表，整理成可查詢、可追溯、可驗證的量測紀錄。

工廠的檢驗資料常常來自舊系統 CSV、機台匯出檔、站點 Excel 日報和人工表單，格式各不相同，也常有缺值、錯字和單位不一致。出問題時，要追查是哪個原料批、哪台機台或哪段等待時間造成的，往往得靠人工翻檔案。

Inspection Trace 用一套固定規則匯入這些報表，錯誤會標出來源列、欄與原因，入庫後可依批號往上下游追溯，並用管制圖找出製程異常。專案使用自行設計的 ABF 載板製程模擬資料，每一個刻意埋入的錯誤和製程事件都有答案可以核對。

**[線上展示](https://ipas-ai-umber.vercel.app/projects/inspection-trace)**：不需安裝，可以篩選匯入結果、查看錯誤報告、點選管制圖上的量測點，並比較模擬情境設定修改後的驗證結果。頁面內容由本專案的[匯出腳本](docs/TRY.md#匯出展示資料)實際執行系統後產生，是靜態展示，不能上傳檔案。

<!-- 截圖待補：上傳退件後的錯誤報告，以及批號追溯畫面 -->

## 重點

- **髒資料進不來，也不會默默被改掉。** 能確定意思的格式差異會自動修正，例如日期格式、單位、全形數字和多餘空白。缺值、非數字、不存在的機台等錯誤會讓整份檔案退件，錯誤報告指出是哪一列、哪一欄。同一份檔案重傳不會重複入庫。
- **從一筆量測查到原因。** 可依批號往上游查原料批、往下游查使用同一原料的生產批，並計算兩站之間的等待時間（Q-time）是否超過上限。I-MR 管制圖可以找出漂移和突變。
- **結果有答案可以對。** 模擬資料產生器會為每一份檔案和每一個製程事件留下答案清單，匯入偵測率和管制圖命中率都可以重跑驗證。設計上的取捨記錄在 [架構決策紀錄](docs/DECISIONS.md)。

## 驗證結果

以下結果都使用固定 seed 42 的模擬資料，可依 [試跑指南](docs/TRY.md) 重跑。

| 項目 | 結果 |
| --- | --- |
| 檔案判定（內附 21 份樣本） | 11 份入庫、8 份退件、2 份重傳，**全部符合答案** |
| 阻擋錯誤偵測 | 11 個埋入錯誤**全部抓到**，誤報 **0**（列、欄與錯誤代碼都須相符） |
| 自動修正 | 預期入庫的 11 份檔案，修正後的值**全部通過**答案核對 |
| 管制圖（中型資料） | 雷射孔徑漂移 **3/3 段**、介電層厚度突變 **1/1 段**觸發預期方向的訊號 |
| 持續整合 | GitHub Actions 執行測試與程式檢查，並用建置好的 Docker 映像實際匯入樣本 |

錯誤代碼的明細見下方[匯入偵測率](#匯入偵測率)。

## 功能

| 功能 | 說明 |
| --- | --- |
| 檢驗報表匯入 | 支援 Big5 舊系統 CSV、UTF-8 機台 CSV、Excel 站點寬表、Excel 人工表單與固定版面化驗 PDF |
| 驗證與錯誤報告 | 正規化日期、數值和單位，核對主資料。退件時可下載 Excel 錯誤報告 |
| 紀錄查詢 | 依批號、站點、機台、檢驗項目與量測時間查詢，支援分頁及超規篩選 |
| 批號追溯與 Q-time | 追查上游原料、下游生產批及分批關係，計算兩站間的等待時間 |
| 統計製程管制 | 依檢驗項目與機台畫 I-MR 管制圖，標記超出管制界線或連續同側的訊號 |
| 匯入範本 | 遇到新格式時預覽表頭、對應欄位，經人工核准後套用；可選用本機模型建議欄位 |
| MCP 查詢 | AI 客戶端可透過本機連線，以唯讀方式查詢批號、等待時間、量測、管制訊號與匯入錯誤 |
| 模擬情境 | 透過網頁調整製程、量測和事件，產生可下載的模擬資料集。產生前先預覽驗證，設定互相矛盾或資料量過大時會擋下 |

## 架構

```text
網頁工作頁 ──> REST API ──┐
                          ├──> 服務層（查詢、參數限制） ──> PostgreSQL
AI 客戶端 ──> MCP (stdio) ┘

報表檔案 ──> 讀檔 ──> 表頭對應 ──> 逐列驗證 ──> 正規化 ──> 主資料核對 ──> 單一交易寫入
```

網頁與 MCP 共用同一個服務層，限制和查詢邏輯只寫一次。MCP 查詢使用唯讀交易，每次查詢限時 5 秒，並記錄稽核。

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

啟動後，建議先照 [試跑指南](docs/TRY.md) 走一次：

- [匯入、退件與重傳](docs/TRY.md#試跑完整匯入)
- [批號追溯與等待時間](docs/TRY.md#試跑批號追溯)
- [製程管制圖](docs/TRY.md#試跑製程管制)
- [自訂匯入範本](docs/TRY.md#試跑自訂匯入範本p9)與[固定版面 PDF](docs/TRY.md#試跑固定版面-pdfp10)
- [連接本機 MCP](docs/TRY.md#連接本機-mcp)
- [建立自訂模擬情境](docs/TRY.md#建立自訂模擬情境p11)

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

命中須同時符合答案的列、欄與錯誤代碼。這批樣本的召回率為 **100%**，預期可入庫的檔案有 **11/11 項**自動修正通過驗證。樣本數小，這個結果代表規則對已知錯誤類型有效，不代表能涵蓋實際產線的所有情況。

## 已知限制

- **系統不內建 AI 自動判斷。** MCP 提供唯讀查詢與調查範本，由外部 AI 客戶端使用，結論需由人依證據 ID 核對。本機 8B 模型初步測試時無法穩定完成多步驟的工具呼叫，因此移除了調查評估程式。
- **作業員名單**目前從模擬資料檔讀取，尚未納入資料庫主資料。
- **PDF** 只支援有文字層的固定版面，不支援掃描件和加密檔。
- **MCP** 只提供本機 stdio 連線，遠端連線與身分驗證列為後續規劃。
- **歷史量測更正**尚未支援。同一筆量測出現不同數值時，整份檔案退件並保留原值。
- 系統為單機使用，範本核准者以輸入的名稱記錄，沒有登入機制。

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

## 文件

- [試跑指南](docs/TRY.md)
- [報表退件處理說明](docs/ERROR_REPORT_GUIDE.md)（給上傳報表的現場人員）
- [系統設計與資料模型](docs/DESIGN.md)
- [架構決策紀錄](docs/DECISIONS.md)
- [交付範圍與後續規劃](docs/PLAN.md)
- [模擬資料產生方式](generator/README.md)
- [產生器設計](docs/GENERATOR_DESIGN.md)

[samples/files](samples/files/) 存放可上傳的示範檔及答案清單。[samples/truth](samples/truth/) 提供初始化的模擬主資料與量測答案。

> 所有資料皆為模擬，不代表任何公司的真實製程或規格。
