const baseUrlInput = document.getElementById("baseUrl");
const statusEl = document.getElementById("status");
const refreshBtn = document.getElementById("refreshBtn");

const kpi_total = document.getElementById("kpi_total");
const kpi_assigned = document.getElementById("kpi_assigned");
const kpi_unassigned = document.getElementById("kpi_unassigned");
const kpi_review = document.getElementById("kpi_review");
const kpi_spam = document.getElementById("kpi_spam");
const kpi_avgurg = document.getElementById("kpi_avgurg");

const f_office = document.getElementById("f_office");
const f_assigned = document.getElementById("f_assigned");
const f_segment = document.getElementById("f_segment");
const f_category = document.getElementById("f_category");
const f_lang = document.getElementById("f_lang");
const f_review = document.getElementById("f_review");
const f_umin = document.getElementById("f_umin");
const f_umax = document.getElementById("f_umax");
const f_q = document.getElementById("f_q");

const applyBtn = document.getElementById("applyBtn");
const clearBtn = document.getElementById("clearBtn");

const tbody = document.getElementById("tbody");
const prevBtn = document.getElementById("prevBtn");
const nextBtn = document.getElementById("nextBtn");
const pageInfo = document.getElementById("pageInfo");

const detailEl = document.getElementById("detail");
const titleEl = document.getElementById("title");
const officeEl = document.getElementById("office");
const managerEl = document.getElementById("manager");
const segmentEl = document.getElementById("segment");
const langEl = document.getElementById("lang");
const catEl = document.getElementById("cat");
const sentEl = document.getElementById("sent");
const urgEl = document.getElementById("urg");
const reviewEl = document.getElementById("review");
const summaryEl = document.getElementById("summary");
const descEl = document.getElementById("desc");
const addrEl = document.getElementById("addr");
const rawEl = document.getElementById("raw");
const copyBtn = document.getElementById("copyBtn");

let lastJson = null;
let page = 0;
const pageSize = 25;

let chCategory = null;
let chOffice = null;
let chUrgency = null;
let chLang = null;

function baseUrl() {
  return baseUrlInput.value.trim().replace(/\/$/, "");
}

function setStatus(msg, kind = "info") {
  statusEl.className = `status ${kind}`;
  statusEl.textContent = msg || "";
}

function qs(params) {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
}

function currentFilters() {
  return {
    office_id: f_office.value || "",
    assigned: f_assigned.value || "",
    segment: f_segment.value || "",
    category: f_category.value.trim() || "",
    language: f_lang.value || "",
    needs_review: f_review.value === "" ? "" : f_review.value,
    min_urgency: f_umin.value || "",
    max_urgency: f_umax.value || "",
    q: f_q.value.trim() || "",
  };
}

function destroyChart(ch) {
  if (ch) ch.destroy();
  return null;
}

function renderBarChart(canvasId, labels, values) {
  const ctx = document.getElementById(canvasId);
  return new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ label: "Count", data: values }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 } },
      },
    },
  });
}

function renderLineChart(canvasId, labels, values) {
  const ctx = document.getElementById(canvasId);
  return new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{ label: "Count", data: values, tension: 0.25 }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

async function fetchJson(path) {
  const r = await fetch(`${baseUrl()}${path}`);
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`${r.status} ${t}`);
  }
  return await r.json();
}

async function loadOffices() {
  const data = await fetchJson("/api/offices");
  f_office.innerHTML = "";
  const optAll = document.createElement("option");
  optAll.value = "";
  optAll.textContent = "All";
  f_office.appendChild(optAll);

  data.forEach((o) => {
    const opt = document.createElement("option");
    opt.value = o.id;
    opt.textContent = o.office_name;
    f_office.appendChild(opt);
  });
}

function setKPIs(kpi) {
  kpi_total.textContent = kpi.total ?? "—";
  kpi_assigned.textContent = kpi.assigned ?? "—";
  kpi_unassigned.textContent = kpi.unassigned ?? "—";
  kpi_review.textContent = kpi.needs_review ?? "—";
  kpi_spam.textContent = kpi.spam ?? "—";
  kpi_avgurg.textContent = (kpi.avg_urgency ?? 0).toFixed(2);
}

function renderCharts(stats) {
  // category
  const catLabels = stats.by_category.map((x) => x.key);
  const catValues = stats.by_category.map((x) => x.value);
  chCategory = destroyChart(chCategory);
  chCategory = renderBarChart("ch_category", catLabels, catValues);

  // office
  const offLabels = stats.by_office.map((x) => x.key);
  const offValues = stats.by_office.map((x) => x.value);
  chOffice = destroyChart(chOffice);
  chOffice = renderBarChart("ch_office", offLabels, offValues);

  // urgency 1..10
  const uLabels = stats.urgency_hist.map((x) => String(x.key));
  const uValues = stats.urgency_hist.map((x) => x.value);
  chUrgency = destroyChart(chUrgency);
  chUrgency = renderLineChart("ch_urgency", uLabels, uValues);

  // language
  const lLabels = stats.by_language.map((x) => x.key);
  const lValues = stats.by_language.map((x) => x.value);
  chLang = destroyChart(chLang);
  chLang = renderBarChart("ch_lang", lLabels, lValues);
}

function hideDetail() {
  detailEl.classList.add("hidden");
  lastJson = null;
}

function showDetail(data) {
  lastJson = data;
  detailEl.classList.remove("hidden");

  titleEl.textContent = `Ticket: ${data.client_guid}`;
  officeEl.textContent = data.assigned_office ?? "—";
  managerEl.textContent = data.assigned_manager ?? "—";
  segmentEl.textContent = data.segment ?? "—";
  langEl.textContent = data.final_language ?? "—";

  catEl.textContent = data.type_category ?? "—";
  sentEl.textContent = data.sentiment ?? "—";
  urgEl.textContent = data.urgency ?? "—";
  reviewEl.textContent = data.needs_review === true ? "YES" : "NO";

  summaryEl.textContent = data.summary ?? "—";
  descEl.textContent = data.description ?? "—";

  const addr = [data.country, data.region, data.city, data.street, data.house]
    .filter(Boolean)
    .join(", ");
  addrEl.textContent = addr || "—";

  rawEl.textContent = JSON.stringify(data, null, 2);
}

copyBtn.addEventListener("click", async () => {
  if (!lastJson) return;
  await navigator.clipboard.writeText(JSON.stringify(lastJson, null, 2));
  setStatus("Copied JSON ✅", "ok");
  setTimeout(() => setStatus(""), 900);
});

async function loadStats() {
  const f = currentFilters();
  // stats endpoint supports only some filters (office/segment/lang)
  const s = qs({
    office_id: f.office_id,
    segment: f.segment,
    language: f.language,
  });
  const stats = await fetchJson(`/api/stats${s}`);
  setKPIs(stats.kpi);
  renderCharts(stats);
}

function rowCell(text) {
  const td = document.createElement("td");
  td.textContent = text ?? "";
  return td;
}

function shorten(s, n = 90) {
  if (!s) return "";
  const x = String(s);
  return x.length <= n ? x : x.slice(0, n) + "…";
}

async function loadTickets() {
  const f = currentFilters();
  const s = qs({
    ...f,
    limit: pageSize,
    offset: page * pageSize,
  });

  const data = await fetchJson(`/api/tickets${s}`);

  tbody.innerHTML = "";
  data.items.forEach((it) => {
    const tr = document.createElement("tr");
    tr.className = "rowHover";

    const guid = document.createElement("td");
    guid.innerHTML = `<span class="linkish">${it.client_guid}</span>`;
    guid.addEventListener("click", async () => {
      setStatus("Loading ticket…", "info");
      try {
        const t = await fetchJson(
          `/api/tickets/${encodeURIComponent(it.client_guid)}`,
        );
        showDetail(t);
        setStatus("", "info");
        window.scrollTo({
          top: document.body.scrollHeight,
          behavior: "smooth",
        });
      } catch (e) {
        setStatus(`Ticket load failed: ${e.message}`, "err");
      }
    });
    tr.appendChild(guid);

    tr.appendChild(rowCell(it.assigned_office || "—"));
    tr.appendChild(rowCell(it.assigned_manager || "—"));
    tr.appendChild(rowCell(it.segment || "—"));
    tr.appendChild(rowCell(it.type_category || "—"));
    tr.appendChild(rowCell(it.urgency ?? "—"));
    tr.appendChild(rowCell(it.final_language || "—"));
    tr.appendChild(rowCell(it.needs_review ? "YES" : "NO"));
    tr.appendChild(rowCell(shorten(it.summary)));

    tbody.appendChild(tr);
  });

  const total = data.total || 0;
  const start = page * pageSize + 1;
  const end = Math.min((page + 1) * pageSize, total);
  pageInfo.textContent = total ? `${start}-${end} of ${total}` : "0";

  prevBtn.disabled = page === 0;
  nextBtn.disabled = (page + 1) * pageSize >= total;
}

async function refreshAll() {
  setStatus("Refreshing…", "info");
  hideDetail();
  try {
    await loadStats();
    await loadTickets();
    setStatus("Ready ✅", "ok");
    setTimeout(() => setStatus(""), 700);
  } catch (e) {
    setStatus(`Error: ${e.message}`, "err");
  }
}

refreshBtn.addEventListener("click", () => refreshAll());

applyBtn.addEventListener("click", () => {
  page = 0;
  refreshAll();
});

clearBtn.addEventListener("click", () => {
  f_office.value = "";
  f_assigned.value = "";
  f_segment.value = "";
  f_category.value = "";
  f_lang.value = "";
  f_review.value = "";
  f_umin.value = "";
  f_umax.value = "";
  f_q.value = "";
  page = 0;
  refreshAll();
});

prevBtn.addEventListener("click", () => {
  if (page === 0) return;
  page -= 1;
  loadTickets();
});

nextBtn.addEventListener("click", () => {
  page += 1;
  loadTickets();
});

// init
(async () => {
  try {
    await loadOffices();
    await refreshAll();
  } catch (e) {
    setStatus(`Init error: ${e.message}`, "err");
  }
})();
