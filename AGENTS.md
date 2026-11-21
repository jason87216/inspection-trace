# Inspection Trace 維護規範

Inspection Trace 是使用模擬資料的製程檢驗紀錄與批號追溯系統。實作範圍與後續規劃見 [PLAN.md](docs/PLAN.md)，架構與資料模型見 [DESIGN.md](docs/DESIGN.md)。

## 開發環境

- 使用 Python 3.12、uv、FastAPI、SQLAlchemy 2.x、Alembic 和 PostgreSQL 17。依賴變更需經確認，並同步維護 `uv.lock`。
- 依 [README](README.md) 建立本機環境、執行遷移、載入模擬主資料及啟動服務。
- 函式使用型別註記，程式碼、識別字與 Git 提交訊息使用英文。

## 實作品質

- 匯入驗證與正規化維持純函式，檔案和資料庫存取由對應的讀檔或寫入層處理。
- 匯入錯誤使用 `ingestion/errors.py` 的穩定代碼。測試應比對代碼與原始列欄。
- 資料表變更透過 Alembic 遷移。變更既有 `CHECK` 約束時，應檢查遷移內容。
- 修改資料產生器時，保留同一份設定與 seed 的可重現性，並同步確認範例與答案清單。

## 資料與版本控制

- 專案只使用產生器產出的模擬資料，`samples/` 保留小型示範檔。
- 將憑證、`.env`、本機資料庫、執行記錄與大型生成檔留在本機。共用設定使用可攜的專案相對路徑。
- 修改前確認 Git 狀態，暫存時指定變更的檔案。提交與推送須有明確授權。

## 驗證

- 執行 `uv run pytest`、`uv run ruff check .` 和 `uv run ruff format --check .`。
- 資料表異動時執行 `uv run alembic upgrade head` 及 `uv run alembic check`。
- `tests/unit/` 無資料庫依賴，`tests/integration/` 使用 PostgreSQL，`tests/generator/` 驗證模擬資料與答案清單。
- 回報實際結果及未驗證的範圍。影響資料一致性或 API 契約的決策應記錄在 [DECISIONS.md](docs/DECISIONS.md)。
