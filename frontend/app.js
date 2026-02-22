const form = document.getElementById("form");
const guidInput = document.getElementById("guid");
const baseUrlInput = document.getElementById("baseUrl");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");

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

function setStatus(msg, kind = "info") {
  statusEl.className = `status ${kind}`;
  statusEl.textContent = msg || "";
}

function showResult(data) {
  lastJson = data;
  resultEl.classList.remove("hidden");

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

function hideResult() {
  resultEl.classList.add("hidden");
  lastJson = null;
}

copyBtn.addEventListener("click", async () => {
  if (!lastJson) return;
  await navigator.clipboard.writeText(JSON.stringify(lastJson, null, 2));
  setStatus("Copied JSON to clipboard ✅", "ok");
  setTimeout(() => setStatus(""), 1200);
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const guid = guidInput.value.trim();
  const baseUrl = baseUrlInput.value.trim().replace(/\/$/, "");

  if (!guid) {
    setStatus("Enter a GUID first.", "warn");
    hideResult();
    return;
  }

  setStatus("Searching…", "info");
  hideResult();

  try {
    const r = await fetch(`${baseUrl}/api/tickets/${encodeURIComponent(guid)}`);
    if (r.status === 404) {
      setStatus("Not found (check GUID or run pipeline).", "warn");
      return;
    }
    if (!r.ok) {
      const t = await r.text();
      setStatus(`Backend error: ${r.status} ${t}`, "err");
      return;
    }

    const data = await r.json();
    setStatus("Found ✅", "ok");
    showResult(data);
  } catch (err) {
    setStatus(`Cannot reach backend. Is it running on ${baseUrl}?`, "err");
  }
});
