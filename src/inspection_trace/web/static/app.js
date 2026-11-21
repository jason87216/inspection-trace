const query = new URLSearchParams(window.location.search);
const requestedTab = query.get("tab");
const tab = ["upload", "history", "records", "trace", "spc"].includes(requestedTab) ? requestedTab : "upload";
const labels = {
  ACCEPT: ["已入庫", ""],
  REJECT: ["拒絕入庫", "reject"],
  DUPLICATE_FILE: ["重複上傳", "duplicate"],
};
const sources = {
  SRC_LEGACY: "舊系統 CSV", SRC_MACHINE: "機台 CSV",
  SRC_STATION: "站點日報", SRC_MANUAL: "人工表單", UNKNOWN: "未辨識",
};
const taipeiTime = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei", dateStyle: "short", timeStyle: "medium", hourCycle: "h23",
});
const hoursFormat = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 });

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function link(text, href) {
  const node = element("a", text);
  node.href = href;
  return node;
}

function tableRow(values) {
  const row = element("tr");
  for (const value of values) {
    const cell = element("td");
    cell.append(value instanceof Node ? value : String(value ?? "—"));
    row.append(cell);
  }
  return row;
}

function statusChip(status) {
  const [label, klass] = labels[status] ?? [status, ""];
  return element("span", label, `chip ${klass}`);
}

function detailLink(batchId, text = "查看") {
  return link(text, `/ui/?tab=history&id=${encodeURIComponent(batchId)}`);
}

function lotLink(lotNo, direction = "up") {
  const params = new URLSearchParams({ tab: "trace", lot_no: lotNo, direction });
  return link(lotNo, `/ui/?${params}`);
}

function localDateTime(value) {
  return `${value.length === 16 ? value + ":00" : value}+08:00`;
}

function formatTime(value) {
  return value ? taipeiTime.format(new Date(value)) : "—";
}

function reportLink(batchId) {
  return link("下載錯誤報告", `/imports/${encodeURIComponent(batchId)}/error-report`);
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (response.ok) return response.json();
  const result = await response.json().catch(() => null);
  const detail = result?.detail;
  const message = typeof detail === "string" ? detail : detail?.[0]?.msg;
  throw new Error(message ?? "請求失敗，請稍後再試。");
}

function indicate(target, message, error = false) {
  target.textContent = message;
  target.classList.toggle("error", error);
}

function renderResult(batch) {
  const card = element("article", undefined, "card stack");
  const header = element("div", undefined, "result-head");
  header.append(element("h2", batch.file_name), statusChip(batch.status));
  card.append(header);
  const summary = `${sources[batch.source_type] ?? batch.source_type} · ${batch.station_code ?? "站點未辨識"} · 入庫 ${batch.inserted_count} 筆 · 錯誤 ${batch.error_count} 項`;
  card.append(element("p", summary, "note"));
  if (batch.duplicate_of_id !== null) {
    const notice = element("p", "檔案內容與先前上傳相同。", "notice");
    notice.append(" ", detailLink(batch.duplicate_of_id, batch.duplicate_file_name ?? "原始匯入"));
    card.append(notice);
  }
  if (batch.warnings.length) {
    const warnings = element("ul", undefined, "notice warning-list");
    for (const warning of batch.warnings) warnings.append(element("li", warning));
    card.append(warnings);
  }
  if (batch.errors.length) {
    card.append(reportLink(batch.id));
    const scroll = element("div", undefined, "table-wrap");
    scroll.tabIndex = 0;
    scroll.setAttribute("aria-label", "錯誤明細");
    const table = element("table");
    const head = element("thead");
    const row = element("tr");
    for (const field of ["列", "欄位", "代碼", "說明", "原始值"]) {
      const cell = element("th", field);
      cell.scope = "col";
      row.append(cell);
    }
    head.append(row);
    const body = element("tbody");
    for (const issue of batch.errors) {
      body.append(tableRow([
        issue.row_no, issue.column, issue.code, issue.message, issue.raw_value === "" ? "（空白）" : issue.raw_value,
      ]));
    }
    table.append(head, body);
    scroll.append(table);
    card.append(scroll);
    if (batch.error_count > batch.errors.length) card.append(element("p", "完整明細請下載錯誤報告。", "note"));
  }
  return card;
}

function configureUpload() {
  const form = document.getElementById("upload-form");
  const input = document.getElementById("files");
  const zone = document.getElementById("dropzone");
  const submit = form.querySelector("button");
  const status = document.getElementById("upload-status");
  const results = document.getElementById("upload-results");

  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (!submit.disabled) zone.classList.add("dragging");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dragging");
    if (!submit.disabled && event.dataTransfer.files.length) input.files = event.dataTransfer.files;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    const files = Array.from(input.files);
    if (!files.length) return;
    submit.disabled = true;
    input.disabled = true;
    results.replaceChildren();
    let failed = 0;
    for (const [index, file] of files.entries()) {
      indicate(status, `處理第 ${index + 1}／${files.length} 份檔案…`);
      try {
        const batch = await getJson(`/imports?file_name=${encodeURIComponent(file.name)}`, {
          method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
        });
        results.append(renderResult(batch));
      } catch (error) {
        failed += 1;
        results.append(element("p", `${file.name}：${error.message}`, "notice"));
      }
    }
    indicate(status, failed ? `處理完成。${failed} 份檔案無法送出。` : `已處理 ${files.length} 份檔案。`, failed > 0);
    input.value = "";
    input.disabled = false;
    submit.disabled = false;
  });
}

function renderPager(target, data, load) {
  if (data.total === 0) {
    target.replaceChildren();
    return;
  }
  const pageCount = Math.ceil(data.total / data.page_size);
  const start = (data.page - 1) * data.page_size + 1;
  const end = Math.min(data.page * data.page_size, data.total);
  const label = element("span", `第 ${start}–${end} 筆，共 ${data.total} 筆`);
  const actions = element("div", undefined, "actions");
  for (const [title, page] of [["上一頁", data.page - 1], ["下一頁", data.page + 1]]) {
    const button = element("button", title, "btn");
    button.type = "button";
    button.disabled = page < 1 || page > pageCount;
    button.addEventListener("click", () => load(page));
    actions.append(button);
  }
  target.replaceChildren(label, actions);
}

function configureHistory() {
  const form = document.getElementById("history-form");
  const body = document.getElementById("history-rows");
  const status = document.getElementById("history-status");
  const pager = document.getElementById("history-pager");
  let statusFilter = "";
  let requestId = 0;

  async function load(page) {
    const current = ++requestId;
    indicate(status, "載入匯入歷史…");
    const params = new URLSearchParams({ page, page_size: 10 });
    if (statusFilter) params.set("status", statusFilter);
    try {
      const data = await getJson(`/imports?${params}`);
      if (current !== requestId) return;
      const rows = data.items.map((batch) => {
        const actions = element("div", undefined, "actions");
        actions.append(detailLink(batch.id));
        if (batch.error_count) actions.append(reportLink(batch.id));
        return tableRow([
          taipeiTime.format(new Date(batch.created_at)), batch.file_name, sources[batch.source_type],
          batch.station_code, statusChip(batch.status), batch.inserted_count, actions,
        ]);
      });
      body.replaceChildren(...rows);
      renderPager(pager, data, load);
      indicate(status, rows.length ? "" : "找不到符合條件的匯入紀錄。");
    } catch (error) {
      if (current !== requestId) return;
      body.replaceChildren();
      pager.replaceChildren();
      indicate(status, error.message, true);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    statusFilter = new FormData(form).get("status");
    load(1);
  });
  load(1);

  const id = query.get("id");
  if (id && /^[0-9]{1,10}$/.test(id)) {
    const target = document.getElementById("history-detail");
    getJson(`/imports/${id}`).then((batch) => {
      target.replaceChildren(renderResult(batch));
      target.scrollIntoView({ block: "nearest" });
    }).catch((error) => target.replaceChildren(element("p", error.message, "notice")));
  }
}

function configureRecords() {
  const form = document.getElementById("records-form");
  const status = document.getElementById("records-status");
  const body = document.getElementById("records-rows");
  const pager = document.getElementById("records-pager");
  let filters = new URLSearchParams();
  let requestId = 0;

  async function load(page) {
    const current = ++requestId;
    indicate(status, "查詢中…");
    const params = new URLSearchParams(filters);
    params.set("page", page);
    params.set("page_size", 10);
    try {
      const data = await getJson(`/records?${params}`);
      if (current !== requestId) return;
      const rows = data.items.map((row) => tableRow([
        lotLink(row.lot_no), `${row.station_code} / ${row.machine_code}`, `${row.item_name} (${row.item_code})`,
        `${row.panel} / ${row.point ?? "—"}`, `${row.value} ${row.unit}`,
        element("span", row.out_of_spec ? "超規" : "規格內", row.out_of_spec ? "chip reject" : "chip"),
        taipeiTime.format(new Date(row.measured_at)), row.operator,
        detailLink(row.import_batch_id, `${row.file_name} · 第 ${row.source_row_no} 列`),
      ]));
      body.replaceChildren(...rows);
      renderPager(pager, data, load);
      indicate(status, rows.length ? "" : "找不到符合條件的檢驗紀錄。");
    } catch (error) {
      if (current !== requestId) return;
      body.replaceChildren();
      pager.replaceChildren();
      indicate(status, error.message, true);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    filters = new URLSearchParams();
    for (const [key, value] of new FormData(form)) {
      if (!value.trim()) continue;
      if (key === "out_of_spec") filters.set(key, "true");
      else if (key === "from" || key === "to") {
        filters.set(key, localDateTime(value));
      }
      else filters.set(key, value.trim());
    }
    load(1);
  });
  indicate(status, "輸入條件後按「查詢」。");
}

function waitValues(item) {
  const names = {
    within_limit: "限時內", over_limit: "超時", incomplete: "資料未齊", invalid_order: "時間順序異常",
  };
  const warning = item.status === "over_limit" || item.status === "invalid_order";
  return [
    `${item.from_station_code} → ${item.to_station_code}`, formatTime(item.from_end),
    formatTime(item.to_start), item.hours === null ? "—" : `${hoursFormat.format(item.hours)} 小時`,
    `${hoursFormat.format(item.limit_hours)} 小時`,
    element("span", names[item.status] ?? item.status, warning ? "chip reject" : "chip"),
  ];
}

function showRows(id, rows, fallback, width) {
  const body = document.getElementById(id);
  if (rows.length) {
    body.replaceChildren(...rows);
  } else {
    const row = tableRow([fallback]);
    row.firstElementChild.colSpan = width;
    body.replaceChildren(row);
  }
}

function configureTrace() {
  const form = document.getElementById("trace-form");
  const status = document.getElementById("trace-status");
  const results = document.getElementById("trace-results");
  let requestId = 0;
  async function load() {
    const params = new FormData(form);
    const lotNo = params.get("lot_no").trim();
    if (!lotNo) {
      ++requestId;
      results.hidden = true;
      indicate(status, "請輸入批號。", true);
      return;
    }
    const current = ++requestId;
    const direction = params.get("direction");
    const maxDepth = params.get("max_depth");
    const path = `/lots/${encodeURIComponent(lotNo)}`;
    const traceParams = new URLSearchParams({ direction, max_depth: maxDepth });
    const pageParams = new URLSearchParams({ tab: "trace", lot_no: lotNo, direction, max_depth: maxDepth });
    window.history.replaceState(null, "", `/ui/?${pageParams}`);
    results.hidden = true;
    indicate(status, "查詢批號、追溯關係及等待時間…");
    try {
      const [lot, trace] = await Promise.all([getJson(path), getJson(`${path}/trace?${traceParams}`)]);
      if (current !== requestId) return;
      const pairs = [
        ["批號", lot.lot_no], ["類型", lot.lot_type === "production" ? "生產批" : "原料批"],
        ...(lot.lot_type === "production"
          ? [["料號", lot.part_no], ["片數", lot.panel_count], ["投產時間", formatTime(lot.released_at)]]
          : [["原料類型", lot.kind]]),
      ];
      document.getElementById("batch-fields").replaceChildren(...pairs.map(([term, value]) => {
        const pair = element("div");
        pair.append(element("dt", term), element("dd", value ?? "—"));
        return pair;
      }));
      const label = trace.direction === "up" ? "往上游" : "往下游";
      document.getElementById("links-summary").textContent =
        `${label} · 最多 ${trace.max_depth} 層 · ${trace.links.length} 筆關係`;
      showRows("trace-links", trace.links.map((row) => tableRow([
        row.depth, lotLink(row.parent_lot_no), row.relation === "split" ? "分批" : "投料",
        lotLink(row.child_lot_no, "down"),
      ])), "這個方向沒有批號關係。", 4);
      showRows("trace-moves", lot.movements.map((move) => tableRow([
        `${move.station_code} ${move.station_name}`, move.machine_code, formatTime(move.start_at),
        move.end_at ? formatTime(move.end_at) : "尚未出站", move.operator,
      ])), "本批沒有進出站紀錄。", 5);
      showRows("trace-qtime", lot.qtime.map((item) => {
        const source = element("div", undefined, "actions");
        const inherited = new Set([item.from_lot_no, item.to_lot_no]);
        for (const origin of inherited) {
          if (origin && origin !== lot.lot_no) source.append(lotLink(origin));
        }
        return tableRow([...waitValues(item), source.childElementCount ? source : "—"]);
      }), "本批沒有適用的 Q-time 紀錄。", 7);
      results.hidden = false;
      indicate(status, "");
    } catch (error) {
      if (current === requestId) indicate(status, error.message, true);
    }
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    load();
  });
  const lot = query.get("lot_no");
  if (lot) {
    form.elements.namedItem("lot_no").value = lot;
    form.elements.namedItem("direction").value = query.get("direction") === "down" ? "down" : "up";
    const depth = query.get("max_depth");
    if (/^(?:[1-9]|10)$/.test(depth ?? "")) form.elements.namedItem("max_depth").value = depth;
    if (form.checkValidity()) load();
  }
}

function configurePeriodQTime() {
  const form = document.getElementById("qtime-form");
  const status = document.getElementById("qtime-status");
  const results = document.getElementById("qtime-results");
  let requestId = 0;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const current = ++requestId;
    const fields = new FormData(form);
    const params = new URLSearchParams({
      from: localDateTime(fields.get("from")), to: localDateTime(fields.get("to")),
      over_only: String(fields.has("over_only")),
    });
    results.hidden = true;
    indicate(status, "查詢等待時間…");
    try {
      const data = await getJson(`/qtime?${params}`);
      if (current !== requestId) return;
      const rows = data.items.map((item) => tableRow([lotLink(item.lot_no), ...waitValues(item)]));
      document.getElementById("qtime-rows").replaceChildren(...rows);
      results.hidden = !rows.length;
      indicate(status, rows.length ? `找到 ${rows.length} 筆等待紀錄。` : "這段期間沒有符合條件的等待紀錄。");
    } catch (error) {
      if (current === requestId) indicate(status, error.message, true);
    }
  });
}

const signalLabels = {
  I_OUTSIDE_3SIGMA: "個別值超出 3σ",
  I_EIGHT_SAME_SIDE: "連續 8 點在中心線同側",
  MR_OUTSIDE_LIMIT: "移動全距超出管制上限",
};
const spcFormat = new Intl.NumberFormat("zh-TW", { maximumSignificantDigits: 6 });

function drawControlChart(canvas, points, limits, baselineSize, field) {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return;
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);

  const values = points.map((point) => point[field]);
  const numeric = values.filter((value) => value !== null);
  if (!numeric.length) return;
  const bounds = limits ? [limits.lower, limits.upper] : [];
  const lo = Math.min(...numeric, ...bounds);
  const hi = Math.max(...numeric, ...bounds);
  const left = 68, right = width - 16, top = 16, bottom = height - 30;
  const x = (index) => left + (right - left) * index / Math.max(1, points.length - 1);
  const y = (value) => {
    const ratio = hi === lo ? 0.5 : (value / 2 - lo / 2) / (hi / 2 - lo / 2);
    return bottom - (bottom - top) * ratio;
  };
  ctx.fillStyle = "#e7effa";
  ctx.fillRect(left, top, x(Math.min(points.length - 1, baselineSize - 0.5)) - left, bottom - top);
  if (limits) {
    for (const level of [limits.lower, limits.center, limits.upper]) {
      ctx.beginPath();
      ctx.strokeStyle = level === limits.center ? "#627186" : "#9a4a24";
      ctx.setLineDash(level === limits.center ? [] : [5, 4]);
      ctx.moveTo(left, y(level));
      ctx.lineTo(right, y(level));
      ctx.stroke();
    }
  }
  ctx.setLineDash([]);
  ctx.strokeStyle = "#1c5fb8";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  values.forEach((value, index) => {
    if (value === null) return;
    ctx.lineTo(x(index), y(value));
  });
  ctx.stroke();
  values.forEach((value, index) => {
    if (value === null) return;
    const marked = points[index].signals.some((signal) =>
      field === "value" ? signal.startsWith("I_") : signal === "MR_OUTSIDE_LIMIT"
    );
    if (!marked && numeric.length > 1) return;
    ctx.beginPath();
    ctx.fillStyle = marked ? "#ad3e14" : "#1c5fb8";
    ctx.arc(x(index), y(value), marked ? 4 : 3, 0, 2 * Math.PI);
    ctx.fill();
  });
  ctx.font = "11px system-ui";
  ctx.fillStyle = "#556170";
  ctx.textAlign = "right";
  for (const level of new Set([lo, hi])) {
    const text = Math.abs(level) >= 1e5 ? level.toExponential(2) : spcFormat.format(level);
    ctx.fillText(text, left - 8, y(level) + 4);
  }
  ctx.textAlign = "left";
  ctx.fillText("#1", left, height - 10);
  ctx.textAlign = "right";
  if (points.length > 1) ctx.fillText(`#${points.length}`, right, height - 10);
}

function configureSpc() {
  const form = document.getElementById("spc-form");
  const status = document.getElementById("spc-status");
  const results = document.getElementById("spc-results");
  const charts = [
    ["individual", "value", "individual_limits"],
    ["range", "moving_range", "range_limits"],
  ];
  let data;
  let requestId = 0;
  const draw = () => {
    if (!data || results.hidden) return;
    for (const [key, field, limitKey] of charts) {
      drawControlChart(document.getElementById(`${key}-chart`), data.points, data[limitKey], data.baseline_points, field);
    }
  };
  new ResizeObserver(draw).observe(results);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const current = ++requestId;
    const fields = new FormData(form);
    const params = new URLSearchParams({
      machine: fields.get("machine").trim(),
      baseline_points: fields.get("baseline_points"),
    });
    for (const key of ["from", "to"]) {
      if (fields.get(key)) params.set(key, localDateTime(fields.get(key)));
    }
    results.hidden = true;
    indicate(status, "正在計算管制圖…");
    try {
      const chart = await getJson(`/spc/${encodeURIComponent(fields.get("item_code").trim())}?${params}`);
      if (current !== requestId) return;
      data = chart;
      if (!chart.points.length) {
        indicate(status, "沒有符合條件的已入庫量測。");
        return;
      }
      const rows = chart.points.flatMap((point, index) => point.signals.length ? [tableRow([
        index + 1, lotLink(point.lot_no), formatTime(point.measured_at), spcFormat.format(point.value),
        point.moving_range === null ? "—" : spcFormat.format(point.moving_range),
        point.signals.map((signal) => signalLabels[signal]).join("、"),
      ])] : []);
      const monitored = Math.max(0, chart.points.length - chart.baseline_points);
      showRows("spc-signals", rows, monitored ? "監測點尚未觸發規則。" : "此期間沒有可判定的監測點。", 6);
      for (const [key, , limitKey] of charts) {
        const limits = chart[limitKey];
        const summary = limits
          ? `下限 ${spcFormat.format(limits.lower)}　中心 ${spcFormat.format(limits.center)}　上限 ${spcFormat.format(limits.upper)} ${chart.unit}　｜　淡藍區為基準，橘點為訊號`
          : `基準點不足，先顯示原始序列（${chart.unit}）。`;
        document.getElementById(`${key}-limits`).textContent = summary;
      }
      results.hidden = false;
      draw();
      indicate(status, monitored
        ? `共 ${chart.points.length} 筆量測，${monitored} 個監測點，${rows.length} 個點觸發規則。`
        : `共 ${chart.points.length} 筆量測。需要超過 ${chart.baseline_points} 筆才有監測點。`);
    } catch (error) {
      if (current === requestId) indicate(status, error.message, true);
    }
  });
}

document.getElementById(`${tab}-page`).hidden = false;
document.querySelector(`[data-tab="${tab}"]`).setAttribute("aria-current", "page");
if (tab === "upload") configureUpload();
if (tab === "history") configureHistory();
if (tab === "records") configureRecords();
if (tab === "spc") configureSpc();
if (tab === "trace") {
  configureTrace();
  configurePeriodQTime();
}
