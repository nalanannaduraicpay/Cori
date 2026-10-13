// Cori demo UI - plain JS, no build step.
// All invoice-derived text is untrusted: always render through esc().
"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STEPS = [["document_loader", "Load"], ["extractors", "Extract"], ["content_guardrail", "Guardrail"], ["consensus_engine", "Consensus"],
  ["validation_agent", "Validate"], ["anomaly_agent", "Anomaly"], ["decided", "Decide"]];
const FIELDS = ["vendor_name", "vendor_id", "vendor_country", "invoice_number", "invoice_date", "due_date", "currency",
  "subtotal", "tax_amount", "total_amount", "client_number", "matter_number", "bank_name", "bank_account", "swift_bic"];
let pollTimer = null;

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = JSON.stringify((await r.json()).detail); } catch (_) { /* keep statusText */ }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json();
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
}

function decisionBadge(d) {
  if (!d) return '<span class="badge info">-</span>';
  return d === "ADVANCED_TO_APPROVAL" ? '<span class="badge ok">Advanced to approval</span>'
    : '<span class="badge bad">Needs review</span>';
}
function statusBadge(s) {
  const cls = { PENDING: "info", ADVANCED_TO_APPROVAL: "ok", NEEDS_REVIEW: "bad", CORI_PROCESSING: "warn" }[s] || "info";
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}
function sevBadge(s) {
  const cls = { HIGH: "bad", MEDIUM: "warn", LOW: "info", INFO: "info" }[s] || "info";
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}
const money = (m) => (m && m.amount != null ? esc(m.display) : '<span class="muted">n/a</span>');

// ---------------------------------------------------------------- health
async function loadHealth() {
  try {
    const h = await api("/health");
    $("#health").textContent = `DB ${h.database} (${h.db_backend}) · LLM ${h.llm_provider} · extractors: ${h.extractors.join(", ")}`;
  } catch (e) { $("#health").textContent = "API unreachable"; }
}

// ---------------------------------------------------------------- invoices
async function loadInvoices() {
  const status = $("#filter").value;
  const rows = await api("/invoices" + (status ? `?status=${encodeURIComponent(status)}` : ""));
  const tb = $("#invoice-table tbody");
  tb.innerHTML = rows.map((r) => `
    <tr class="clickable" data-id="${r.invoice_id}">
      <td><input type="checkbox" class="pick" value="${r.invoice_id}" ${["PENDING", "NEEDS_REVIEW"].includes(r.status) ? "" : "disabled"}></td>
      <td class="mono">${r.invoice_id}</td><td>${esc(r.file_name)}</td>
      <td>${statusBadge(r.status)}</td><td class="small muted">${esc((r.created_at || "").replace("T", " "))}</td>
    </tr>`).join("");
  $("#empty").hidden = rows.length > 0;
  tb.querySelectorAll("tr").forEach((tr) => tr.addEventListener("click", (ev) => {
    if (ev.target.classList.contains("pick")) return;
    const st = tr.querySelector(".badge").textContent;
    if (st !== "PENDING") showDetail(tr.dataset.id);
  }));
  tb.querySelectorAll(".pick").forEach((cb) => cb.addEventListener("change", updateSend));
  $("#select-all").checked = false;
  updateSend();
}

function selectedIds() { return [...document.querySelectorAll(".pick:checked")].map((c) => Number(c.value)); }
function updateSend() {
  const n = selectedIds().length;
  $("#send").disabled = n === 0;
  $("#send").textContent = n ? `Send ${n} to Cori` : "Send to Cori";
}

async function sendToCori() {
  const ids = selectedIds();
  if (!ids.length) return;
  $("#send").disabled = true;
  try {
    const res = await api("/batches", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invoice_ids: ids, submitted_by: "demo.user" }) });
    toast(`Batch submitted: ${res.invoice_count} invoices`);
    watchBatch(res.batch_id);
  } catch (e) { toast("Submit failed: " + e.message); updateSend(); }
}

async function uploadFiles(files) {
  for (const f of files) {
    const fd = new FormData(); fd.append("file", f);
    try { await api("/invoices/upload", { method: "POST", body: fd }); toast(`Uploaded ${f.name}`); }
    catch (e) { toast(`Upload failed for ${f.name}: ${e.message}`); }
  }
  $("#filter").value = "PENDING";
  loadInvoices();
}

// ---------------------------------------------------------------- batch progress
function stepChips(item) {
  const cur = item.status === "DONE" ? "decided" : (item.current_step || "").startsWith("extractor_") ? "extractors" : item.current_step;
  const idx = STEPS.findIndex(([k]) => k === cur);
  return `<div class="steps">${STEPS.map(([k, label], i) => {
    const cls = item.status === "DONE" || i < idx ? "done" : i === idx ? "active" : "";
    return `<span class="step ${cls}">${label}</span>`;
  }).join("")}</div>`;
}

function watchBatch(batchId) {
  clearInterval(pollTimer);
  $("#batch-panel").hidden = false;
  $("#batch-id").textContent = batchId;
  const tick = async () => {
    let b;
    try { b = await api(`/batches/${batchId}`); } catch (e) { return; }
    $("#batch-bar").style.width = `${Math.round((100 * b.processed) / Math.max(1, b.invoice_count))}%`;
    $("#batch-meta").innerHTML = `${esc(b.status)} · ${b.processed}/${b.invoice_count} processed · ` +
      `<span class="badge ok">${b.decision_counts.ADVANCED_TO_APPROVAL || 0} advanced</span> ` +
      `<span class="badge bad">${b.decision_counts.NEEDS_REVIEW || 0} need review</span>`;
    $("#batch-table tbody").innerHTML = b.items.map((it) => `
      <tr class="${it.decision ? "clickable" : ""}" data-id="${it.invoice_id}">
        <td class="mono">${it.invoice_id}</td><td>${esc(it.file_name)}</td><td>${stepChips(it)}</td>
        <td>${decisionBadge(it.decision)}</td>
        <td>${it.decision ? '<button class="btn link">View</button>' : ""}</td>
      </tr>`).join("");
    $("#batch-table tbody").querySelectorAll("tr.clickable").forEach((tr) =>
      tr.addEventListener("click", () => showDetail(tr.dataset.id)));
    if (["COMPLETED", "FAILED", "INTERRUPTED"].includes(b.status)) { clearInterval(pollTimer); loadInvoices(); }
  };
  tick();
  pollTimer = setInterval(tick, 1000);
}

// ---------------------------------------------------------------- detail
function extractorTable(v) {
  const ex = v.extractions || [];
  if (!ex.length) return '<p class="muted">No extractor output.</p>';
  const dis = Object.fromEntries((v.consensus?.disagreements || []).map((d) => [d.field, d]));
  const status = v.consensus?.field_status || {};
  const head = `<tr><th>Field</th>${ex.map((e) => `<th>${esc(e.name)}<div class="small muted">${esc(e.method)} · ${esc(e.model)}</div>
      ${e.status === "ok" ? "" : `<div class="badge bad">${esc(e.status)}</div><div class="small">${esc(e.error)}</div>`}</th>`).join("")}<th>Consensus</th></tr>`;
  const val = (e, f) => {
    if (!e.normalized) return "";
    if (f === "currency") return e.normalized.currency || (e.normalized.currency_candidates?.length ? `? ${e.normalized.currency_candidates.join("/")}` : "");
    return e.normalized[f] ?? "";
  };
  const rows = FIELDS.map((f) => {
    const d = dis[f];
    const cls = d ? (d.critical ? "dis-critical" : "dis-minor") : "";
    return `<tr><td>${f}</td>${ex.map((e) => `<td class="${cls} mono">${esc(val(e, f))}</td>`).join("")}
      <td class="small">${esc(status[f] || "")}${d ? `<div class="muted">${esc(d.resolution)}</div>` : ""}</td></tr>`;
  });
  const li = dis.line_items;
  rows.push(`<tr><td>line_items</td>${ex.map((e) => `<td class="${li ? (li.critical ? "dis-critical" : "dis-minor") : ""} small">
      ${e.normalized ? e.normalized.line_items.length + " lines" : ""}</td>`).join("")}<td class="small">${esc(status.line_items || "")}</td></tr>`);
  return `<table class="grid">${head}${rows.join("")}</table>
    <p class="small muted">Red = critical field disagreement (blocks advancement); amber = non-critical, resolved by majority.</p>`;
}

function baselineBlock(a) {
  const b = a.baseline;
  if (!b) return `<p class="muted">${esc(a.skipped_reason || "No baseline.")}</p>`;
  return `<dl class="kv">
    <dt>Level used</dt><dd><b>${esc(b.level)}</b> ${b.level_key ? `(${esc(b.level_key)})` : ""} · n=${b.count}
      <span class="small muted">tried: ${esc((b.levels_tried || []).join(", "))}</span></dd>
    <dt>Median</dt><dd>${money(b.median)}</dd><dt>Mean</dt><dd>${money(b.mean)}</dd>
    <dt>Std dev</dt><dd>${money(b.std)}</dd><dt>95th percentile</dt><dd>${money(b.p95)}</dd>
    <dt>z-score</dt><dd>${a.z_score ?? "n/a"}</dd>
    <dt>Native (${esc(b.native_currency || "-")})</dt><dd>median ${money(b.native_median)} · p95 ${money(b.native_p95)} · n=${b.native_count}</dd>
  </dl>`;
}

async function showDetail(id) {
  const v = await api(`/invoices/${id}/decision`);
  const p = $("#detail-panel"); p.hidden = false;
  if (!v.decision) { $("#detail").innerHTML = `<p>No decision yet for invoice ${esc(id)}.</p>`; return; }
  const inv = v.invoice;
  const checks = v.validation_checks || [];
  const flags = v.anomaly?.flags || [];
  const sims = v.anomaly?.similar_invoices || [];
  $("#detail").innerHTML = `
    <div class="panel-head"><div class="decision-head"><h2>Invoice ${esc(v.invoice_id)} · ${esc(v.file_name)}</h2>
      ${decisionBadge(v.decision)}</div>
      <div><a class="btn secondary" href="/invoices/${encodeURIComponent(v.invoice_id)}/pdf" target="_blank" rel="noopener">Open PDF</a>
      <a class="btn secondary" href="/invoices/${encodeURIComponent(v.invoice_id)}/audit" target="_blank" rel="noopener">Audit trail</a></div></div>
    <div class="summary"><b>Cori:</b> ${esc(v.summary)}</div>
    <h3>Reasons</h3>
    ${v.reasons.length ? `<ul class="reasons">${v.reasons.map((r) => `<li>${sevBadge(r.severity)}
      <span class="mono small">${esc(r.code)}</span><span>${esc(r.message)}</span>${r.blocking ? "" : '<span class="badge info">note</span>'}</li>`).join("")}</ul>`
      : '<p class="muted">No issues found.</p>'}
    <div class="cols">
      <div><h3>Invoice (agreed values)</h3><dl class="kv">
        <dt>Vendor</dt><dd>${esc(inv.vendor_name)} <span class="muted">${esc(inv.vendor_id || "")}</span></dd>
        <dt>Invoice no.</dt><dd class="mono">${esc(inv.invoice_number)}</dd><dt>Date</dt><dd>${esc(inv.invoice_date)}</dd>
        <dt>Client / matter</dt><dd>${esc(inv.client_number)} / ${esc(inv.matter_number)}</dd>
        <dt>Currency</dt><dd>${esc(inv.currency || "unresolved")} <span class="small muted">${esc(inv.currency_resolution?.method || "")}</span></dd>
        <dt>Subtotal</dt><dd>${money(inv.subtotal)}</dd><dt>Tax</dt><dd>${money(inv.tax)}</dd>
        <dt>Total</dt><dd><b>${money(inv.total)}</b></dd><dt>Total (base)</dt><dd>${money(inv.total_base)}</dd>
        <dt>FX used</dt><dd>${v.fx.rate ? `${esc(v.fx.pair)} ${esc(Number(v.fx.rate).toFixed(6))} <span class="small muted">rate date ${esc(v.fx.rate_date)}</span>` : "n/a"}</dd>
      </dl></div>
      <div><h3>Validation checks</h3><ul class="reasons">${checks.map((c) =>
        `<li class="${c.passed ? "check-ok" : "check-bad"}"><span>${esc(c.message)}</span></li>`).join("") || '<li class="muted">Not run</li>'}</ul></div>
    </div>
    <h3>Extractors side by side</h3>${extractorTable(v)}
    <div class="cols">
      <div><h3>Anomaly baseline</h3>${baselineBlock(v.anomaly || {})}</div>
      <div><h3>Anomaly flags</h3>${flags.length ? `<ul class="reasons">${flags.map((f) => `<li>${sevBadge(f.severity)} <span>${esc(f.message)}</span></li>`).join("")}</ul>` : '<p class="muted">None</p>'}</div>
    </div>
    <h3>Similar historical invoices (vector search)</h3>
    ${sims.length ? `<table class="grid"><tr><th>Invoice</th><th>Vendor</th><th>Date</th><th class="num">Total</th><th class="num">Total (base)</th><th class="num">Distance</th></tr>
      ${sims.map((s) => `<tr><td class="mono">${esc(s.invoice_number)}</td><td>${esc(s.vendor_name)}</td><td>${esc(s.invoice_date)}</td>
        <td class="num">${money(s.total)}</td><td class="num">${money(s.total_base)}</td><td class="num">${esc(s.distance)}</td></tr>`).join("")}</table>
      <p class="small muted">Average of the closest ${sims.length}: ${money(v.anomaly.similar_avg)}</p>` : '<p class="muted">None</p>'}
    <h3>Reviewer feedback</h3>
    <form class="feedback" id="fb">
      <div class="row"><input type="text" name="reviewer" placeholder="Reviewer" required>
        <input type="text" name="field" placeholder="Field (e.g. total_amount)"><input type="text" name="value" placeholder="Corrected value"></div>
      <div class="row">${[...new Set(v.reasons.map((r) => r.code))].map((c) => `<label class="small"><input type="checkbox" name="clear" value="${esc(c)}"> clear ${esc(c)}</label>`).join("")}</div>
      <div><button class="btn secondary" type="submit">Save feedback</button></div>
      ${(v.feedback || []).length ? `<p class="small muted">${v.feedback.length} feedback record(s) saved.</p>` : ""}
    </form>`;
  $("#fb").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const field = f.get("field");
    const body = { reviewer: f.get("reviewer"), cleared_flags: f.getAll("clear"),
      corrections: field ? [{ field_name: field, agent_value: String(v.invoice?.[field]?.amount ?? v.invoice?.[field] ?? ""), corrected_value: f.get("value") }] : [] };
    try { await api(`/invoices/${v.invoice_id}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      toast("Feedback saved"); showDetail(v.invoice_id); } catch (e) { toast("Feedback failed: " + e.message); }
  });
  p.scrollIntoView({ behavior: "smooth" });
}

// ---------------------------------------------------------------- wire up
$("#send").addEventListener("click", sendToCori);
$("#filter").addEventListener("change", loadInvoices);
$("#upload").addEventListener("change", (e) => uploadFiles(e.target.files));
$("#select-all").addEventListener("change", (e) => {
  document.querySelectorAll(".pick:not(:disabled)").forEach((c) => { c.checked = e.target.checked; });
  updateSend();
});
loadHealth();
loadInvoices();
