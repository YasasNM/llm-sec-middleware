"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = { role: "clinician", scenarios: [], last: null, lastResult: null, docs: [], doc: null, llm: false };

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch (_) { /* empty body */ }
  if (!r.ok) {
    const d = body && body.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : `Request failed (${r.status})`);
  }
  return body;
}
let toastTimer;
function toast(msg, ms = 3500) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), ms);
}
const fail = (e) => toast(e.message || String(e), 6000);
const pct = (x) => `${Math.round(x * 100)}%`;

/* ---------- header, tabs, role ---------- */
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll("#tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.id === `tab-${b.dataset.tab}`));
  if (b.dataset.tab === "policies") loadPolicies();
  if (b.dataset.tab === "behaviour") loadEvents();
}));
function setRole(role) {
  state.role = role;
  $("roleClinician").classList.toggle("on", role === "clinician");
  $("roleAdmin").classList.toggle("on", role === "admin");
  $("roleClinician").setAttribute("aria-pressed", String(role === "clinician"));
  $("roleAdmin").setAttribute("aria-pressed", String(role === "admin"));
  $("roleLabel").textContent = role === "admin" ? "as an administrator" : "as a clinician";
  $("adminPanel").hidden = role !== "admin" || !state.last;
  if (role === "admin" && state.last) showTrace(state.last.requestId);
  document.querySelectorAll(".adminonly").forEach((n) => (n.hidden = role !== "admin"));
}
$("roleClinician").onclick = () => setRole("clinician");
$("roleAdmin").onclick = () => setRole("admin");

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    const chips = [];
    const c3 = s.c3;
    chips.push(c3 ? `<span class="chip ok">Policy check ready</span>` : `<span class="chip bad">Policy check not reachable</span>`);
    if (c3) {
      chips.push(`<span class="chip ${c3.model_loaded ? "ok" : "bad"}">Model: ${esc(c3.model_version || "none")}</span>`);
      chips.push(`<span class="chip">Retriever: ${esc(c3.embedder)}</span>`);
    }
    chips.push(s.c4 ? `<span class="chip ok">Behaviour check ready</span>` : `<span class="chip bad">Behaviour check not reachable</span>`);
    state.llm = s.ollama;
    $("useLlm").disabled = !s.ollama;
    $("llmHint").textContent = s.ollama ? "" : "(not connected)";
    $("chips").innerHTML = chips.join("");
  } catch (e) { $("chips").innerHTML = `<span class="chip bad">Demo server not reachable</span>`; }
}

/* ---------- chat ---------- */
async function loadScenarios() {
  state.scenarios = await api("/api/scenarios");
  $("scenario").innerHTML = `<option value="">Write my own</option>` +
    state.scenarios.map((s, i) => `<option value="${i}">${esc(s.title)}</option>`).join("");
}
$("scenario").onchange = () => {
  const s = state.scenarios[$("scenario").value];
  if (!s) return;
  $("prompt").value = s.prompt; $("answer").value = s.answer;
  $("sendNote").textContent = s.note ? `Expected: ${s.expect}. ${s.note}` : "";
};
function bubble(cls, html) {
  const d = document.createElement("div"); d.className = `bubble ${cls}`; d.innerHTML = html;
  const t = $("thread"); if (t.querySelector("p.muted")) t.innerHTML = "";
  t.appendChild(d); t.scrollTop = t.scrollHeight; return d;
}
function renderPipeline(stages) {
  $("pipeline").innerHTML = stages.map((s) =>
    `<div class="step ${s.stage === "Policy check" ? (s.note === "violation" ? "is-bad" : s.note === "clean" ? "is-ok" : "hot") : ""}"><b>${esc(s.stage)}</b><span>${esc(s.note)}${s.ms ? ` · ${s.ms} ms` : ""}</span></div>`).join("");
}
async function send(promptOverride) {
  const prompt = promptOverride?.prompt ?? $("prompt").value.trim();
  const answer = promptOverride?.answer ?? $("answer").value.trim();
  if (!prompt) return toast("Write a question first.");
  if (!answer && !$("useLlm").checked) return toast("Type the assistant's answer, or turn on the real model.");
  $("send").disabled = true; $("send").textContent = "Checking…";
  try {
    const preset = state.scenarios[$("scenario").value];       // an unedited example searches only the policy it was written for
    const categories = preset && preset.answer === answer && preset.prompt === prompt ? preset.categories : null;
    const res = await api("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, llm_answer: $("useLlm").checked ? null : answer,
        use_llm: $("useLlm").checked, role: state.role, user_id: "dr.silva", categories }) });
    state.last = { requestId: res.message.requestId, prompt, answer: res.answer ?? answer };
    state.lastResult = res.message.type === "blocked" ? "withheld" : "clean";
    renderPipeline(res.stages);
    const masked = res.masking.tokens.length ? `<small>Privacy check replaced ${res.masking.tokens.length} identifier(s): ${esc(res.masking.masked_prompt)}</small>` : "";
    bubble("user", `${esc(prompt)}${masked}`);
    if (res.message.type === "blocked") {
      bubble("blocked", `<b>Answer withheld</b><br>${esc(res.withheld_text)}<small>Reference: ${esc(res.message.requestId)}</small>`);
    } else {
      bubble("bot is-clean", `${esc(res.message.content)}<small>✓ Checked against hospital policy · ${esc(res.message.requestId)}</small>`);
    }
    $("rescan").disabled = false;
    if (state.role === "admin" && res.admin) renderAdmin(res.admin, res.message.requestId);
    else $("adminPanel").hidden = true;
    return res;
  } catch (e) { fail(e); } finally { $("send").disabled = false; $("send").textContent = "Check and send"; }
}
$("send").onclick = () => send();

/* ---------- admin explanation ---------- */
function latencyBar(b) {
  const parts = Object.entries(b || {}).filter(([, v]) => v > 0);
  const total = parts.reduce((a, [, v]) => a + v, 0) || 1;
  if (!parts.length) return "";                                          // a reopened result has no timing breakdown
  const slow = parts.reduce((m, [, v]) => Math.max(m, v), 0);
  const cls = (v) => (v === slow ? "lat-slow" : "lat-step");           // the slowest step stands out, the rest stay quiet
  return `<div class="lat" role="img" aria-label="Time spent in each step">${parts.map(([k, v]) =>
    `<i class="${cls(v)}" style="width:${(v / total) * 100}%" title="${esc(k)} ${v} ms"></i>`).join("")}</div>
    <div class="legend">${parts.map(([k, v]) => `<span><span class="sw ${cls(v)}"></span>${esc(k.replace(/_/g, " "))} ${v} ms</span>`).join("")}</div>`;
}
function renderAdmin(v, requestId) {
  const bad = v.c3_verdict === "VIOLATION";
  const gen = v.generation || {};
  const cites = (v.cited_clauses || []).map((c) => `<div class="cite"><b>${esc(c.clause_id)}</b> · ${esc(c.title)}
      <small class="muted"> · ${[c.doc_name, `rank ${c.retrieval_rank}`, c.citation_status].filter(Boolean).map(esc).join(" · ")}</small>
      <q>${esc(c.quote)}</q></div>`).join("");
  const leak = v.leak_check && v.leak_check.entities && v.leak_check.entities.length
    ? `<p><span class="badge bad">Identifier leak</span> ${v.leak_check.entities.map((e) => esc(e.type)).join(", ")} found in the answer.</p>` : "";
  const meter = gen.p_violation != null ? `<div class="meter" role="img" aria-label="Probability of violation ${pct(gen.p_violation)}, threshold ${pct(gen.tau)}" title="probability of violation"><i style="width:${pct(gen.p_violation)}"></i><u style="left:${pct(gen.tau)}" title="threshold"></u></div>
      <small class="muted">Model confidence ${pct(gen.p_violation)} · threshold ${pct(gen.tau)}${gen.decoded ? "" : " · no explanation needed"}</small>` : "";
  $("adminPanel").classList.toggle("is-bad", bad); $("adminPanel").classList.toggle("is-ok", !bad);
  $("adminVerdict").innerHTML = `<span class="badge solid ${bad ? "bad" : "ok"}">${bad ? "WITHHELD" : "ALLOWED"}</span>
      <span class="badge ${v.assurance === "low" ? "warn" : "ok"}">assurance ${esc(v.assurance || "n/a")}</span>
      <span class="muted">decided by ${esc((v.decision_source || "").replace(/_/g, " "))} · ${esc(v.latency_ms)} ms</span>`;
  $("adminBody").innerHTML = `
    ${v.error_code ? `<p class="badge warn">Check failed (${esc(v.error_code)}): the answer is withheld and no event is sent.</p>` : ""}
    ${meter}${leak}
    ${bad && cites ? `<h3>Rule broken</h3>${cites}<p><b>Reason:</b> ${esc(v.rationale)}</p>` : bad ? "" : "<p>No policy rule was broken.</p>"}
    ${latencyBar(v.latency_breakdown_ms)}
    <div class="facts"><div class="fact"><small>Policy version</small><b>${esc((v.policy_index_version || "").slice(-9))}</b></div>
      <div class="fact"><small>Model</small><b>${esc(v.model_version || "")}</b></div>
      <div class="fact"><small>Reference</small><b>${esc(requestId)}</b></div></div>
    <div class="fb"><span class="muted">Was this result right?</span>
      <button data-fb="agree">Yes</button><button data-fb="disagree">No</button><span id="fbDone" class="muted"></span></div>`;
  $("adminPanel").hidden = false;
  document.querySelectorAll("[data-fb]").forEach((b) => (b.onclick = async () => {
    const right = b.dataset.fb === "agree";
    try {
      await api("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: requestId, c3_verdict: v.c3_verdict, answer_text: state.last?.answer || "",
          judged_violation: right ? bad : !bad }) });
      $("fbDone").textContent = "Saved. Thank you."; }
    catch (e) { fail(e); }
  }));
}
async function showTrace(requestId) {
  try {
    const t = await api(`/api/admin/trace/${encodeURIComponent(requestId)}`);
    renderAdmin({ c3_verdict: t.verdict, assurance: t.assurance, decision_source: t.decision_source,
      latency_ms: t.latency_total_ms, error_code: t.error_code, rationale: t.rationale,
      generation: { p_violation: t.confidence, tau: t.tau, decoded: !!t.decoded },
      cited_clauses: (t.cited_clauses || []).map((c) => ({ ...c, doc_name: "" })),
      policy_index_version: t.policy_index_version, model_version: t.model_version,
      leak_check: { entities: Array(t.leaked_pii_count || 0).fill({ type: "identifier" }) } }, requestId);
  } catch (e) { fail(e); }
}

/* ---------- policies ---------- */
const chipFor = (s) => ({ active: "ok", processing: "warn", needs_attention: "bad", removed: "" }[s] || "");
async function loadPolicies() {
  try {
    const { documents, status } = await api("/api/admin/policies");
    state.docs = documents;
    const names = { ready: "Ready", updating: "Updating…", empty: "No policies yet", attention: "Needs attention" };
    $("polStatus").innerHTML = `<b>${esc(names[status.state] || status.state)}</b> · ${status.documents} document(s) · ${status.clauses} clauses · version <code>${esc(status.index_version.slice(-9))}</code>`;
    $("docTable").querySelector("tbody").innerHTML = documents.length ? documents.map((d) => `<tr>
      <td class="namecell"><b>${esc(d.name)}</b><br><small class="muted">${esc(d.filename)}</small>${d.status_message ? `<small class="warnnote clamp" title="${esc(d.status_message)}">${esc(d.status_message)}</small>` : ""}</td>
      <td><code>${esc(d.doc_code)}</code></td><td><span class="badge ${chipFor(d.status)}">${esc(d.status.replace("_", " "))}</span></td>
      <td>${d.clause_count}</td><td>v${d.version}</td><td>${esc(d.uploaded_at.slice(11, 19))}</td>
      <td class="actions"><button data-act="clauses" data-id="${esc(d.doc_id)}">Clauses</button>
        <button data-act="edit" data-id="${esc(d.doc_id)}">Edit</button>
        <label class="filebtn">Replace<input type="file" data-act="replace" data-id="${esc(d.doc_id)}" accept=".pdf,.md,.markdown"></label>
        <button data-act="delete" data-id="${esc(d.doc_id)}">Delete</button></td></tr>`).join("")
      : `<tr><td colspan="7" class="muted">No policy documents. Load one above.</td></tr>`;
    if (documents.some((d) => d.status === "processing")) setTimeout(loadPolicies, 600);
  } catch (e) { fail(e); }
}
$("docTable").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-act]"); if (!b) return;
  const id = b.dataset.id;
  if (b.dataset.act === "clauses") showClauses(id);
  if (b.dataset.act === "edit") openEditor(id);
  if (b.dataset.act === "delete" && confirm("Remove this policy document?")) {
    try { await api(`/api/admin/policies/${id}`, { method: "DELETE" }); toast("Removed. Checks stop using it now."); loadPolicies(); $("clauseCard").hidden = true; $("editCard").hidden = true; }
    catch (e) { fail(e); }
  }
});
$("docTable").addEventListener("change", async (ev) => {
  const inp = ev.target.closest("input[data-act=replace]"); if (!inp || !inp.files[0]) return;
  const fd = new FormData(); fd.append("file", inp.files[0]);
  timedChange(() => api(`/api/admin/policies/${inp.dataset.id}`, { method: "PUT", body: fd }), "Replaced");
  inp.value = "";
});
$("uploadFile").onchange = async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  timedChange(() => api("/api/admin/policies", { method: "POST", body: fd }), "Uploaded");
  ev.target.value = "";
};
$("loadDemo").onclick = () => timedChange(() => api(`/api/admin/load-demo?which=${$("demoPick").value}`, { method: "POST" }), "Loaded");

async function timedChange(action, verb) {
  const t0 = performance.now();
  $("polMsg").textContent = `${verb}… reading the document and updating the index.`;
  try {
    const doc = await action();
    await loadPolicies();
    for (let i = 0; i < 400; i++) {                                  // poll until the change is live
      const d = await api(`/api/admin/policies/${doc.doc_id}`);
      if (d.status !== "processing") {
        const secs = ((performance.now() - t0) / 1000).toFixed(2);
        const ok = d.status === "active";
        $("polMsg").textContent = ok ? `${verb}: ${d.clause_count} clauses, enforced in ${secs} s. No restart.` : `Problem: ${d.status_message || d.status}. The previous version stays live.`;
        $("enforce").textContent = ok ? `live in ${secs} s` : "";
        toast($("polMsg").textContent, 5000);
        await loadPolicies(); if (state.doc === doc.doc_id) { showClauses(doc.doc_id); }
        return;
      }
      await new Promise((r) => setTimeout(r, 150));
    }
  } catch (e) { $("polMsg").textContent = ""; fail(e); }
}
async function showClauses(id) {
  state.doc = id;
  try {
    const cl = await api(`/api/admin/policies/${id}/clauses`);
    $("clauseCard").hidden = false;
    $("clauseTitle").textContent = `Clauses (${cl.length})`;
    const render = () => {
      const q = $("clauseFilter").value.toLowerCase();
      $("clauseList").innerHTML = cl.filter((c) => !q || (c.clause_id + c.title + c.text).toLowerCase().includes(q)).slice(0, 150)
        .map((c) => `<div class="cl"><code>${esc(c.clause_id)}</code> <b>${esc(c.title)}</b> <span class="badge ${c.severity === "high" ? "bad" : c.severity === "medium" ? "warn" : "ok"}">${esc(c.severity)}</span><br><span class="muted">${esc(c.text.slice(0, 220))}${c.text.length > 220 ? "…" : ""}</span></div>`).join("");
    };
    $("clauseFilter").oninput = render; render();
  } catch (e) { fail(e); }
}
async function openEditor(id) {
  try {
    const m = await api(`/api/admin/policies/${id}/markdown`);
    state.doc = id; state.editName = m.filename;
    $("editCard").hidden = false; $("mdEditor").value = m.markdown; showClauses(id);
    $("editCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) { fail(e); }
}
$("saveMd").onclick = () => timedChange(() => api(`/api/admin/policies/${state.doc}/markdown`, { method: "PUT",
  headers: { "Content-Type": "application/json" }, body: JSON.stringify({ markdown: $("mdEditor").value, filename: state.editName || "policy.md" }) }), "Saved");
$("rescan").onclick = async () => {
  if (!state.last) return toast("Send a message in the Chat tab first.");
  const before = state.lastResult;
  document.querySelector('#tabs button[data-tab="chat"]').click();
  const res = await send({ prompt: state.last.prompt, answer: state.last.answer });
  if (res) {
    const now = res.message.type === "blocked" ? "withheld" : "clean";
    toast(before === now ? `Same result as before: ${now}.` : `Result changed: ${before} → ${now}. The edit took effect.`, 6000);
  }
};

/* ---------- behaviour ---------- */
async function loadEvents() {
  try {
    const { events, users } = await api("/api/events");
    $("userTable").querySelector("tbody").innerHTML = users.length ? users.map((u) => `<tr><td>${esc(u.user_id)}</td><td>${u.requests}</td><td>${u.violations}</td><td>${u.leaks}</td><td><span class="badge ${u.violations >= 3 ? "bad" : u.violations ? "warn" : "ok"}">${esc(u.level)}</span></td></tr>`).join("") : `<tr><td colspan="5" class="muted">Nothing yet.</td></tr>`;
    $("eventTable").querySelector("tbody").innerHTML = events.length ? events.map((e) => `<tr><td>${esc(e.event_timestamp.slice(11, 19))}</td><td>${esc(e.user_id)}</td><td><span class="badge ${e.c3_verdict === "CLEAN" ? "ok" : "bad"}">${esc(e.c3_verdict)}</span></td><td>${esc(e.clause_id ?? "—")}</td><td>${e.leaked_pii_count}</td></tr>`).join("") : `<tr><td colspan="5" class="muted">No events yet.</td></tr>`;
  } catch (e) { fail(e); }
}
$("resetEvents").onclick = async () => { await api("/api/events/reset", { method: "POST" }); loadEvents(); };

/* ---------- start ---------- */
(async () => {
  await Promise.all([loadScenarios(), refreshStatus()]);
  setInterval(refreshStatus, 10000);
  setRole("clinician");
  try { const p = await api("/api/admin/policies"); if (!p.documents.length) toast("No policy loaded yet. Open Policies and load one.", 7000); } catch (_) { /* shown in chips */ }
})();
