/* C4 dashboard.
   One HTML page, no framework. The address after the # decides which page is shown
   (#/overview, #/user/user_005, #/access, #/rules, #/demo). Data comes from the C4 server
   under /ui/api. The page asks for fresh data every few seconds, so a block or an attack
   shows up without pressing refresh. */

"use strict";

const ORG = new URLSearchParams(location.search).get("org") || "org_001";
const POLL_MS = 2500;

const PRIORITY_NAME = { P1: "Critical", P2: "High", P3: "Medium", P4: "Low" };
const ADVICE = {
  BLOCK_RECOMMENDED: "C4 recommends blocking this user. The score is in the critical range.",
  CLOSE_MONITORING: "Watch this user closely. The score is high but not critical.",
  WATCH: "Keep an eye on this user. Some suspicious activity was seen.",
  NONE: "No action needed. This user looks normal.",
};

// What each dummy user does (the 7 scenarios in c4/demo/scenarios.py)
const DEMO_USERS = [
  ["user_001", "Careful clinician", "14 normal working days, nothing suspicious."],
  ["user_002", "Careless power user", "Many requests with a lot of personal data masked, but nothing hostile."],
  ["user_003", "Curious tester", "Four blocked prompts on one day, then normal work."],
  ["user_004", "Gradual escalator", "Suspicious events grow a little every day for a week."],
  ["user_005", "Burst attacker", "Six blocked prompts and two leaks within 40 minutes."],
  ["user_006", "Slow and low", "One blocked prompt every three days for a month."],
  ["user_007", "Compromised account", "Normal for 20 days, then a sudden evening attack."],
];

let rules = null;            // scoring rules, loaded once (used to draw the meter ticks)
let page = { name: "", arg: "" };
let lastData = "";           // last response as text, to skip redrawing when nothing changed
let clockOffsetMs = 0;       // how far the server clock is moved forward (Demo tools > Time)
let timelineShown = 20;      // how many timeline rows are visible on the user page
let filters = { q: "", priority: "", status: "" };

const $ = (sel) => document.querySelector(sel);

// Names and reasons come from the database, so they are escaped before going into HTML.
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, options = {}) {
  const url = `/ui/api${path}${path.includes("?") ? "&" : "?"}org_id=${encodeURIComponent(ORG)}`;
  const res = await fetch(url, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* an empty body is fine */ }
  return { ok: res.ok, status: res.status, data };
}

/* ---------- small helpers ---------- */

function toast(message, bad = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.className = "toast"), 3000);
}

function serverNow() {
  return Date.now() + clockOffsetMs;
}

// Remember the server clock from every response and show a chip when time has been moved.
function syncClock(clock) {
  if (!clock) return;
  clockOffsetMs = clock.offset_hours * 3600 * 1000;
  const chip = $("#clockChip");
  const days = clock.offset_hours / 24;
  chip.hidden = clock.offset_hours === 0;
  chip.textContent = `Time moved +${Number.isInteger(days) ? days : days.toFixed(1)} days`;
}

function ago(iso) {
  if (!iso) return "never";
  // serverNow() = browser time + the offset set on Demo tools, so "ago" matches the scores
  const minutes = Math.max(0, Math.round((serverNow() - new Date(iso)) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function when(iso) {
  return new Date(iso).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function priorityLabel(p) {
  return `<span class="dot c-${p}"></span> ${PRIORITY_NAME[p]}`;
}

// The risk meter: a short bar filled to the score, with ticks where the priority levels start.
function meter(score, priority) {
  const t = rules ? rules.priorities : { P1: 80, P2: 60, P3: 30 };
  const ticks = [t.P3, t.P2, t.P1].map((v) => `<s style="left:${v}%"></s>`).join("");
  return `<div class="meter" title="Priority levels start at ${t.P3}, ${t.P2} and ${t.P1}">${ticks}<i class="c-${priority}" style="width:${Math.min(100, score)}%"></i></div>`;
}

function statusPill(status) {
  return `<span class="pill status-${status}">${status === "blocked" ? "Blocked" : "Active"}</span>`;
}

/* ---------- decision dialog (block / unblock) ---------- */

let pendingDecision = null;

function openDecision(userId, action, suggestion = "") {
  pendingDecision = { userId, action };
  const blocking = action === "block";
  $("#decisionTitle").textContent = blocking ? `Block ${userId}?` : `Unblock ${userId}?`;
  $("#decisionText").textContent = blocking
    ? "C4 adds this user to the block list. C1 stops their requests as soon as it reads the list."
    : "C4 removes this user from the block list. Their requests are allowed again.";
  $("#decisionOk").textContent = blocking ? "Block user" : "Unblock user";
  $("#decisionOk").className = blocking ? "btn danger" : "btn";
  $("#decisionReason").value = suggestion;
  $("#decision").showModal();
}

$("#decisionCancel").addEventListener("click", () => $("#decision").close());

$("#decisionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const { userId, action } = pendingDecision;
  const reason = $("#decisionReason").value.trim();
  if (!reason) return;
  const res = await api(`/users/${encodeURIComponent(userId)}/${action}`, {
    method: "POST",
    body: { by: "admin_demo", reason },
  });
  $("#decision").close();
  if (res.ok) {
    toast(action === "block" ? `${userId} is now blocked` : `${userId} is unblocked`);
    tick(true);
  } else {
    toast("The decision could not be saved", true);
  }
});

/* ---------- charts (plain SVG strings) ---------- */

function hourChart(hist) {
  const max = Math.max(1, ...hist);
  const bars = hist.map((n, h) => {
    const height = (n / max) * 80;
    return `<rect x="${12 + h * 19}" y="${90 - height}" width="14" height="${height}" style="fill:var(--ink)"><title>${String(h).padStart(2, "0")}:00 UTC, ${n} requests</title></rect>`;
  }).join("");
  const labels = [0, 6, 12, 18, 23].map((h) => `<text x="${19 + h * 19}" y="108" text-anchor="middle">${String(h).padStart(2, "0")}</text>`).join("");
  return `<svg class="chart" viewBox="0 0 480 116" role="img" aria-label="Requests by hour of day">
    <line x1="8" x2="472" y1="90" y2="90" stroke="#e4e4e2"/>${bars}${labels}</svg>`;
}

function sessionChart(sessions) {
  const shown = sessions.slice(-14);
  if (!shown.length) return `<p class="muted">No sessions yet.</p>`;
  const max = Math.max(1, ...shown.map((s) => s.suspicious_events));
  const step = 34;
  const bars = shown.map((s, i) => {
    const height = (s.suspicious_events / max) * 70;
    const x = 14 + i * step;
    const colour = s.suspicious_events ? "var(--p1)" : "#d6d6d2";
    const day = new Date(s.first_seen).toLocaleDateString([], { day: "2-digit", month: "short" });
    return `<rect x="${x}" y="${84 - Math.max(height, 2)}" width="22" height="${Math.max(height, 2)}" style="fill:${colour}"><title>${esc(s.session_id)}: ${s.suspicious_events} suspicious events</title></rect>
      <text x="${x + 11}" y="${78 - Math.max(height, 2)}" text-anchor="middle">${s.suspicious_events || ""}</text>
      <text x="${x + 11}" y="100" text-anchor="middle">${day}</text>`;
  }).join("");
  // a short chart is padded to 480 wide, otherwise the browser would stretch it and blow up the text
  const width = Math.max(480, shown.length * step + 20);
  return `<svg class="chart" viewBox="0 0 ${width} 108" role="img" aria-label="Suspicious events per session">
    <line x1="8" x2="${width - 8}" y1="84" y2="84" stroke="#e4e4e2"/>${bars}</svg>`;
}

function scoreChart(timelineNewestFirst, nowScore) {
  const points = [...timelineNewestFirst].reverse();   // oldest first for drawing
  // Add the score as it is now, so time decay shows as a falling line after the last event
  const last = points[points.length - 1];
  if (last && serverNow() - new Date(last.event_timestamp) > 60000) {
    points.push({ event_timestamp: new Date(serverNow()).toISOString(), score_after: nowScore, rules_fired: [] });
  }
  if (points.length < 2) return `<p class="muted">Not enough events to draw a line yet.</p>`;
  const W = 720, H = 210, left = 34, right = 70, top = 10, bottom = 26;
  const t0 = new Date(points[0].event_timestamp).getTime();
  const t1 = new Date(points[points.length - 1].event_timestamp).getTime();
  const x = (iso) => left + ((new Date(iso).getTime() - t0) / Math.max(1, t1 - t0)) * (W - left - right);
  const y = (score) => top + (1 - score / 100) * (H - top - bottom);

  const levels = rules ? rules.priorities : { P1: 80, P2: 60, P3: 30 };
  const guides = [["P1", levels.P1], ["P2", levels.P2], ["P3", levels.P3]].map(([p, v]) =>
    `<line x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}" stroke="#d6d6d2" stroke-dasharray="3 4"/>
     <text x="${W - right + 6}" y="${y(v) + 4}">${PRIORITY_NAME[p]} ${v}</text>`).join("");

  const line = points.map((p) => `${x(p.event_timestamp).toFixed(1)},${y(p.score_after).toFixed(1)}`).join(" ");
  const hits = points.filter((p) => p.rules_fired.length).map((p) => {
    const text = p.rules_fired.map((r) => `${r.rule_id} +${r.impact}`).join(", ");
    return `<circle cx="${x(p.event_timestamp).toFixed(1)}" cy="${y(p.score_after).toFixed(1)}" r="3.5" style="fill:var(--p1)"><title>${esc(when(p.event_timestamp))}: ${esc(text)}, score ${p.score_after}</title></circle>`;
  }).join("");

  // three date labels along the bottom: start, middle, end
  const ticks = [0, 0.5, 1].map((f) => {
    const date = new Date(t0 + f * (t1 - t0));
    // a short span (under 36 hours) gets clock times, otherwise the three labels would all say the same day
    const label = t1 - t0 < 36 * 3600 * 1000
      ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : date.toLocaleDateString([], { day: "2-digit", month: "short" });
    return `<text x="${left + f * (W - left - right)}" y="${H - 6}" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${label}</text>`;
  }).join("");

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Risk score over time">
    <text x="${left - 6}" y="${y(100) + 4}" text-anchor="end">100</text>
    <text x="${left - 6}" y="${y(0) + 4}" text-anchor="end">0</text>
    <line x1="${left}" x2="${W - right}" y1="${y(0)}" y2="${y(0)}" stroke="#e4e4e2"/>
    ${guides}
    <polyline points="${line}" fill="none" stroke="#1b1b1a" stroke-width="1.6" stroke-linejoin="round"/>
    ${hits}${ticks}</svg>
    <p class="small faint">Red dots are events where a rule fired. After the last event the line follows the score as it fades with time.</p>`;
}

/* ---------- page: overview ---------- */

function overviewShell() {
  $("#view").innerHTML = `
    <h1>Users at risk</h1>
    <p class="lead" id="ovSentence">Loading…</p>
    <div id="simBox"></div>
    <div id="ovBand"></div>
    <div class="filters">
      <input type="search" id="fQ" placeholder="Search by user id" aria-label="Search by user id" value="${esc(filters.q)}">
      <select id="fP" aria-label="Filter by priority">
        <option value="">All priorities</option>
        ${["P1", "P2", "P3", "P4"].map((p) => `<option value="${p}" ${filters.priority === p ? "selected" : ""}>${PRIORITY_NAME[p]}</option>`).join("")}
      </select>
      <select id="fS" aria-label="Filter by status">
        <option value="">Active and blocked</option>
        <option value="active" ${filters.status === "active" ? "selected" : ""}>Active only</option>
        <option value="blocked" ${filters.status === "blocked" ? "selected" : ""}>Blocked only</option>
      </select>
    </div>
    <div id="ovTable"></div>`;
  $("#fQ").addEventListener("input", (e) => { filters.q = e.target.value; drawOverview(); });
  $("#fP").addEventListener("change", (e) => { filters.priority = e.target.value; drawOverview(); });
  $("#fS").addEventListener("change", (e) => { filters.status = e.target.value; drawOverview(); });
}

let overviewData = null;

function drawOverview() {
  const data = overviewData;
  if (!data || !$("#ovTable")) return;
  const s = data.summary;

  if (s.total_users === 0) {
    $("#ovSentence").textContent = "C4 has not received any events yet.";
    $("#ovBand").innerHTML = "";
    $("#ovTable").innerHTML = `<div class="empty">No users to show.<br><a class="btn" href="#/demo">Load the demo data</a></div>`;
    return;
  }

  $("#ovSentence").textContent =
    `${s.P1} of ${s.total_users} users are at critical risk` +
    (s.blocked ? `, and ${s.blocked} ${s.blocked === 1 ? "is" : "are"} blocked.` : ". Nobody is blocked yet.");

  $("#ovBand").innerHTML = `
    <div class="band" role="img" aria-label="Users by priority">
      ${["P1", "P2", "P3", "P4"].map((p) => `<i class="c-${p}" style="width:${(s[p] / s.total_users) * 100}%"></i>`).join("")}
    </div>
    <div class="legend">
      ${["P1", "P2", "P3", "P4"].map((p) => `<div><b class="t-${p}">${s[p]}</b><span>${PRIORITY_NAME[p]}</span></div>`).join("")}
      <div><b>${s.blocked}</b><span>Blocked</span></div>
    </div>`;

  const q = filters.q.trim().toLowerCase();
  const rows = data.users.filter((u) =>
    (!q || u.user_id.toLowerCase().includes(q)) &&
    (!filters.priority || u.priority === filters.priority) &&
    (!filters.status || u.status === filters.status));

  $("#ovTable").innerHTML = rows.length ? `
    <table>
      <thead><tr><th>User</th><th>Risk score</th><th>Priority</th><th>Main reason</th><th>Status</th><th class="num">Last activity</th></tr></thead>
      <tbody>${rows.map((u) => `
        <tr class="link" data-user="${esc(u.user_id)}">
          <td><a class="userid" href="#/user/${encodeURIComponent(u.user_id)}">${esc(u.user_id)}</a></td>
          <td><div class="scorecell"><b>${u.score}</b>${meter(u.score, u.priority)}</div></td>
          <td>${priorityLabel(u.priority)}</td>
          <td class="${u.main_reason ? "" : "faint"}">${esc(u.main_reason || "Nothing suspicious")}</td>
          <td>${statusPill(u.status)}</td>
          <td class="num muted">${ago(u.last_activity)}</td>
        </tr>`).join("")}
      </tbody>
    </table>` : `<div class="empty">No user matches these filters.</div>`;

  document.querySelectorAll("#ovTable tr.link").forEach((tr) =>
    tr.addEventListener("click", () => { location.hash = `#/user/${encodeURIComponent(tr.dataset.user)}`; }));

  const sim = data.simulation;
  $("#simBox").innerHTML = sim && sim.running
    ? `<div class="advice warn">Attack simulation running for <b>${esc(sim.user_id)}</b>, step ${sim.step} of ${sim.total}.</div>` : "";
}

/* ---------- page: user ---------- */

function userShell(id) {
  $("#view").innerHTML = `<a class="back" href="#/overview">All users</a><div id="userBody"><p class="muted">Loading ${esc(id)}…</p></div>`;
}

function drawUser(data, id) {
  const box = $("#userBody");
  if (!box) return;
  if (!data) {
    box.innerHTML = `<h1>${esc(id)}</h1><p class="lead">C4 has no record of this user.</p>`;
    return;
  }
  const p = data.profile, b = p.behaviour_summary, blocked = p.status === "blocked";
  const pointsTotal = p.top_contributors.reduce((sum, c) => sum + c.points, 0);
  const maxPoints = Math.max(1, ...p.top_contributors.map((c) => c.points));

  const advice = blocked
    ? `<div class="advice">This user is blocked. C1 refuses their requests.</div>`
    : `<div class="advice ${p.recommendation === "BLOCK_RECOMMENDED" ? "warn" : ""}">${ADVICE[p.recommendation]}</div>`;

  const contributors = p.top_contributors.length
    ? `<div class="bars">${p.top_contributors.map((c) => `
        <div class="barrow">
          <b>${c.rule_id}</b>
          <div class="track"><i style="width:${(c.points / maxPoints) * 100}%"></i></div>
          <span class="pts">${c.points}</span>
          <span class="name">${esc(c.title)}</span>
        </div>`).join("")}</div>
       ${pointsTotal > 100 ? `<p class="small faint">The points add up to ${pointsTotal.toFixed(0)}, but the score stops at 100.</p>` : ""}`
    : `<p class="muted">No rule has fired for this user.</p>`;

  const shown = data.timeline.slice(0, timelineShown);
  const timeline = `
    <table>
      <thead><tr><th>Time</th><th>Check</th><th>What happened</th><th>Rules fired</th><th class="num">Score after</th></tr></thead>
      <tbody>${shown.map((e) => `
        <tr>
          <td class="muted">${when(e.event_timestamp)}</td>
          <td>${esc(e.source_label)}</td>
          <td class="${e.display_severity === "High" ? "sevHigh" : e.display_severity === "Medium" ? "sevMedium" : ""}">${esc(e.event_title)}</td>
          <td>${e.rules_fired.map((r) => `<span class="chip hit" title="${esc(r.title)}">${r.rule_id} +${r.impact}</span>`).join("") || '<span class="faint">None</span>'}</td>
          <td class="num">${e.score_after}</td>
        </tr>`).join("")}
      </tbody>
    </table>
    ${data.timeline.length > timelineShown ? `<p><button class="btn plain small" id="moreRows">Show 20 more</button></p>` : ""}`;

  box.innerHTML = `
    <div class="userhead">
      <div>
        <h1>${esc(p.user_id)} ${statusPill(p.status)}</h1>
        <div class="bigscore"><b class="t-${p.priority}">${p.score}</b><span>out of 100, ${PRIORITY_NAME[p.priority].toLowerCase()} risk</span></div>
        ${meter(p.score, p.priority)}
      </div>
      <div class="actions">
        ${blocked
          ? `<button class="btn plain" id="btnUnblock">Unblock user</button>`
          : `<button class="btn danger" id="btnBlock">Block user</button>`}
      </div>
    </div>
    ${advice}

    <div class="two">
      <section>
        <h2>Why this score</h2>
        ${contributors}
      </section>
      <section>
        <h2>Behaviour in the last 30 days</h2>
        <dl class="facts">
          <dt>Requests</dt><dd>${b.requests}</dd>
          <dt>Sessions</dt><dd>${b.sessions}</dd>
          <dt>Prompts blocked by C2</dt><dd>${b.intent_blocks}</dd>
          <dt>Policy violations found by C3</dt><dd>${b.policy_violations}</dd>
          <dt>Answers that leaked identifiers</dt><dd>${b.leak_events}</dd>
          <dt>Identifiers leaked in total</dt><dd>${b.leaked_items}</dd>
          <dt>High-severity events</dt><dd>${b.high_severity}</dd>
          <dt>Suspicious events at unusual hours</dt><dd>${b.unfamiliar_time_events}</dd>
          <dt>Average masking per request</dt><dd>${b.avg_masking_percentage_30d}%</dd>
        </dl>
      </section>
    </div>

    <h2>Score over time</h2>
    ${scoreChart(data.timeline, p.score)}

    <div class="two">
      <section>
        <h2>Suspicious events per session</h2>
        ${sessionChart(data.sessions)}
        <p class="small faint">C4 follows a user across sessions. A rise from session to session fires rule R7.</p>
      </section>
      <section>
        <h2>Activity by hour of day (UTC)</h2>
        ${hourChart(data.hour_histogram)}
        <p class="small faint">A suspicious event at an hour the user rarely works fires rule R6.</p>
      </section>
    </div>

    <h2>Event timeline</h2>
    ${timeline}`;

  const reason = `Recommended by C4: score ${p.score}, ${PRIORITY_NAME[p.priority].toLowerCase()} risk.`;
  $("#btnBlock")?.addEventListener("click", () => openDecision(p.user_id, "block", reason));
  $("#btnUnblock")?.addEventListener("click", () => openDecision(p.user_id, "unblock", "Reviewed by an admin."));
  $("#moreRows")?.addEventListener("click", () => { timelineShown += 20; drawUser(data, id); });
}

/* ---------- page: access control ---------- */

function accessShell() {
  $("#view").innerHTML = `
    <h1>Access control</h1>
    <p class="lead">C4 decides who is risky and keeps the block list. C1 and the gateway read that list and refuse the users on it.</p>
    <div class="two">
      <section>
        <h2 style="margin-top:0">Block list</h2>
        <div id="blockList"></div>
      </section>
      <section>
        <h2 style="margin-top:0">Test the gate</h2>
        <p class="muted small">This simulates C1: a request arrives from the chosen user and the gate checks the C4 block list.</p>
        <div class="row">
          <select id="gateUser" aria-label="User"></select>
          <button class="btn" id="gateSend">Send request</button>
        </div>
        <div class="result" id="gateResult"><span class="muted">The result of the check appears here.</span></div>
        <h2>Recent attempts</h2>
        <div id="gateLog"></div>
      </section>
    </div>
    <h2>Audit log</h2>
    <div id="auditLog"></div>`;
  $("#gateSend").addEventListener("click", sendThroughGate);
}

async function sendThroughGate() {
  const user = $("#gateUser").value;
  if (!user) return;
  const res = await api("/gate", { method: "POST", body: { user_id: user } });
  const d = res.data || {};
  const blocked = d.decision === "BLOCKED";
  $("#gateResult").className = `result ${blocked ? "blocked" : "allowed"}`;
  $("#gateResult").innerHTML = `<strong>${blocked ? "Blocked" : "Allowed"}</strong>
    <div>${esc(user)}: ${blocked ? "HTTP 403, the request is refused." : "HTTP 200, the request goes on."}</div>
    <div class="small muted">${esc(d.reason || "")}</div>`;
  tick(true);
}

function drawAccess(access, users) {
  if (!$("#blockList")) return;

  const byId = Object.fromEntries(users.map((u) => [u.user_id, u]));
  $("#blockList").innerHTML = access.blocked.length ? `
    <table>
      <thead><tr><th>User</th><th>Score</th><th></th></tr></thead>
      <tbody>${access.blocked.map((id) => `
        <tr><td><a class="userid" href="#/user/${encodeURIComponent(id)}">${esc(id)}</a></td>
            <td>${byId[id] ? byId[id].score : "-"}</td>
            <td class="num"><button class="btn plain small" data-unblock="${esc(id)}">Unblock</button></td></tr>`).join("")}
      </tbody>
    </table>` : `<p class="muted">Nobody is blocked. Open a user with a critical score to block them.</p>`;
  document.querySelectorAll("[data-unblock]").forEach((b) =>
    b.addEventListener("click", () => openDecision(b.dataset.unblock, "unblock", "Reviewed by an admin.")));

  // keep the chosen user while the list refreshes
  const select = $("#gateUser");
  const current = select.value;
  select.innerHTML = users.map((u) => `<option value="${esc(u.user_id)}">${esc(u.user_id)} (${PRIORITY_NAME[u.priority]}${u.status === "blocked" ? ", blocked" : ""})</option>`).join("");
  if (current) select.value = current;

  $("#gateLog").innerHTML = access.gate_log.length ? `
    <table><tbody>${access.gate_log.slice(0, 8).map((g) => `
      <tr><td class="muted">${esc(g.at)}</td><td>${esc(g.user_id)}</td>
          <td class="${g.decision === "BLOCKED" ? "t-P1" : "t-P4"}">${g.decision === "BLOCKED" ? "Blocked" : "Allowed"}</td></tr>`).join("")}
    </tbody></table>` : `<p class="muted small">No requests sent yet.</p>`;

  $("#auditLog").innerHTML = access.actions.length ? `
    <table>
      <thead><tr><th>Time</th><th>User</th><th>Decision</th><th>By</th><th>Reason</th></tr></thead>
      <tbody>${access.actions.map((a) => `
        <tr><td class="muted">${when(a.at)}</td><td>${esc(a.user_id)}</td>
            <td>${a.action === "block" ? "Blocked" : "Unblocked"}</td><td>${esc(a.by)}</td><td>${esc(a.reason)}</td></tr>`).join("")}
      </tbody>
    </table>` : `<p class="muted">No block or unblock decision has been made yet.</p>`;
}

/* ---------- page: scoring rules ---------- */

function decayChart(halfLife) {
  const days = 14, W = 480, H = 170, left = 36, right = 12, top = 10, bottom = 26;
  const x = (d) => left + (d / days) * (W - left - right);
  const y = (pct) => top + (1 - pct / 100) * (H - top - bottom);
  const pts = [];
  for (let d = 0; d <= days; d += 0.25) pts.push(`${x(d).toFixed(1)},${y(100 * Math.pow(0.5, (d * 24) / halfLife)).toFixed(1)}`);
  const halfDays = halfLife / 24;
  const marks = [1, 2, 3, 4].filter((k) => k * halfDays <= days).map((k) =>
    `<circle cx="${x(k * halfDays)}" cy="${y(100 / Math.pow(2, k))}" r="3.5" style="fill:var(--ink)"><title>After ${k * halfLife} hours: ${(100 / Math.pow(2, k)).toFixed(1)}%</title></circle>`).join("");
  const xTicks = [0, 3, 6, 9, 12].map((d) => `<text x="${x(d)}" y="${H - 6}" text-anchor="middle">${d}</text>`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Weight of a rule hit as it ages">
    <line x1="${left}" x2="${W - right}" y1="${y(0)}" y2="${y(0)}" stroke="#e4e4e2"/>
    <line x1="${left}" x2="${W - right}" y1="${y(50)}" y2="${y(50)}" stroke="#d6d6d2" stroke-dasharray="3 4"/>
    <text x="${left - 6}" y="${y(100) + 4}" text-anchor="end">100%</text>
    <text x="${left - 6}" y="${y(50) + 4}" text-anchor="end">50%</text>
    <text x="${left - 6}" y="${y(0) + 4}" text-anchor="end">0%</text>
    <polyline points="${pts.join(" ")}" fill="none" stroke="#1b1b1a" stroke-width="1.6"/>
    ${marks}${xTicks}
    <text x="${W - right}" y="${H - 6}" text-anchor="end">days old</text></svg>`;
}

async function rulesPage() {
  const res = await api("/rules");
  if (!res.ok) { $("#view").innerHTML = `<p class="muted">The rules could not be loaded.</p>`; return; }
  const r = res.data;
  $("#view").innerHTML = `
    <h1>How the score works</h1>
    <p class="lead">Every rule that fires adds points. Older points count less, and the total never goes above 100.
      Every number here is read from <code>configs/rules.yaml</code>, not typed into the code.</p>

    <h2 style="margin-top:0">Rules</h2>
    <table>
      <thead><tr><th>Rule</th><th>Name</th><th>Fires when</th><th class="num">Points</th></tr></thead>
      <tbody>${r.rules.map((x) => `<tr><td><b>${x.id}</b></td><td>${esc(x.title)}</td><td class="muted">${esc(x.description)}</td><td class="num">${x.impact}</td></tr>`).join("")}</tbody>
    </table>

    <div class="two">
      <section>
        <h2>Time decay</h2>
        <p class="muted">A hit loses half its weight every ${r.half_life_hours} hours (${(r.half_life_hours / 24).toFixed(0)} days).
          Hits older than ${r.window_days} days are ignored. A user who stops misbehaving slowly returns to a low score.</p>
        ${decayChart(r.half_life_hours)}
      </section>
      <section>
        <h2>Priority levels</h2>
        <table>
          <thead><tr><th>Priority</th><th>Score from</th><th>C4 recommends</th></tr></thead>
          <tbody>
            <tr><td>${priorityLabel("P1")}</td><td>${r.priorities.P1}</td><td>Block the user</td></tr>
            <tr><td>${priorityLabel("P2")}</td><td>${r.priorities.P2}</td><td>Monitor closely</td></tr>
            <tr><td>${priorityLabel("P3")}</td><td>${r.priorities.P3}</td><td>Watch</td></tr>
            <tr><td>${priorityLabel("P4")}</td><td>0</td><td>Nothing</td></tr>
          </tbody>
        </table>
        <p class="small faint">C4 only recommends. A person decides whether to block.</p>
      </section>
    </div>`;
}

/* ---------- page: demo tools ---------- */

function demoShell() {
  $("#view").innerHTML = `
    <h1>Demo tools</h1>
    <p class="lead">Use these while real events from C1, C2 and C3 are not connected yet. They only work on dummy data.</p>

    <div class="tool">
      <div><h3>Load demo data</h3>
        <p>Replaces everything with 7 dummy users and about 1,800 events. Each user follows a different behaviour pattern.</p></div>
      <button class="btn" id="btnLoad">Load demo data</button>
    </div>

    <div class="tool">
      <div><h3>Live attack</h3>
        <p>Sends a short run of hostile events for one user through the normal event path. Watch the score climb on the Overview page.</p>
        <div class="row" style="margin-top:10px">
          <input type="text" id="attackUser" value="user_live" aria-label="User id" size="16">
        </div>
        <div class="progress" id="attackBar" hidden><i style="width:0"></i></div>
        <p class="small" id="attackText"></p></div>
      <button class="btn" id="btnAttack">Start attack</button>
    </div>

    <div class="tool">
      <div><h3>Move time forward</h3>
        <p>Shows time decay. Scores fade as days pass, so this moves the clock the scores are calculated with. No stored data changes.</p>
        <div class="timebox">
          <b id="timeNow"></b>
          <div class="row">
            <button class="btn plain small" data-hours="24">+1 day</button>
            <button class="btn plain small" data-hours="72">+3 days</button>
            <button class="btn plain small" data-hours="168">+7 days</button>
            <button class="btn plain small" data-hours="720">+30 days</button>
            <button class="btn plain small" id="btnBackNow">Back to now</button>
          </div>
        </div></div>
    </div>

    <div class="tool">
      <div><h3>Reset</h3><p>Deletes all users, events and decisions. The page returns to an empty state.</p></div>
      <button class="btn plain" id="btnReset">Delete all data</button>
    </div>

    <h2>The 7 demo users</h2>
    <table>
      <thead><tr><th>User</th><th>Pattern</th><th>What it does</th></tr></thead>
      <tbody>${DEMO_USERS.map(([id, name, text]) => `<tr><td><a class="userid" href="#/user/${id}">${id}</a></td><td>${name}</td><td class="muted">${text}</td></tr>`).join("")}</tbody>
    </table>`;

  $("#btnLoad").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Loading…";
    const res = await api("/demo/load", { method: "POST" });
    e.target.disabled = false; e.target.textContent = "Load demo data";
    if (res.ok) toast(`Loaded ${res.data.users} users and ${res.data.events} events`);
    else toast("Loading failed", true);
  });

  document.querySelectorAll("[data-hours]").forEach((b) =>
    b.addEventListener("click", async () => {
      const res = await api("/demo/time", { method: "POST", body: { add_hours: Number(b.dataset.hours) } });
      if (res.ok) { syncClock(res.data); toast(`Clock moved to ${when(res.data.now)}`); tick(true); }
    }));
  $("#btnBackNow").addEventListener("click", async () => {
    const res = await api("/demo/time", { method: "POST", body: { reset: true } });
    if (res.ok) { syncClock(res.data); toast("Back to the real time"); tick(true); }
  });

  $("#btnReset").addEventListener("click", async () => {
    if (!confirm("Delete all C4 data? This cannot be undone.")) return;
    const res = await api("/demo/reset", { method: "POST" });
    toast(res.ok ? "All data deleted" : "Reset failed", !res.ok);
  });

  $("#btnAttack").addEventListener("click", async () => {
    const user = $("#attackUser").value.trim();
    if (!/^[A-Za-z0-9_.:-]+$/.test(user)) { toast("Use letters, digits and _ - . : only", true); return; }
    const res = await api("/demo/attack", { method: "POST", body: { user_id: user } });
    if (res.ok) toast(`Attack started for ${user}`);
    else toast(res.status === 409 ? "An attack is already running" : "Could not start the attack", true);
    tick(true);
  });
}

function drawDemo(data) {
  if (!$("#attackBar")) return;
  const c = data.clock;
  $("#timeNow").textContent = c.offset_hours === 0
    ? "The clock shows the real time."
    : `Dashboard time: ${when(c.now)} (+${(c.offset_hours / 24).toFixed(1)} days)`;
  const sim = data.simulation;
  const bar = $("#attackBar");
  if (sim && sim.user_id) {
    bar.hidden = false;
    bar.firstElementChild.style.width = `${(sim.step / sim.total) * 100}%`;
    const row = data.users.find((u) => u.user_id === sim.user_id);
    $("#attackText").innerHTML = sim.running
      ? `Step ${sim.step} of ${sim.total}. Score of ${esc(sim.user_id)}: <b>${row ? row.score : 0}</b>`
      : `Finished. ${esc(sim.user_id)} ended at <b>${row ? row.score : 0}</b> (${row ? PRIORITY_NAME[row.priority] : ""}). <a href="#/user/${encodeURIComponent(sim.user_id)}">Open the user</a>`;
  }
  $("#btnAttack").disabled = Boolean(sim && sim.running);
}

/* ---------- routing and refresh ---------- */

async function route() {
  const hash = location.hash.replace(/^#\/?/, "") || "overview";
  const [name, ...rest] = hash.split("/");
  page = { name, arg: decodeURIComponent(rest.join("/")) };
  lastData = "";
  timelineShown = 20;

  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("on", a.dataset.page === name || (name === "user" && a.dataset.page === "overview")));
  window.scrollTo(0, 0);

  if (!rules) {
    const res = await api("/rules");
    if (res.ok) rules = res.data;
  }
  if (name === "user") userShell(page.arg);
  else if (name === "access") accessShell();
  else if (name === "rules") { await rulesPage(); return; }
  else if (name === "demo") demoShell();
  else { page.name = "overview"; overviewShell(); }
  tick(true);
}

// Ask the server for fresh data and redraw only when something really changed.
async function tick(force = false) {
  if (document.hidden && !force) return;
  const stamp = page.name;
  try {
    if (page.name === "overview" || page.name === "demo") {
      const res = await api("/overview");
      if (page.name !== stamp || !res.ok) return;
      const text = JSON.stringify(res.data);
      if (!force && text === lastData) return;
      lastData = text;
      syncClock(res.data.clock);
      if (page.name === "demo") drawDemo(res.data);
      else { overviewData = res.data; drawOverview(); }
    } else if (page.name === "user") {
      const res = await api(`/users/${encodeURIComponent(page.arg)}`);
      if (page.name !== stamp) return;
      const text = JSON.stringify(res.data);
      if (!force && text === lastData) return;
      lastData = text;
      if (res.ok) syncClock(res.data.clock);
      drawUser(res.ok ? res.data : null, page.arg);
    } else if (page.name === "access") {
      const [access, overview] = await Promise.all([api("/access"), api("/overview")]);
      if (page.name !== stamp || !access.ok || !overview.ok) return;
      const text = JSON.stringify([access.data, overview.data.users]);
      if (!force && text === lastData) return;
      lastData = text;
      syncClock(overview.data.clock);
      drawAccess(access.data, overview.data.users);
    }
  } catch (err) {
    // The server may be restarting. The next tick tries again.
    console.warn("refresh failed", err);
  }
}

window.addEventListener("hashchange", route);
setInterval(() => tick(false), POLL_MS);
route();
