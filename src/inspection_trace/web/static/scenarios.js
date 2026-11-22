const builtinId = "builtin-abf";
const sourceLabels = {
  SRC_LEGACY: "舊系統 CSV（Big5，連續量測）",
  SRC_MACHINE: "機台 CSV（計數）",
  SRC_STATION: "站點日報 Excel（連續量測）",
  SRC_MANUAL: "人工表單 Excel（連續量測）",
};
const eventFields = {
  drift: ["量測漂移", [
    ["machines", "機台代碼（逗號分隔）", "list"], ["items", "檢驗項目代碼", "list"],
    ["rate_per_lot", "每批偏移量", "number"], ["pm_interval_lots", "每幾批保養歸零", "number"],
  ]],
  material_shift: ["原料突變", [
    ["material_kind", "材料分類"], ["item", "檢驗項目代碼"],
    ["from_material_index", "開始材料批索引（0 為第一批）", "number"],
    ["delta", "平均值變化量", "number"],
  ]],
  bath_aging: ["槽液老化", [
    ["items", "檢驗項目代碼（逗號分隔）", "list"],
    ["aging_per_lot", "每批增加的標準差倍率", "number"],
    ["bath_interval_lots", "每幾批換槽", "number"],
  ]],
  qtime_over: ["Q-time 超時", [
    ["from_station", "等待起站（出站）"], ["to_station", "等待迄站（進站）"],
    ["probability", "追加等待機率（0–1）", "number"],
    ["extra_wait_hours", "追加等待（最小、最大小時）", "pair"],
    ["affected_items", "受影響計數項目（逗號分隔）", "list"],
    ["mean_multiplier", "超時後的計數平均倍率", "number"],
  ]],
  oos_spike: ["偶發超規", [
    ["items", "檢驗項目（留白套用所有連續項目）", "list"],
    ["probability_per_value", "每筆發生機率（0–1）", "number"],
    ["sigma", "偏移的標準差倍數", "number"],
  ]],
  split: ["製程分批", [
    ["at_station", "分批站點"], ["probability", "發生機率（0–1）", "number"],
    ["child_panels", "子批片數", "number"],
  ]],
};
const dirtyLabels = {
  D_UNIT_MIL: "英制換算", D_UNIT_ALIAS: "單位別名", D_UNIT_UNKNOWN: "未知單位",
  D_DATE_FORMAT: "日期格式", D_REQUIRED_BLANK: "必填缺值", D_DUP_ROW: "重複列",
  D_PHYSICAL_IMPOSSIBLE: "不合理數值", D_NOT_NUMERIC: "非數值", D_LOT_TYPO: "批號錯字",
  D_REF_UNKNOWN: "不存在的參照", D_WHITESPACE: "空白", D_FULLWIDTH: "全形字元",
  D_TRANSPOSE: "數字對調", D_WRONG_MACHINE: "同站機台誤選",
  D_HEADER_ALIAS: "表頭別名", D_LAYOUT_NOISE: "人工表單空白列或小計",
};

function node(tag, text, klass) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (klass) element.className = klass;
  return element;
}

function button(text, action, disabled = false) {
  const element = node("button", text, "btn");
  element.type = "button";
  element.disabled = disabled;
  element.addEventListener("click", action);
  return element;
}

function details(title, content, open = false, klass = "") {
  const container = node("details", undefined, klass);
  container.open = open;
  container.append(node("summary", title), content);
  return container;
}

function validateMessage(detail) {
  if (!Array.isArray(detail)) return typeof detail === "string" ? detail : "操作失敗，請稍後再試。";
  return detail.map((error) => typeof error === "string" ? error : `${error.loc?.slice(1).join(".")}: ${error.msg}`).join("\n");
}

async function request(path = "", method = "GET", payload) {
  const response = await fetch(`/scenarios${path}`, {
    method,
    ...(payload === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  });
  if (response.status === 204) return null;
  const result = await response.json().catch(() => null);
  if (!response.ok) throw new Error(validateMessage(result?.detail));
  return result;
}

function fields(model, descriptions, changed) {
  const grid = node("div", undefined, "filters");
  for (const [key, text, kind = "text", choices] of descriptions) {
    const label = node("label", text, kind === "bool" ? "check" : "field");
    const control = node(choices ? "select" : "input");
    if (choices) {
      for (const [value, caption] of Object.entries(choices)) {
        const option = node("option", caption);
        option.value = value;
        control.append(option);
      }
    } else {
      control.type = kind === "bool" ? "checkbox" : kind === "number" ? "number"
        : kind === "datetime" ? "datetime-local" : "text";
      if (kind === "number") control.step = "any";
    }
    if (kind === "bool") control.checked = Boolean(model[key]);
    else if (kind === "pair" || kind === "list") control.value = model[key]?.join(", ") ?? "";
    else control.value = model[key] ?? "";
    control.addEventListener(kind === "bool" || choices ? "change" : "input", () => {
      const value = control.value.trim();
      if (kind === "bool") model[key] = control.checked;
      else if (kind === "number") model[key] = value === "" ? null : Number(value);
      else if (kind === "pair") model[key] = value.split(/[,，]/).map((part) => part.trim() ? Number(part) : null);
      else if (kind === "list") model[key] = value.split(/[,，]/).map((part) => part.trim()).filter(Boolean);
      else model[key] = choices && !value ? null : value;
      changed();
    });
    label.append(control);
    grid.append(label);
  }
  return grid;
}

function editableRows(title, list, description, descriptors, factory, changed, reorder = false) {
  const content = node("div", undefined, "stack");
  content.append(node("p", description, "note"));
  const rows = node("div");
  const render = (focus = -1) => {
    rows.replaceChildren(...list.map((row, index) => {
      const body = node("div");
      body.append(fields(row, descriptors, changed));
      const toolbar = node("div", undefined, "toolbar");
      const shift = (delta) => {
        const next = index + delta;
        [list[index], list[next]] = [list[next], list[index]];
        changed();
        render(next);
      };
      if (reorder) toolbar.append(
        button("上移", () => shift(-1), index === 0),
        button("下移", () => shift(1), index === list.length - 1),
      );
      toolbar.append(button("從草稿移除", () => {
        list.splice(index, 1);
        changed();
        render(Math.min(index, list.length - 1));
      }));
      body.append(toolbar);
      const label = row.name || row.code || row.part_no || "新增項目";
      const id = row.code || row.part_no || "";
      const summary = details(`${index + 1}. ${label}${id && id !== label ? `（${id}）` : ""}`, body,
        index === focus, "scenario-row");
      body.addEventListener("change", () => {
        summary.firstElementChild.textContent = `${index + 1}. ${row.name || row.code || row.part_no || "新增項目"}`;
      });
      return summary;
    }));
  };
  render();
  content.append(rows, button(`新增${title}`, () => {
    list.push(factory());
    changed();
    render(list.length - 1);
  }));
  return details(title, content, title === "站點", "card");
}

function configurationEditor(preset, changed) {
  const config = preset.config;
  const basic = node("div", undefined, "stack");
  basic.append(fields(preset, [["name", "預設名稱"]], changed));
  basic.append(fields(config.scenario, [
    ["company", "模擬公司名稱"], ["start", "開始時間（台北時間）", "datetime"],
    ["release_interval_hours", "生產批投產間隔（小時）", "number"],
    ["panels_per_lot", "每批片數", "number"], ["sample_panels", "預設抽樣片數", "number"],
    ["points_per_panel", "每片量測點數", "number"], ["operators", "操作員人數", "number"],
    ["qtime_limit_hours", "Q-time 上限（小時）", "number"],
  ], changed));
  basic.append(fields(config.scale, [
    ["small", "小型批數", "number"], ["medium", "中型批數", "number"], ["large", "大型批數", "number"],
  ], changed));
  const stations = editableRows("站點", config.stations, "表單順序就是製程順序。機台以「前綴-01」起號。",
    [
      ["code", "站點代碼"], ["name", "站點名稱"], ["machine_prefix", "機台代碼前綴"],
      ["machines", "機台數量", "number"], ["queue_hours", "等待時間（平均、標準差）", "pair"],
      ["process_hours", "加工時間（平均、標準差）", "pair"],
      ["source", "報表來源", "text", { "": "無檢驗報表", ...sourceLabels }],
    ], () => ({
      code: "", name: "", machine_prefix: "", machines: 1,
      queue_hours: [0.5, 0.1], process_hours: [1, 0.2], source: null,
    }), changed, true);
  const items = editableRows("檢驗項目", config.items,
    "規格下限、上限用於判定產品是否超規。連續量測可另設模擬平均與抽樣片數；計數項目每片一筆。",
    [
      ["code", "檢驗代碼"], ["name", "檢驗名稱"], ["station", "所屬站點代碼"],
      ["kind", "類型", "text", { continuous: "連續量測", count: "計數" }], ["unit", "單位"],
      ["lsl", "規格下限 LSL（可留白）", "number"], ["target", "規格目標 Target", "number"],
      ["usl", "規格上限 USL", "number"], ["physical_min", "物理下限", "number"],
      ["simulation_mean", "連續量測平均（留白用目標）", "number"],
      ["sd", "連續量測標準差", "number"], ["mean", "計數平均", "number"],
      ["decimals", "連續量測小數位", "number"],
      ["sample_panels", "連續量測抽樣片數（留白用預設）", "number"],
    ], () => ({
      code: "", name: "", station: "", kind: "continuous", unit: "μm", lsl: null, target: 10,
      usl: 15, physical_min: 0, simulation_mean: null, sd: 1, mean: 0, decimals: 2, sample_panels: null,
    }), changed);
  const parts = editableRows("料號", config.parts, "產線字母會成為生產批號的一部分。",
    [["part_no", "料號"], ["name", "料號名稱"], ["line", "產線字母（A–Z）"]],
    () => ({ part_no: "", name: "", line: "A" }), changed);
  const materials = editableRows("材料分類", config.materials.kinds,
    "每個生產批依設定取用各分類的材料。可新增分類，或移除所有材料。",
    [["code", "材料代碼"], ["lots_per_material", "每個原料批可供應的生產批數", "number"]],
    () => ({ code: "", lots_per_material: 6 }), changed);
  const events = node("div", undefined, "stack");
  for (const [name, [title, editable]] of Object.entries(eventFields)) {
    events.append(details(title, fields(config.events[name], [["enabled", "啟用此事件", "bool"], ...editable], changed)));
  }
  const dirty = node("div", undefined, "stack");
  dirty.append(fields(preset.dirty.files, [
    ["clean_ratio", "完全乾淨的檔案比例（0–1）", "number"],
    ["duplicate_ratio", "額外重傳檔案的機率（0–1）", "number"],
  ], changed));
  dirty.append(fields(preset.dirty.row_rates, Object.keys(sourceLabels).map((key) =>
    [key, `${sourceLabels[key]}的錯誤列機率（0–1）`, "number"]), changed));
  for (const [source, weights] of Object.entries(preset.dirty.ops)) {
    dirty.append(details(`${sourceLabels[source]}的錯誤權重`,
      fields(weights, Object.keys(weights).map((key) => [key, dirtyLabels[key] ?? key, "number"]), changed)));
  }
  dirty.append(details("檔案層級錯誤機率", fields(preset.dirty.file_ops,
    Object.keys(preset.dirty.file_ops).map((key) => [key, dirtyLabels[key] ?? key, "number"]), changed)));
  return [
    details("基本設定與規模", basic, true, "card"), stations, items, parts, materials,
    details("製程異常與分批", events, false, "card"),
    details("髒資料與重傳比例", dirty, false, "card"),
  ];
}

export function configureScenarios() {
  const byId = (id) => document.getElementById(`scenario-${id}`);
  const editor = byId("editor"), notice = byId("notice"), select = byId("select");
  const previewArea = byId("validation"), approval = byId("confirm"), buttonGenerate = byId("generate");
  let entry;
  let draft;
  let changed = false;
  let approvedPreview = false;
  let busy = false;
  const report = (text = "", error = false) => {
    notice.textContent = text;
    notice.classList.toggle("error", error);
  };
  const updateControls = () => {
    const builtin = entry?.id === builtinId;
    editor.disabled = busy || builtin;
    select.disabled = busy;
    byId("save").disabled = busy || builtin || !draft;
    byId("delete").disabled = busy || !entry || builtin;
    approval.disabled = busy || !approvedPreview || changed || !entry;
    buttonGenerate.disabled = approval.disabled || !approval.checked;
    for (const id of ["new", "copy", "restore", "import", "preview", "export"]) byId(id).disabled = busy;
  };
  const invalidate = () => {
    approvedPreview = false;
    approval.checked = false;
    previewArea.replaceChildren();
    byId("download").replaceChildren();
    updateControls();
  };
  const onEdit = () => {
    changed = true;
    invalidate();
    report("草稿尚未儲存。");
  };
  const show = (record, modified = false) => {
    entry = modified ? null : record;
    draft = structuredClone(record.preset);
    changed = modified;
    select.value = entry?.id ?? "";
    editor.replaceChildren(node("legend", "情境編輯", "visually-hidden"), ...configurationEditor(draft, onEdit));
    byId("workspace").hidden = false;
    invalidate();
    report(entry?.id === builtinId ? "內建 ABF 為唯讀。使用「複製為自訂情境」開始編輯。" :
      modified ? "草稿尚未儲存。" : "已載入儲存的預設。");
  };
  const refresh = async () => {
    const options = (await request()).map((row) => {
      const option = node("option", row.name);
      option.value = row.id;
      return option;
    });
    const unsaved = node("option", "未儲存的情境");
    unsaved.value = "";
    unsaved.disabled = true;
    select.replaceChildren(unsaved, ...options);
  };
  const discard = () => !changed || window.confirm("要離開這份尚未儲存的草稿嗎？");
  const execute = (action) => async () => {
    if (busy) return;
    busy = true;
    updateControls();
    try { await action(); }
    catch (error) { report(error.message, true); }
    finally { busy = false; updateControls(); }
  };
  const open = async (id) => show(await request(`/${id}`));
  select.addEventListener("change", execute(async () => {
    if (discard()) await open(select.value);
    else select.value = entry?.id ?? "";
  }));
  byId("restore").addEventListener("click", execute(async () => {
    if (discard()) await open(builtinId);
  }));
  byId("copy").addEventListener("click", execute(async () => {
    const copy = structuredClone(draft);
    copy.name = `${copy.name.slice(0, 46)} 副本`;
    show({ preset: copy }, true);
  }));
  byId("new").addEventListener("click", execute(async () => {
    if (!discard()) return;
    const profile = (await request(`/${builtinId}`)).preset;
    profile.name = "新情境";
    const station = { ...profile.config.stations[3], code: "X10", name: "檢驗站", machine_prefix: "CHK" };
    profile.config.stations = [station];
    profile.config.items = [{ ...profile.config.items[0], code: "WIDTH", station: "X10", name: "尺寸" }];
    profile.config.parts = [{ part_no: "PN-001", name: "模擬產品", line: "A" }];
    profile.config.materials.kinds = [];
    for (const event of Object.values(profile.config.events)) event.enabled = false;
    show({ preset: profile }, true);
  }));
  byId("import").addEventListener("click", execute(async () => {
    const file = byId("import-file").files[0];
    const dirty = byId("import-dirty").files[0];
    if (!file) throw new Error("請先選擇設定檔。");
    if (!discard()) return;
    if (file.size > 400000 || (dirty && dirty.size > 100000)) throw new Error("設定檔太大。");
    const profile = await request("/import", "POST", {
      name: file.name.replace(/\.(json|toml)$/i, "").slice(0, 50),
      text: await file.text(),
      dirty_toml: dirty ? await dirty.text() : null,
    });
    show({ preset: profile }, true);
  }));
  byId("export").addEventListener("click", execute(async () => {
    const href = URL.createObjectURL(new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" }));
    const anchor = node("a");
    anchor.href = href;
    anchor.download = "inspection-scenario.json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(href), 500);
  }));
  byId("save").addEventListener("click", execute(async () => {
    const saved = await request(entry ? `/${entry.id}` : "", entry ? "PUT" : "POST",
      entry ? { revision: entry.revision, preset: draft } : draft);
    await refresh();
    show(saved);
    report("已儲存自訂預設。");
  }));
  byId("delete").addEventListener("click", execute(async () => {
    if (!window.confirm(`要移除「${entry.preset.name}」預設嗎？已產生的資料集仍可下載。`)) return;
    await request(`/${entry.id}?revision=${entry.revision}`, "DELETE");
    await refresh();
    await open(builtinId);
    report("已移除自訂預設。");
  }));
  byId("preview").addEventListener("click", execute(async () => {
    invalidate();
    const result = await request("/validate", "POST", { preset: draft, scale: byId("scale").value });
    previewArea.append(node("p", `預估 ${result.lots} 個原始生產批、${result.estimated_records} 筆量測（上限 ${result.maximum_records}）。`));
    previewArea.append(node("p", `輸出格式：${result.formats.map((format) => sourceLabels[format]).join("、") || "無"}。`, "note"));
    for (const [title, messages, klass] of [
      ["阻擋性錯誤", result.errors, "error"], ["警告", result.warnings, "notice"],
    ]) {
      if (!messages.length) continue;
      const list = node("ul", undefined, klass);
      for (const message of messages) list.append(node("li", message));
      previewArea.append(node("h2", title), list);
    }
    approvedPreview = result.errors.length === 0;
    report(approvedPreview ? (changed ? "驗證通過。請儲存預設後重新預覽以確認產生。" : "驗證通過，可確認後產生。") :
      "驗證發現錯誤，請修正後再試。", !approvedPreview);
  }));
  for (const id of ["scale", "seed"]) byId(id).addEventListener("change", invalidate);
  approval.addEventListener("change", updateControls);
  buttonGenerate.addEventListener("click", execute(async () => {
    if (!byId("seed").reportValidity()) return;
    report("正在產生資料集…");
    const result = await request(`/${entry.id}/runs`, "POST", {
      revision: entry.revision, scale: byId("scale").value, seed: Number(byId("seed").value), confirm: true,
    });
    const download = node("a", "下載完整資料集（ZIP）");
    download.href = result.download_url;
    const summary = `已產生 ${result.summary.lots} 批、${result.summary.records} 筆量測及 ${result.summary.files} 份報表。`;
    byId("download").replaceChildren(node("span", summary), download);
    approval.checked = false;
    report(summary);
  }));
  execute(async () => { await refresh(); await open(builtinId); })();
}
