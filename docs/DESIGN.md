# Inspection Trace｜製程檢驗紀錄數位化與批號追溯系統

本系統將製程檢驗報表轉為可查詢的量測紀錄，並以批號連結原料、生產批與各站作業時間。展示資料由專案內的產生器建立。

目前本機版本涵蓋 P0～P8。後續功能與交付範圍見 [PLAN.md](PLAN.md)。

---

## 1. 要解決的問題

工廠的檢驗報告與測試紀錄散落在格式不一的 Excel 和 CSV。常見狀況：

- 欄位名稱、單位、日期格式、檔案編碼各自不同
- 人工輸入錯誤、漏填、重複上傳
- 發生品質問題時，很難從一個不良批號回查它經過哪些站點、機台、等了多久、用了哪批原料

系統透過同一條驗證與正規化流程處理四種來源。量測入庫後，可透過工作頁或 REST API 查詢紀錄、追溯批號及檢查站點間等待時間。

## 2. 成功標準

**目前交付（P0～P7）**

1. 上傳一份 Excel／CSV，系統判斷整份檔案能否入庫；不行時產生可下載的「錯誤報告 Excel」，標出列、欄、錯誤代碼與原始值。
2. 同一份檔案重複上傳不會產生重複資料。
3. 可依批號、站點、機台、檢驗項目、時間查詢紀錄，支援分頁。

4. 用產生器埋入的已知錯誤，量測偵測率與誤報數，結果寫進 README。
5. 輸入一個批號，可往前追溯原料批、往後追溯下游批，並顯示各站 Q-time。
6. 管制圖可標出產生器埋入的漂移與突變。
7. 本機 MCP 客戶端可呼叫唯讀查詢，並取得量測與來源證據。

**後續驗收目標（P8～P10）**

- 接上 LLM 產出附證據的調查報告，並以產生器的事件答案量測根因命中率。
- 透過核准流程新增檔案格式範本，將 PDF 擷取結果送入既有驗證流程。

## 3. 技術選擇

| 層 | 選擇 | 理由 |
|---|---|---|
| 語言／環境 | Python 3.12、uv | 以鎖定檔重建依賴版本 |
| Web／API | FastAPI | 管理 HTTP 介面並產出 OpenAPI 文件 |
| 頁面 | HTML、CSS、JavaScript | 由 FastAPI 提供工作頁，前端透過 HTTP 呼叫 API |
| 資料庫 | PostgreSQL 17 | 提供匯入交易、唯一約束與批號遞迴查詢 |
| 資料映射／遷移 | SQLAlchemy 2.x、Alembic | 管理 Python 資料模型及資料庫結構版本 |
| Excel | openpyxl | 讀取 Excel 並產出錯誤報告 |
| 執行 | Docker Compose（app + db） | 以一致的設定啟動應用程式及資料庫 |
| 品質 | pytest、Ruff、GitHub Actions | 執行測試、靜態檢查及格式檢查 |

## 4. 系統架構

```
  瀏覽器（web/static）或 API 用戶端
                    |
                    v
               FastAPI 路由
        ├─ ingestion/   檔案解析、驗證、寫入與錯誤報告
        ├─ api/records  檢驗紀錄查詢與分頁
        ├─ trace/       批號關係與站點等待時間
        └─ spc/         I-MR 管制界線與異常規則
                    |
                    v
        SQLAlchemy + PostgreSQL

  generator/  獨立產生模擬主資料、報表及答案清單
```

工作頁與其他用戶端使用同一組 HTTP 端點。匯入流程由 `ingestion/` 處理，追溯查詢與等待時間計算集中於 `trace/`。
P7 將批號與等待時間、檢驗紀錄、管制圖、匯入歷史與錯誤明細整理到 `services/`。REST 路由與 MCP 工具呼叫同一組服務。

### 4.1 匯入管線（核心）

```
讀檔 reader（Excel / CSV，處理 UTF-8 與 Big5）
  -> 表頭定位（跳過標題列、空白列、合計列）
  -> 欄位對應 mapping（依來源使用對應規則）
  -> 逐列驗證 validation（純函式，不碰資料庫）
  -> 正規化 normalization（單位換算後四捨五入、日期含民國年與 Excel 序號並補上 Asia/Taipei、全形、空白）
  -> 參照檢查（站點、檢驗項目、料號須為 active；機台須在量測當下的有效期間內；批號格式）
  -> 寫入 loader（單一資料庫交易）
```

**設計原則：**

- 驗證與正規化寫成**純函式**：輸入一列原始資料，輸出「乾淨資料」或「錯誤清單」。
- **全有或全無**：一份檔案有任何 B 類錯誤就整份不入庫，回傳錯誤報告。追溯資料寧缺勿錯，也避免修正後重傳造成部分重複。
- **兩層冪等**：每個 `file_sha256` 只有一筆原始匯入，重傳另留一筆歷史並指向原始匯入；`inspection_record` 另外有量測業務鍵唯一約束。
- **錯誤代碼固定**，與產生器一致（見第 6 節）。
- **品質異常保留原量測**。合理範圍內的超規值仍入庫，可在紀錄查詢中篩選。

## 5. 資料模型

各項設計的取捨見 [架構決策紀錄](DECISIONS.md)（P1-1～P1-13）。表中 P7～P9 為規劃項目。

### 5.1 共通規則

- **主鍵**：有業務代碼的表直接用代碼當主鍵（站點、機台、檢驗項目、料號、批號）；沒有代碼的表用流水號 `id`，
  業務鍵另設唯一約束。（P1-2、P1-9）
- **代碼不可變**：代碼由上游系統發出，本系統不提供修改；意義改變就新增新代碼、停用舊的。因此不用 `ON UPDATE CASCADE`。（P1-2）
- **停用**：機台用有效期間 `[valid_from, valid_to)`，量測當下必須有效；站點、檢驗項目、料號用 `active`。
  停用只影響新匯入的資料。（P1-7、P1-8）
- **時間**：所有時間欄位都是 `timestamptz`。匯入時本地時間補 `Asia/Taipei`；依日期、班別分組用
  `AT TIME ZONE 'Asia/Taipei'`。系統時間用 `DEFAULT now()`。（P1-3、P1-6）
- **數值**：量測值與規格用 `double precision`；規格判斷前、單位換算後四捨五入到固定小數位數。（P1-10）
- **單位**：紀錄只存換算成項目標準單位後的值，單位一律看 `inspection_item.unit`。（P1-11）
- **NULL**：欄位預設 `NOT NULL`；允許 NULL 的欄位在下表註明它代表什麼。
- **約束命名**：`db.py` 的命名規則自動產生 `pk_`、`uq_`、`fk_`、`ix_` 名稱；CHECK 要給短名，產生 `ck_表名_短名`。
  Alembic autogenerate 不比對 CHECK，修改 CHECK 要手寫遷移。

### 5.2 資料表

| 資料表 | 階段 | 主鍵 | 主要欄位 | 說明 |
|---|---|---|---|---|
| `station` | P1 | `code` | name, seq, active | 製程站點 |
| `machine` | P1 | `code` | station_code → station, valid_from, valid_to（NULL＝仍在使用） | 機台；有效期間為半開區間 |
| `inspection_item` | P1 | `code` | name, station_code → station, kind（continuous／count）, unit, lsl, target, usl, physical_min, active | 檢驗項目與規格；lsl、target 為 NULL＝沒有下限／目標（例如缺陷數、翹曲） |
| `part` | P1 | `part_no` | name, active | 料號 |
| `lot` | P1 | `lot_no` | lot_type（production／material）, part_no → part, panel_count, released_at, kind（ABF／CORE） | 生產批與原料批同一張表。part_no、panel_count、released_at 只有生產批有、kind 只有原料批有，NULL＝不適用，由 CHECK 依 lot_type 保證必填 |
| `lot_link` | P1 | `id` | parent_lot_no → lot, child_lot_no → lot, relation（consume／split）；唯一（parent_lot_no, child_lot_no） | 批號關係。「consume 的上游是原料批」是跨列規則，CHECK 管不到，由 seed／loader 保證 |
| `lot_move` | P1 | `id` | lot_no → lot, station_code → station, machine_code → machine, start_at, end_at（NULL＝尚未出站）, operator；唯一（lot_no, station_code） | 進出站紀錄；Q-time 由此計算。遇到重工（同批同站兩次）再調整唯一鍵 |
| `import_batch` | P2 | `id` | file_name, file_sha256, source_type, template_id, status, total_rows, error_rows, created_at, duplicate_of_id, station_code, warnings | 每次上傳一筆；條件唯一索引限制每個指紋只有一筆原始匯入 |
| `import_error` | P2 | `id` | import_batch_id, row_no, column, code, message, raw_value | 錯誤明細；raw_value 保存原始值供錯誤報告 |
| `inspection_record` | P2 | `id`（bigint） | lot_no, station_code, machine_code, item_code, panel, point（NULL＝計數型沒有量測點）, value, measured_at, operator, import_batch_id, source_row_no | 每個量測值一列；業務鍵唯一 `UNIQUE NULLS NOT DISTINCT (lot_no, station_code, item_code, panel, point)`，資料層冪等；不存 unit |
| `qtime_rule` | P5 | `id` | from_station_code, to_station_code, limit_hours | Q-time 規則 |
| `audit_log` | P7 | `id` | actor, channel, action, target, detail（JSON）, created_at | 操作紀錄；channel：web／api／mcp |
| `llm_call_log` | P8 | `id` | feature, provider, model, input_digest, output（JSON）, latency_ms, created_at | 每次 LLM 呼叫的紀錄 |
| `import_template` | P9 | `id` | name, source_type, header_row, column_map（JSON）, unit_rules（JSON）, date_format, status, approved_by, approved_at | 檔案格式範本；status：draft／approved |

- `inspection_record` 保留 `import_batch_id` 與 `source_row_no`，任何一筆資料都能回查是哪份檔案的第幾列；
  系統不保存原始檔案，原文以 `file_sha256` 確認使用者手上的檔案是同一份。
- 量測以業務鍵比對。新檔已有相同量測值的列可以略過，有不同量測值時整檔退件。`id` 用於識別某一筆紀錄（SPC 標記、MCP 證據 ID）。
- C40 和站點日報讀取報表上的操作員。機台 CSV 與人工表單的量測列沒有操作員欄，依該批號、站點的進出站紀錄取得操作員，再核對模擬名單。人工表單的填表人是表單層欄位。

### 5.3 主資料 seed

- 從產生器的 `samples/truth/` 灌入站點、機台、檢驗項目、料號、原料批（`materials.csv`）、生產批（`lots.csv`）、
  批號關係、進出站紀錄。`lot_move` 在 MVP 階段由 `truth/lot_moves.csv` 匯入（模擬 MES 的進出站紀錄）。
- **`records.csv` 不灌**：它是檢查匯入結果的標準答案，檢驗紀錄要透過匯入 `files/` 進來。
- 流程：逐表比對 CSV 與資料庫、列出差異報告 → 互動詢問確認 → 在單一交易內套用。
  新增照做；顯示用欄位（名稱、排序）可更新；意義改變（規格、單位、類型、所屬站點或料號）拒絕整個 seed；
  資料庫有但 CSV 沒有只提醒、不刪除；不碰 `active`、`valid_to`。
- 非互動環境預設不套用；`--check` 只顯示報告，`--yes` 自動確認。CI 用來驗證冪等：套用後 `--check` 必須沒有差異。
- 補上 CSV 沒有的值：機台 `valid_from` 預設為 1970-01-01 UTC，另依來源設定 `lot_type` 與時間的時區。

### 5.4 測試資料庫

本機測試與開發共用同一個資料庫，每個測試包在外層交易內、結束時 rollback；Session 使用
`join_transaction_mode="create_savepoint"`，被測程式的 `commit()` 只結束 savepoint。測試不斷言總筆數、
自己建立需要的主資料。CI 每次使用全新資料庫。（P1-13）

## 6. 模擬資料與錯誤代碼

產生器已完成，詳見 `generator/README.md` 與 `docs/GENERATOR_DESIGN.md`。重點：

- 四種來源：舊系統 Big5 CSV、機台匯出 CSV、站點日報寬表、人工抄錄表
- 錯誤分三類：**A 應自動修正**、**B 應拒絕**、**C 驗證層抓不到**（刻意設計）
- 每份檔案附答案清單；製程事件（漂移、換膜批、藥水老化、Q-time 超時、偶發超規）附事件答案

**錯誤代碼（匯入系統與產生器共用）：**

| 代碼 | 說明 | 產生器是否會產生 |
|---|---|---|
| `E_REQUIRED_MISSING` | 必填空白 | 會 |
| `E_NOT_NUMERIC` | 非數值 | 會 |
| `E_UNIT_UNKNOWN` | 單位無法辨識 | 會 |
| `E_LOT_FORMAT` | 批號格式錯誤（`^L\d{6}[A-Z]\d{3}(-S\d)?$`） | 會 |
| `E_REF_NOT_FOUND` | 參照不存在（機台、站點、項目） | 會 |
| `E_OUT_OF_PHYSICAL_RANGE` | 物理上不可能（負值） | 會 |
| `E_DUPLICATE_IN_FILE` | 同檔內重複列 | 會 |
| `E_HEADER_UNKNOWN` | 必要欄位無法對應 | 尚未（之後的 `F_WRONG_TEMPLATE`） |
| `E_DATE_INVALID` | 日期無法解析 | 尚未 |

**評估腳本** `scripts/evaluate_detection.py`：讀取全部檔案與答案清單，輸出每種錯誤的召回率、誤報數，以及 ACCEPT／REJECT／DUPLICATE_FILE 判定的正確率。

## 7. REST API

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/health` | 健康檢查（含資料庫連線） |
| POST | `/imports` | 上傳檔案，回傳匯入結果 |
| GET | `/imports` | 匯入歷史（分頁） |
| GET | `/imports/{id}` | 單次匯入結果 |
| GET | `/imports/{id}/error-report` | 下載錯誤報告 Excel |
| GET | `/records` | 查詢檢驗紀錄（條件必填、分頁） |
| GET | `/lots/{lot_no}` | 批號基本資料與各站進出紀錄 |
| GET | `/lots/{lot_no}/trace?direction=up\|down&max_depth=10` | 批號追溯（遞迴 CTE），查詢深度 1 至 10 層 |
| GET | `/qtime?from=&to=&over_only=false` | 期間內 Q-time 狀況與超時批，時間需含時區，最長 31 天 |
| GET | `/spc/{item_code}?machine=&from=&to=&baseline_points=20` | 單一機台的連續量測管制圖與異常點。機台必填、時間可選且需含時區，基準點數 5 至 200，每次最多 1,000 點 |

批號詳情保留每筆進出站的原始批號。分批若沿用上游已發生的等待時間，Q-time 會標出原始批號。期間查詢以實際下游進站時間篩選，尚未進下游時以可用的上游出站或進站時間篩選。同一次等待只在原始批的期間結果中計算。

管制圖依量測時間、批號、片號及量測點排序。回應包含量測 ID、批號、個別值、移動全距和訊號。當量測不足以建立基準時，回傳量測序列，管制界線為空值。

## 8. MCP 伺服器

### 8.1 角色

讓任何支援 MCP 的 LLM 用戶端，以**固定的格式**呼叫系統的查詢能力。系統不綁定任何模型或廠商。

**分工：MCP 鎖住「怎麼呼叫」，服務層決定「能不能呼叫、能拿到什麼」。**

| 規則 | 放在哪裡 |
|---|---|
| 參數型別、數值上下限、格式、可選值 | 寫進工具的 schema（讓 LLM 一開始就知道），伺服器端再驗證一次 |
| 跨欄位規則（例如查詢期間 ≤ 31 天）、白名單（項目、機台是否存在） | 服務層 |
| 執行範圍 | 本機客戶端以子程序啟動 MCP，查詢使用 PostgreSQL 唯讀交易 |
| 稽核資料 | 獨立交易保存本機通道、操作、對象和結果狀態 |
| 回傳大小 | 服務層：筆數上限、分頁、只回摘要 |
| 稽核 | 進入查詢服務的工具及資源呼叫寫入 `audit_log`（channel = mcp） |

### 8.2 工具（全部唯讀）

| 工具 | 用途 | 限制 |
|---|---|---|
| `trace_lot` | 批號往上或往下追溯 | 深度 1–10，最多回傳 100 條關係 |
| `get_lot_history` | 某批各站機台、時間、作業員、Q-time | 單一批號，各類最多回傳 100 筆 |
| `check_qtime` | 期間內 Q-time 超時批 | 期間 ≤ 31 天，最多回傳 100 筆 |
| `query_records` | 依條件查詢檢驗紀錄 | 至少一個條件，每頁最多 100 筆 |
| `spc_status` | 某項目／機台的管制狀態與違規點 | 回傳摘要，不回傳全部原始點 |
| `compare_groups` | 依機台、原料批、台北班別比較平均值與量測超規率 | 單一檢驗項目，期間 ≤ 31 天，最多 100 組 |
| `get_import_errors` | 某次匯入的錯誤明細 | 筆數上限 |

**回傳格式統一**：每個結果都附「證據 ID」（批號、紀錄 ID、來源檔案與列號、匯入批次），
讓 LLM 的結論可以被人回頭核對。

原料分組沿分批關係查祖批，再找投料關係。同一量測可能對應多種原料批，各組的筆數可重疊。超規率以該組的量測筆數為分母。

### 8.3 資源與提示範本

- **Resources**：站點清單、檢驗項目與規格、Q-time 規則。讓模型不必反覆呼叫工具查基本資料。
- **Prompts**：`investigate_anomaly` 範本，固定調查流程：
  1. 確認管制狀態與異常範圍（`spc_status`）
  2. 追溯受影響批號（`trace_lot`、`get_lot_history`）
  3. 檢查 Q-time（`check_qtime`）
  4. 比較機台、原料批、班別（`compare_groups`）
  5. 寫出報告：每個結論都附證據 ID；證據不足時明確寫「無法判斷」

### 8.4 傳輸

- 本機展示使用 stdio，啟動方式見 [README](../README.md#連接本機-mcp)。 [官方 Python SDK](https://py.sdk.modelcontextprotocol.io/)
- 遠端部署列為後續規劃，預計採 Streamable HTTP 與身分驗證。

## 9. 選用 LLM 功能規劃

### 9.1 原則

1. 沒有 LLM，核心系統完整可用。
2. LLM 只處理模糊的部分：理解、擷取、整理、建議。
3. LLM 的輸出**一定要經過驗證管線或人工確認**，不能直接寫入資料或下結論。
4. 支援只用本機模型；不得把製程資料送到未經核准的外部服務。
5. 每次呼叫寫入 `llm_call_log`。

### 9.2 功能

| 功能 | 做法 | 驗證方式 |
|---|---|---|
| **異常調查**（主角） | LLM 用戶端透過 MCP 呼叫工具，依 `investigate_anomaly` 範本產出附證據的調查報告。系統本身不需要內建 Agent | 以 `events.answers.json` 計算**根因命中率**；可比較不同模型（雲端／本機） |
| **新格式範本產生** | 遇到新的檔案格式時，LLM 讀前幾列，提出整份對應範本（表頭列、欄位對應、單位、日期格式），存成 draft；人工核准後才啟用。之後同格式完全由規則處理 | 以產生器的 `D_HEADER_ALIAS` 答案計算欄位對應正確率 |
| **PDF 擷取** | 版面不固定的化驗報告，由 LLM 擷取成表格 | 擷取結果走原本的匯入管線，與人工資料接受相同驗證 |

**設計重點：「讓 LLM 產生規則，再由規則處理資料。」**範本產生只在第一次遇到新格式時呼叫 LLM。

### 9.3 調查評估

- 依事件答案選出 Q-time 超時、漂移、原料突變和槽液老化案例，以批號作為調查起點。
- 根因命中要求事件代碼及關聯對象都相符。具證據命中另外要求引用可解析，且至少一筆查詢結果含符合該類結論的證據。這項評分檢查結構化結論與引用，自由文字因果推理仍需人工覆核。
- 模型每案例最多呼叫 8 次工具。`llm_call_log` 記錄模型名稱、請求摘要雜湊、結果狀態和耗時。評估報告含逐案例證據與分數。
- 設定方式和基線結果見 [README](../README.md#異常調查評估)。

## 10. 專案結構

```
inspection-trace/
├─ README.md
├─ AGENTS.md / CLAUDE.md
├─ pyproject.toml / uv.lock
├─ compose.yaml / Dockerfile
├─ alembic.ini / migrations/
├─ .github/workflows/ci.yml
├─ docs/
│  ├─ DESIGN.md               # 本文件
│  ├─ GENERATOR_DESIGN.md
│  ├─ PLAN.md                 # 交付範圍與後續規劃
│  └─ DECISIONS.md            # 架構決策紀錄
├─ src/inspection_trace/
│  ├─ main.py / config.py / db.py / models.py
│  ├─ ingestion/              # readers、mapping、validation、normalization、errors、loader、report
│  ├─ api/                    # REST 路由
│  ├─ services/               # REST 與 MCP 共用的查詢
│  ├─ mcp_server.py           # 本機唯讀 MCP
│  ├─ trace/                  # 批號追溯與 Q-time
│  ├─ spc/                    # 管制圖計算與異常規則
│  └─ web/static/             # 工作頁
├─ generator/                 # 模擬資料產生器
├─ scripts/
│  ├─ evaluate_detection.py   # 匯入偵測率
│  └─ evaluate_spc.py         # 管制訊號驗證
├─ samples/
└─ tests/
   ├─ unit/                   # 驗證、正規化、對應：不需資料庫
   ├─ integration/            # 匯入、查詢與追溯：需要 PostgreSQL
   └─ generator/
```

## 11. 交付與驗證

各階段的交付內容及驗收入口見 [PLAN.md](PLAN.md)。[README](../README.md) 提供初始化、樣本匯入、批號追溯和匯入偵測率重跑方式。
