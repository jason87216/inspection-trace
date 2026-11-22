const fields = [
  ["lot_no", "批號", true], ["panel", "片號", true],
  ["machine_code", "機台", true], ["item_code", "檢驗項目", true],
  ["value", "量測值", true], ["time", "量測時間", true],
  ["point", "量測點（連續量測需提供）"], ["unit", "單位"], ["operator", "操作員"],
];

export function configureTemplates(getJson, element, indicate) {
  const byId = (id) => document.getElementById(`template-${id}`);
  const select = byId("select"), preview = byId("preview"), editor = byId("editor");
  const approval = byId("approval"), notice = byId("notice");
  const mapping = {};
  let templates = [], headers = [], current = null, documentFormat = null;
  let documentHeaderRow = 1;
  let busy = false, suggestAvailable = false, savedChanges = false;
  const call = (path, method, data) => getJson(path, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  });
  const controls = () => {
    byId("suggest").disabled = busy || !suggestAvailable || !headers.length || current?.status === "approved";
    editor.querySelector('button[type="submit"]').disabled = busy || current?.status === "approved"
      || documentHeaderRow !== Number(preview.elements.header_row.value);
    approval.hidden = current?.status !== "draft" || !savedChanges;
    approval.querySelector("button").disabled = busy;
    preview.querySelector('button[type="submit"]').disabled = busy;
    select.disabled = busy;
    byId("new").disabled = busy;
  };
  const showMapping = () => {
    const names = [...new Set([...headers, ...Object.values(mapping)].filter(Boolean))];
    const panel = byId("fields");
    panel.replaceChildren();
    for (const [code, caption, required] of fields) {
      const label = element("label", caption, "field");
      const input = element("select");
      input.name = code;
      input.required = Boolean(required);
      input.disabled = current?.status === "approved";
      const empty = element("option", required ? "請選表頭" : "未對應");
      empty.value = "";
      input.append(empty);
      for (const name of names) {
        const option = element("option", name);
        option.value = name;
        input.append(option);
      }
      input.value = mapping[code] ?? "";
      input.addEventListener("change", () => { mapping[code] = input.value; });
      label.append(input);
      panel.append(label);
    }
  };
  const load = (selected) => {
    current = selected ?? null;
    headers = [];
    documentFormat = current?.file_type ?? null;
    documentHeaderRow = current?.header_row ?? 1;
    savedChanges = Boolean(current);
    for (const key of Object.keys(mapping)) delete mapping[key];
    Object.assign(mapping, current?.column_map ?? {});
    preview.elements.header_row.value = current?.header_row ?? "1";
    editor.elements.name.value = current?.name ?? "";
    editor.elements.date_format.value = current?.date_format ?? "auto";
    editor.elements.default_unit.value = current?.default_unit ?? "";
    for (const key of ["name", "date_format", "default_unit"]) {
      editor.elements[key].disabled = current?.status === "approved";
    }
    editor.hidden = !current;
    byId("headers-summary").textContent = "";
    select.value = current?.id ?? "";
    showMapping();
    controls();
    indicate(notice, current?.status === "approved" ? `已由 ${current.approved_by} 核准` : "");
  };
  const refresh = async (id = null) => {
    templates = await getJson("/templates");
    const blank = element("option", "選擇既有範本");
    blank.value = "";
    select.replaceChildren(blank, ...templates.map((template) => {
      const option = element("option", `${template.name}（${template.status === "approved" ? "已核准" : "草稿"}）`);
      option.value = template.id;
      return option;
    }));
    load(templates.find((template) => template.id === id));
  };
  const perform = (action) => async (event) => {
    event?.preventDefault();
    if (busy) return;
    busy = true;
    controls();
    try { await action(); }
    catch (error) { indicate(notice, error.message, true); }
    finally { busy = false; controls(); }
  };
  select.addEventListener("change", () => load(templates.find((row) => row.id === Number(select.value))));
  byId("new").addEventListener("click", () => load(null));
  editor.addEventListener("input", () => { savedChanges = false; controls(); });
  preview.elements.header_row.addEventListener("input", () => { savedChanges = false; controls(); });
  byId("preview").addEventListener("submit", perform(async () => {
    const file = preview.elements.file.files[0];
    if (!file) return;
    const sameDraft = current?.status === "draft";
    const requestedRow = Number(preview.elements.header_row.value);
    const params = new URLSearchParams({ file_name: file.name, header_row: requestedRow });
    const result = await getJson(`/templates/preview?${params}`, {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
    });
    if (!sameDraft) load(null);
    documentFormat = result.file_type;
    documentHeaderRow = result.header_row;
    preview.elements.header_row.value = result.header_row;
    headers = result.headers;
    for (const key of Object.keys(mapping)) delete mapping[key];
    Object.assign(mapping, result.suggestions);
    byId("headers-summary").textContent = `第 ${result.header_row} 列，共 ${headers.length} 欄；下方有 ${result.data_rows} 列。`;
    savedChanges = false;
    editor.hidden = false;
    showMapping();
    indicate(notice, "請核對欄位對應，再儲存草稿。");
  }));
  byId("suggest").addEventListener("click", perform(async () => {
    const labels = headers.filter((label) => label.trim() && label.length <= 100);
    const result = await call("/templates/suggest", "POST", { headers: labels });
    Object.assign(mapping, result);
    savedChanges = false;
    showMapping();
    indicate(notice, "已填入本機模型的建議，請逐欄確認。");
  }));
  editor.addEventListener("submit", perform(async () => {
    const values = new FormData(editor);
    const columnMap = Object.fromEntries(
      fields.map(([key]) => [key, values.get(key)]).filter(([, value]) => value),
    );
    const draft = await call(current ? `/templates/${current.id}` : "/templates",
      current ? "PUT" : "POST", {
        name: values.get("name"), file_type: documentFormat,
        header_row: Number(preview.elements.header_row.value),
        column_map: columnMap, date_format: values.get("date_format"),
        default_unit: values.get("default_unit")?.trim() || null,
        ...(current ? {revision: current.revision} : {}),
      });
    await refresh(draft.id);
    indicate(notice, "草稿已儲存，確認後可以核准。");
  }));
  approval.addEventListener("submit", perform(async () => {
    const approved = await call(`/templates/${current.id}/approve`, "POST", {
      approved_by: approval.elements.approved_by.value,
      revision: current.revision,
    });
    approval.reset();
    await refresh(approved.id);
    indicate(notice, "範本已核准，可在「上傳匯入」選用。");
  }));
  perform(async () => {
    suggestAvailable = (await getJson("/templates/model")).available;
    await refresh();
  })();
}
