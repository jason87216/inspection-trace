# generator｜PCB／載板製程模擬資料與髒資料產生器

模擬一座 ABF 載板廠的生產與檢驗，輸出四種「現場格式」的檔案，刻意弄髒，
並為每一個埋入的錯誤留下答案清單，用來驗證匯入系統。

> 情境參考公開產業資訊；所有規格與數值皆為模擬，不代表任何公司的真實製程。
> 公司名稱使用虛構的 `Demo Substrate Co.`。

## 執行

```bash
uv sync
uv run python -m generator --scale small --seed 42 --out out/small
uv run pytest
```

| 規模 | 生產批 | 量測值 | 檔案 | 參考耗時 |
|---|---|---|---|---|
| small | 8 | 約 1,800 | 約 20 | 1 秒內 |
| medium | 60（另有分批子批） | 約 13,800 | 約 80 | 約 4 秒 |
| large | 600（另有分批子批） | 約 137,000 | 約 675 | 約 30 秒 |

同一個 `--seed` 與設定，輸出內容完全相同（xlsx 比對儲存格內容）。
省略 `--out` 時，輸出到 `out/<scale>`。每次產生需指定新目錄或空目錄，重跑同一設定可另選輸出目錄。

## 輸出

```
out/
├─ truth/                     乾淨的真相（匯入系統不應該讀這裡）
│  ├─ stations.csv / machines.csv / items.csv / parts.csv / materials.csv / operators.csv
│  ├─ lots.csv                批號、料號、片數、ABF 膜批、核心材批、分批來源、Q-time
│  ├─ lot_moves.csv           每批每站的機台、進出站時間、作業員
│  ├─ lot_links.csv           批號關係：consume（原料→生產批）、split（分批）
│  ├─ records.csv             每一個量測值的真值
│  └─ events.answers.json     製程事件的正確答案（給 SPC 與追溯驗證）
├─ files/                     要丟給匯入系統的檔案
│  ├─ <檔名>.xlsx / .csv
│  └─ <檔名>.answers.json     這份檔案的答案清單
├─ manifest.csv               每份檔案的來源、是否弄髒、預期結果、各類錯誤數
└─ summary.json               本次產生的統計
```

## 四種來源

| 來源 | 站點 | 格式 | 特徵（乾淨檔案就有，不算錯誤） |
|---|---|---|---|
| `SRC_LEGACY` | C40 內層線路 | CSV，**Big5（cp950）** | 縮寫表頭 `LOTNO, PNL, PT, EQP, ITEM, VAL, UOM, DT, TM, OP`；`OP` 為原始量測的操作員代碼；日期為民國年 `1151120`，時間 `140512`；單位寫 `UM` |
| `SRC_MACHINE` | C50、B80 AOI；F40 電測 | CSV，UTF-8 | 英文表頭；時間為 Unix 秒數（以 UTC+8 換算）；計數型資料 |
| `SRC_STATION` | B10、B20、B40、B60、B70 | xlsx 寬表 | 一列＝批號＋片號＋項目，量測點 `P1..P5` 橫向排開；檔案依班別分（夜班 20:00～08:00 歸屬開始那天） |
| `SRC_MANUAL` | F10、F20、F50 | xlsx | 前三列是標題（A1:I1 合併）、填表人、空白列；最後一列是「合計」 |

乾淨檔案裡的項目名稱：寬表與人工表用中文名稱（例如「雷射孔上孔徑」），
機台 CSV 與舊系統 CSV 用代碼（例如 `VIA_TOP_D`）。對照表在 `truth/items.csv`。

## 答案清單格式

```json
{
  "file": "雷射鑽孔站_日報_20261102_日班.xlsx",
  "source": "SRC_STATION",
  "station": "B20",
  "format": "xlsx",
  "encoding": null,
  "header_row": 1,
  "is_dirty": true,
  "expected_outcome": "ACCEPT",
  "expected_record_count": 60,
  "injections": [
    {
      "op": "D_DATE_FORMAT",
      "category": "A",
      "expected_error_code": null,
      "row": 6,
      "column": "量測時間",
      "column_key": "time",
      "original_value": "2026-11-02T16:42:59",
      "dirty_value": "2026/11/02 16:42",
      "expected_normalized_value": "2026-11-02T16:42:59",
      "tolerance": 60,
      "note": "tolerance in seconds"
    }
  ]
}
```

- `row`：最終檔案中的列號（1 起算，含標題列與表頭）。插入列之後已重新計算。
- `column`：最終檔案的表頭文字（若表頭被換成別名，這裡就是別名）。
- `expected_outcome`：
  - `ACCEPT`：整份應入庫，入庫筆數應等於 `expected_record_count`
  - `REJECT`：有 B 類錯誤，整份不應入庫（全有或全無）
  - `DUPLICATE_FILE`：與 `duplicate_of` 位元組完全相同，應判定為重複上傳
- `tolerance`：數值比對容許誤差；日期為秒數。

## 錯誤類別與操作

**A 應自動修正後入庫**

| 操作 | 說明 | 預期正規化結果 |
|---|---|---|
| `D_HEADER_ALIAS` | 表頭換成別名（`批號`→`Lot`、`VAL`→`DATA`…） | 原表頭 |
| `D_UNIT_MIL` | 單位改成 mil，數值換算（1 mil = 25.4 μm） | 原 μm 值，誤差 0.001 |
| `D_UNIT_ALIAS` | `μm` 寫成 `um`、`µm`（MICRO SIGN）、`UM`、`微米` | `μm` |
| `D_DATE_FORMAT` | `2026/11/20 14:05`、`20261120 1405`、Excel 序號、民國 `115/11/20 14:05`、機台 CSV 改成文字日期 | 原時間，誤差 60 秒 |
| `D_FULLWIDTH` | 數值改全形 `２５．３` | 原數值 |
| `D_WHITESPACE` | 批號前後空白，或中間插入一個空白 | 原批號 |
| `D_LAYOUT_NOISE` | 人工表中間插入空白列、「小計」列 | `ROW_IGNORED` |

**B 應拒絕（整份不入庫）**

| 操作 | 錯誤代碼 |
|---|---|
| `D_REQUIRED_BLANK` | `E_REQUIRED_MISSING` |
| `D_NOT_NUMERIC` | `E_NOT_NUMERIC` |
| `D_UNIT_UNKNOWN` | `E_UNIT_UNKNOWN` |
| `D_LOT_TYPO` | `E_LOT_FORMAT`（批號規則：`^L\d{6}[A-Z]\d{3}(-S\d)?$`） |
| `D_REF_UNKNOWN` | `E_REF_NOT_FOUND`（不存在的機台） |
| `D_PHYSICAL_IMPOSSIBLE` | `E_OUT_OF_PHYSICAL_RANGE`（負的厚度、負的缺陷數） |
| `D_DUP_ROW` | `E_DUPLICATE_IN_FILE`（標在重複出現的那一列） |

**C 驗證層抓不到（刻意設計）**

| 操作 | 說明 |
|---|---|
| `D_TRANSPOSE` | 數字對調，結果仍在規格內 |
| `D_WRONG_MACHINE` | 換成同站另一台存在的機台 |

**檔案層級**：約 5% 的檔案會多一份 `_重傳` 副本（位元組相同）。

**注意：超出規格（USL／LSL）不是匯入錯誤。** 這類值來自製程事件，應正常入庫，交給 SPC。

## 製程事件（truth/events.answers.json）

| 事件 | 內容 |
|---|---|
| `EV_DRIFT` / `EV_PM_RESET` | `LDR-01` 雷射機孔徑每批下降 0.35 μm，每 10 批保養歸零 |
| `EV_MAT_SHIFT` | 第 4 個 ABF 膜批起，介電層厚度 +1.8 μm |
| `EV_BATH_AGING` | 電鍍銅厚度的變異隨藥水使用批數變大，每 12 批換槽 |
| `EV_QTIME_OVER` | 雷射（B20）完成到電鍍（B60）開始超過 8 小時；該批 AOI 與電測缺陷平均值 ×5 |
| `EV_OOS_SPIKE` | 偶發單點超出規格 |

分批：約 8% 的批在防焊（F10）分出 `-S1` 子批（5 片），寫在 `lot_links.csv`。

## 調整

- 情境（站點、項目規格、機台數、事件強度）：`generator/config/scenario.toml`
- 弄髒比例與各來源可用的操作：`generator/config/dirty.toml`

真相和弄髒使用不同的亂數流：調整 `dirty.toml` 不會改變 `truth/`。

## 尚未實作

- 化驗 PDF 來源（配合主系統 P7）
- 檔案截斷、用錯範本（`F_TRUNCATED`、`F_WRONG_TEMPLATE`）
- 單一超大檔案（目前 large 是很多個檔案，單檔最多約 400 列）
