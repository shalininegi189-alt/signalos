/* SignalOS front end - core: API helper, tabs, polling, live map, command centre. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; t.setAttribute("role", "status");
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}
const nodeInfo = (id) => (App.topo?.nodes || []).find((n) => n.id === id) || { id, name: id, short: id, zone: "", note: "", maps: "#" };
const nodeName = (id) => nodeInfo(id).name;
const roadName = (id) => { const r = (App.topo?.roads || []).find((x) => x.id === id); return r ? r.label : id; };
const ZONE_COLOR = { hub: "#1f5fbf", school: "#8a63d2", gateway: "#c26a3d", tourist: "#16a0a0", commercial: "#7b8790", residential: "#26a96c" };
const fmt = (v, d = 1) => (v === null || v === undefined ? "-" : Number(v).toFixed(d).replace(/\.0+$/, ""));

const App = { state: null, topo: null, selected: "1-2", listeners: [], tab: "command", ready: false };
const LANE_COLOR = { N: "#3b82c4", E: "#8a63d2", S: "#16a0a0", W: "#c26a3d" };
const POLICY_SHORT = { FCFS: "FCFS", RR: "Round robin", SRTF: "SRTF", PRIORITY: "Priority", MLFQ: "MLFQ" };

/* ------------------------------------------------------------------ tabs */
function showTab(name) {
  App.tab = name;
  $$(".plate").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + name));
  window.dispatchEvent(new CustomEvent("tabshow", { detail: name }));
}
$$(".plate").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

function wireSubnav(navId, onShow) {
  const nav = $(navId);
  nav.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    $$("button", nav).forEach((x) => x.setAttribute("aria-selected", x === b));
    $$(".sub", nav.parentElement).forEach((s) => s.classList.toggle("on", s.id === b.dataset.sub));
    onShow(b.dataset.sub);
  });
}

/* ---------------------------------------------------------------- polling */
async function poll() {
  try {
    const s = await api(`/api/state?node=${encodeURIComponent(App.selected)}`);
    App.state = s;
    $("#pulse").classList.remove("off");
    $("#pulse-text").textContent = `tick ${s.tick} | ${s.db_written.toLocaleString()} rows saved`;
    renderCommand(s);
    App.listeners.forEach((fn) => { try { fn(s); } catch (e) { console.error(e); } });
  } catch (e) {
    $("#pulse").classList.add("off");
    $("#pulse-text").textContent = "server not reachable";
  }
}

/* --------------------------------------------------------------- controls */
function buildControls() {
  const seg = $("#seg-policy");
  seg.innerHTML = App.topo.policies.map((p) => `<button data-v="${p.id}" aria-pressed="false" title="${esc(p.label)}">${POLICY_SHORT[p.id]}</button>`).join("");
  seg.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setParam({ policy: b.dataset.v }); });
  $("#seg-dl").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setParam({ deadlock_mode: b.dataset.v }); });
  $("#rng-quantum").addEventListener("input", (e) => { $("#quantum-val").textContent = e.target.value; });
  $("#rng-quantum").addEventListener("change", (e) => setParam({ quantum: +e.target.value }));
  $("#rng-demand").addEventListener("input", (e) => { $("#demand-val").textContent = e.target.value; });
  $("#rng-demand").addEventListener("change", (e) => setParam({ demand: +e.target.value }));
  $("#rng-speed").addEventListener("input", (e) => { $("#speed-val").textContent = e.target.value; });
  $("#rng-speed").addEventListener("change", (e) => setParam({ speed: +e.target.value }));
  $("#sw-emerg").addEventListener("change", (e) => setParam({ emergency: e.target.checked }));
  $("#btn-pause").addEventListener("click", () => setParam({ paused: !App.state.params.paused }));
  $$("[data-act]").forEach((b) => b.addEventListener("click", async () => {
    try { const r = await api("/api/action", { type: b.dataset.act }); toast(r.message === "reset" ? "City reset" : "Done: " + r.message); poll(); }
    catch (e) { toast(e.message); }
  }));
}
async function setParam(p) {
  try { await api("/api/control", p); await poll(); } catch (e) { toast(e.message); }
}
function syncControls(s) {
  const p = s.params, act = document.activeElement;
  $$("#seg-policy button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === p.policy));
  $$("#seg-dl button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === p.deadlock_mode));
  const desc = App.topo.policies.find((x) => x.id === p.policy);
  $("#policy-desc").textContent = desc ? desc.label : "";
  $("#quantum-field").style.display = p.policy === "RR" ? "" : "none";
  if (act?.id !== "rng-quantum") { $("#rng-quantum").value = p.quantum; $("#quantum-val").textContent = p.quantum; }
  if (act?.id !== "rng-demand") { $("#rng-demand").value = p.demand; $("#demand-val").textContent = (+p.demand).toFixed(1); }
  if (act?.id !== "rng-speed") { $("#rng-speed").value = p.speed; $("#speed-val").textContent = p.speed; }
  $("#sw-emerg").checked = p.emergency;
  $("#btn-pause").textContent = p.paused ? "Resume" : "Pause";
  $("#ctl-state").textContent = p.paused ? "paused" : "live";
}

/* ----------------------------------------------------------- command panels */
function renderCommand(s) {
  syncControls(s);
  const m = s.metrics, d = s.deadlock;
  const gridlocked = d.cycles.length > 0;
  const k = (val, label, cls = "") => `<div class="kpi ${cls}"><b>${val}</b><span>${label}</span></div>`;
  $("#kpis").innerHTML =
    k(m.in_city, "vehicles in the city") +
    k(m.exited, "trips completed", "good") +
    k(fmt(m.avg_wait, 1), "average wait at a light (ticks)") +
    k(m.max_wait, "longest wait (starvation check)", m.max_wait > 60 ? "bad" : "") +
    k(`${m.deadlocks} / ${m.recovered}`, "gridlocks found / cleared", gridlocked ? "bad" : "") +
    k(m.amb_avg_wait === null ? "-" : fmt(m.amb_avg_wait, 1), `ambulance wait (${m.amb_count} trips)`);

  // variable message sign
  const msgs = [];
  if (gridlocked) msgs.push(`<span class="alert">GRIDLOCK ON ${d.cycles.reduce((a, c) => a + c.length, 0)} ROADS</span>`);
  if (s.edges.some((e) => e.cars.some((c) => c.k === "ambulance"))) msgs.push("AMBULANCE EN ROUTE - SIGNALS YIELD");
  if (s.edges.some((e) => e.closed)) msgs.push("LANE CLOSED - EXPECT DELAYS");
  msgs.push(`${POLICY_SHORT[s.params.policy].toUpperCase()} SCHEDULING   ${m.in_city} VEHICLES`);
  msgs.push(`AVG WAIT ${fmt(m.avg_wait, 1)} TICKS   ${m.exited} TRIPS DONE`);
  if (s.params.paused) msgs.unshift("SIMULATION PAUSED");
  $("#vms-text").innerHTML = msgs[Math.floor(Date.now() / 3200) % msgs.length];

  // scheduler panel
  const sc = s.scheduler;
  $("#sched-node").textContent = nodeName(sc.node);
  $("#sched-policy").textContent = `${sc.context_switches} switches, ${sc.preemptions} preemptions`;
  const cells = sc.gantt.split("").map((ch) => ch === "x" ? `<i class="x" title="all-red clearance (context switch)"></i>` : ch === "-" ? `<i class="idle"></i>` : `<i style="background:${LANE_COLOR[ch]}" title="${ch} green"></i>`).join("");
  const lanes = Object.keys(sc.lanes).sort().map((d) => {
    const L = sc.lanes[d];
    let st = ["idle", "Idle"];
    if (sc.current === d) st = ["run", "Running"];
    else if (L.blocked) st = ["blocked", "Blocked"];
    else if (L.ready > 0) st = ["ready", "Ready"];
    else if (L.queue > 0) st = ["clear", "Arriving"];
    if (L.emergency) st = [st[0], st[1] + " (ambulance)"];
    return `<div class="lane" style="--lc:${LANE_COLOR[d]}"><b>${d}</b>
      <div><div class="bar"><i style="width:${Math.min(100, L.queue * 25)}%"></i></div>
      <span class="tiny">${L.queue} queued, waited ${sc.wait[d] ?? 0}${s.params.policy === "MLFQ" ? `, level ${sc.level[d]}` : ""}</span></div>
      <span class="badge ${st[0]}">${st[1]}</span></div>`;
  }).join("");
  const pi = nodeInfo(sc.node);
  const placeBox = `<div class="place"><span class="zone" style="background:${ZONE_COLOR[pi.zone] || "#7b8790"}">${esc(pi.zone)}</span> ${esc(pi.note)} <a href="${esc(pi.maps)}" target="_blank" rel="noopener">Open in Google Maps</a></div>`;
  $("#sched-body").innerHTML = placeBox + `<div class="tiny">Last 90 ticks. Hatched cells are all-red clearance: the cost of a context switch.</div>
    <div class="gantt">${cells}</div><div class="lanes">${lanes}</div>`;

  $("#log").innerHTML = s.log.length ? s.log.slice(0, 14).map((e) => `<li class="${e.level}"><time>${e.tick}</time><span>${esc(e.text)}</span></li>`).join("")
    : `<li><span class="muted">Nothing yet. Try "Send an ambulance".</span></li>`;

  const h = s.history;
  $("#ch-vehicles").innerHTML = lineChart(h.map((x) => x.vehicles), { color: "#1f5fbf" });
  $("#ch-throughput").innerHTML = lineChart(h.map((x) => x.throughput), { color: "#0a5c3a" });
  $("#ch-wait").innerHTML = lineChart(h.map((x) => x.avg_wait), { color: "#c26a3d" });
}

/* ================================================================= MAP ==== */
const Map2D = (() => {
  const cv = $("#map"), ctx = cv.getContext("2d");
  let W = 0, H = 0, dpr = 1, bg = null, G = null;
  const vis = new Map();

  function geom() {
    const rows = App.topo.rows, cols = App.topo.cols;
    const m = Math.min(W, H) * 0.1;
    const step = Math.min((W - 2 * m) / (cols - 1), (H - 2 * m) / (rows - 1));
    const ox = (W - step * (cols - 1)) / 2, oy = (H - step * (rows - 1)) / 2;
    G = { rows, cols, step, ox, oy, road: step * 0.27, lane: step * 0.066 };
  }
  const P = (id) => { const [r, c] = id.split("-").map(Number); return [G.ox + c * G.step, G.oy + r * G.step]; };

  function resize() {
    if (!App.topo) return;
    const r = cv.getBoundingClientRect();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = r.width; H = r.height;
    cv.width = W * dpr; cv.height = H * dpr;
    geom(); drawBackground();
  }

  function rand(i) { const x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }

  function drawBackground() {
    bg = document.createElement("canvas"); bg.width = W * dpr; bg.height = H * dpr;
    const b = bg.getContext("2d"); b.scale(dpr, dpr);
    b.fillStyle = "#cfd9d6"; b.fillRect(0, 0, W, H);
    const { rows, cols, step, road } = G;
    // city blocks with a few buildings
    let k = 0;
    for (let r = -1; r < rows; r++) for (let c = -1; c < cols; c++) {
      const x0 = G.ox + c * step + road / 2 + 4, y0 = G.oy + r * step + road / 2 + 4, bw = step - road - 8;
      b.fillStyle = (r + c) % 3 === 0 ? "#bfd3c4" : "#dfe5e1";
      b.beginPath(); b.roundRect(x0, y0, bw, bw, 6); b.fill();
      const n = 2 + Math.floor(rand(k++) * 3);
      for (let i = 0; i < n; i++) {
        const w = bw * (0.22 + rand(k * 7 + i) * 0.25), h = bw * (0.2 + rand(k * 3 + i) * 0.3);
        b.fillStyle = (r + c) % 3 === 0 ? "#a5c2ad" : `rgba(120,135,140,${0.22 + rand(k + i) * 0.2})`;
        b.fillRect(x0 + 6 + rand(k * 11 + i) * (bw - w - 12), y0 + 6 + rand(k * 5 + i) * (bw - h - 12), w, h);
      }
    }
    // roads: one two-way band per neighbouring pair, plus gate stubs outside the grid
    b.lineCap = "butt";
    const seg = (a, c2, w, col, dash) => {
      b.strokeStyle = col; b.lineWidth = w; b.setLineDash(dash || []);
      b.beginPath(); b.moveTo(a[0], a[1]); b.lineTo(c2[0], c2[1]); b.stroke();
    };
    const pairs = new Set();
    App.topo.roads.forEach((r) => { const key = [r.from, r.to].sort().join("|"); if (!pairs.has(key)) { pairs.add(key); seg(P(r.from), P(r.to), road, "#2a313a"); } });
    const stub = Math.min(W, H) * 0.1;
    App.topo.nodes.forEach((n) => {
      const [x, y] = P(n.id);
      if (n.row === 0) seg([x, y], [x, y - stub], road, "#2a313a");
      if (n.row === rows - 1) seg([x, y], [x, y + stub], road, "#2a313a");
      if (n.col === 0) seg([x, y], [x - stub, y], road, "#2a313a");
      if (n.col === cols - 1) seg([x, y], [x + stub, y], road, "#2a313a");
    });
    pairs.clear();
    App.topo.roads.forEach((r) => { const key = [r.from, r.to].sort().join("|"); if (!pairs.has(key)) { pairs.add(key); seg(P(r.from), P(r.to), 2, "#f4f2e8", [10, 9]); } });
    App.topo.nodes.forEach((n) => {
      const [x, y] = P(n.id);
      if (n.row === 0) seg([x, y], [x, y - stub], 2, "#f4f2e8", [10, 9]);
      if (n.row === rows - 1) seg([x, y], [x, y + stub], 2, "#f4f2e8", [10, 9]);
      if (n.col === 0) seg([x, y], [x - stub, y], 2, "#f4f2e8", [10, 9]);
      if (n.col === cols - 1) seg([x, y], [x + stub, y], 2, "#f4f2e8", [10, 9]);
    });
    b.setLineDash([]);
    // junction boxes and zebra crossings
    App.topo.nodes.forEach((n) => {
      const [x, y] = P(n.id), h = road / 2;
      b.fillStyle = "#2a313a"; b.fillRect(x - h, y - h, road, road);
      b.fillStyle = "rgba(244,242,232,.85)";
      for (let i = -2; i <= 2; i++) {
        const o = i * road * 0.17;
        b.fillRect(x + o - 1.5, y - h - road * 0.2, 3, road * 0.16); b.fillRect(x + o - 1.5, y + h + road * 0.04, 3, road * 0.16);
        b.fillRect(x - h - road * 0.2, y + o - 1.5, road * 0.16, 3); b.fillRect(x + h + road * 0.04, y + o - 1.5, road * 0.16, 3);
      }
    });
    // locality names
    const fs = Math.max(10, Math.round(G.step * 0.062));
    b.font = `700 ${fs}px Barlow, "Segoe UI", sans-serif`; b.textBaseline = "alphabetic"; b.textAlign = "left";
    App.topo.nodes.forEach((n) => {
      const [x, y] = P(n.id), tw = b.measureText(n.short).width, boxW = tw + 18, boxH = fs + 9;
      const left = n.col >= cols - 2;
      const lx = left ? x - road * 0.62 - boxW : x + road * 0.62, ly = y - road * 0.62 - boxH;
      b.fillStyle = "rgba(255,255,255,.92)"; b.strokeStyle = "rgba(27,33,39,.35)"; b.lineWidth = 1;
      b.beginPath(); b.roundRect(lx, ly, boxW, boxH, 5); b.fill(); b.stroke();
      b.fillStyle = ZONE_COLOR[n.zone] || "#7b8790"; b.beginPath(); b.arc(lx + 8, ly + boxH / 2, 3.5, 0, 7); b.fill();
      b.fillStyle = n.zone === "hub" ? "#0b2a5c" : "#1b2127"; b.fillText(n.short, lx + 15, ly + boxH / 2 + fs * 0.35);
    });
  }

  const P0 = 0.17, P1 = 0.82, GAP = 0.17;       // position range along a road (fraction), spacing in a queue
  const KIND = { car: { L: 0.125, W: 0.062 }, bus: { L: 0.22, W: 0.07 }, truck: { L: 0.18, W: 0.07 }, ambulance: { L: 0.14, W: 0.066 } };
  const carColors = ["#e9edf0", "#cfe0f1", "#f1e3c6", "#d7e6d9", "#e8d5d9", "#c9d3dc"];

  function targets(s) {
    const out = [];
    for (const e of s.edges) {
      const [ux, uy] = P(e.u), [vx, vy] = P(e.v);
      const dx = vx - ux, dy = vy - uy, len = Math.hypot(dx, dy);
      const lx = dy / len, ly = -dx / len;                 // left-hand traffic
      let prev = null;
      e.cars.forEach((c, i) => {
        let p = P0 + Math.min(1, c.f) * (P1 - P0);
        p = Math.min(p, i === 0 ? P1 : prev - GAP);
        prev = p;
        out.push({ id: c.id, k: c.k, x: ux + dx * p + lx * G.lane, y: uy + dy * p + ly * G.lane,
                   a: Math.atan2(dy, dx), stuck: e.stuck, ready: c.ready });
      });
    }
    return out;
  }

  function drawVehicle(v, now) {
    const sp = KIND[v.k] || KIND.car, L = sp.L * G.step, Wd = sp.W * G.step;
    ctx.save(); ctx.translate(v.x, v.y); ctx.rotate(v.a);
    ctx.globalAlpha = v.alpha;
    if (v.k === "ambulance") {
      const f = Math.floor(now / 160) % 2;
      ctx.fillStyle = f ? "rgba(226,55,60,.35)" : "rgba(31,95,191,.35)";
      ctx.beginPath(); ctx.arc(0, 0, L * 1.25, 0, 7); ctx.fill();
    }
    ctx.fillStyle = v.k === "bus" ? "#ffc629" : v.k === "truck" ? "#c97b3a" : v.k === "ambulance" ? "#ffffff" : carColors[v.id % carColors.length];
    ctx.strokeStyle = v.stuck ? "#e2373c" : "rgba(20,25,30,.75)"; ctx.lineWidth = v.stuck ? 2 : 1;
    ctx.beginPath(); ctx.roundRect(-L / 2, -Wd / 2, L, Wd, Wd * 0.28); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "rgba(30,40,50,.55)"; ctx.fillRect(L * 0.1, -Wd * 0.34, L * 0.2, Wd * 0.68);   // windscreen
    if (v.k === "ambulance") {
      ctx.fillStyle = "#e2373c"; ctx.fillRect(-L * 0.28, -Wd * 0.09, L * 0.3, Wd * 0.18); ctx.fillRect(-L * 0.19, -Wd * 0.28, L * 0.1, Wd * 0.56);
    }
    ctx.restore();
  }

  function frame(now) {
    requestAnimationFrame(frame);
    if (!App.topo || !G || !App.state || App.tab !== "command") return;
    const s = App.state;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.drawImage(bg, 0, 0, W, H);

    // hazard / closed overlays
    for (const e of s.edges) {
      if (!e.stuck && !e.closed) continue;
      const [ux, uy] = P(e.u), [vx, vy] = P(e.v);
      const dx = vx - ux, dy = vy - uy, lx = dy / Math.hypot(dx, dy), ly = -dx / Math.hypot(dx, dy);
      ctx.save();
      ctx.lineWidth = G.lane * 2.4; ctx.lineCap = "butt";
      ctx.strokeStyle = e.stuck ? `rgba(226,55,60,${0.45 + 0.3 * Math.sin(now / 180)})` : "rgba(255,198,41,.8)";
      ctx.setLineDash(e.stuck ? [7, 7] : [14, 10]);
      ctx.beginPath();
      ctx.moveTo(ux + dx * 0.16 + lx * G.lane, uy + dy * 0.16 + ly * G.lane);
      ctx.lineTo(ux + dx * 0.84 + lx * G.lane, uy + dy * 0.84 + ly * G.lane);
      ctx.stroke(); ctx.restore();
      if (e.closed) {
        const mx = ux + dx * 0.5 + lx * G.lane, my = uy + dy * 0.5 + ly * G.lane;
        ctx.fillStyle = "#ffc629"; ctx.strokeStyle = "#222"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(mx, my - 9); ctx.lineTo(mx + 9, my + 7); ctx.lineTo(mx - 9, my + 7); ctx.closePath(); ctx.fill(); ctx.stroke();
        ctx.fillStyle = "#222"; ctx.font = "700 10px Barlow, sans-serif"; ctx.textAlign = "center"; ctx.fillText("!", mx, my + 5);
      }
    }

    // intersections: load tint, signal heads, selection
    for (const n of s.nodes) {
      const [x, y] = P(n.id), h = G.road / 2;
      const q = Object.values(n.q).reduce((a, b) => a + b, 0);
      if (q > 0) {
        ctx.fillStyle = q > 8 ? "rgba(226,55,60,.35)" : q > 4 ? "rgba(242,160,7,.30)" : "rgba(255,255,255,.08)";
        ctx.fillRect(x - h, y - h, G.road, G.road);
      }
      const off = h + G.road * 0.12;
      const heads = { N: [x - h * 0.55, y - off], E: [x + off, y - h * 0.55], S: [x + h * 0.55, y + off], W: [x - off, y + h * 0.55] };
      for (const d of Object.keys(n.q)) {
        const [hx, hy] = heads[d];
        const go = n.green === d;
        ctx.fillStyle = "#101418"; ctx.beginPath(); ctx.arc(hx, hy, G.road * 0.115, 0, 7); ctx.fill();
        ctx.fillStyle = go ? "#2fd081" : n.clearing ? "#f2a007" : "#e2373c";
        ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = go ? 8 : 4;
        ctx.beginPath(); ctx.arc(hx, hy, G.road * 0.075, 0, 7); ctx.fill(); ctx.shadowBlur = 0;
      }
      if (n.id === App.selected) {
        ctx.strokeStyle = "#1f5fbf"; ctx.lineWidth = 3; ctx.setLineDash([6, 4]);
        ctx.strokeRect(x - h - 9, y - h - 9, G.road + 18, G.road + 18); ctx.setLineDash([]);
      }
    }

    // vehicles, smoothed between polls
    const seen = new Set();
    for (const t of targets(s)) {
      seen.add(t.id);
      let v = vis.get(t.id);
      if (!v) { v = { ...t, alpha: 0 }; vis.set(t.id, v); }
      const dist = Math.hypot(t.x - v.x, t.y - v.y);
      const k = dist > G.step * 0.9 ? 1 : 0.22;
      v.x += (t.x - v.x) * k; v.y += (t.y - v.y) * k;
      let da = t.a - v.a; while (da > Math.PI) da -= 2 * Math.PI; while (da < -Math.PI) da += 2 * Math.PI;
      v.a += da * (dist > G.step * 0.9 ? 1 : 0.3);
      v.alpha = Math.min(1, v.alpha + 0.12); v.k = t.k; v.stuck = t.stuck; v.id = t.id;
      drawVehicle(v, now);
    }
    for (const id of vis.keys()) if (!seen.has(id)) vis.delete(id);
  }

  cv.addEventListener("click", (e) => {
    if (!G || !App.state) return;
    const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    let best = null, bd = 1e9;
    for (const n of App.topo.nodes) { const [x, y] = P(n.id); const d = Math.hypot(x - mx, y - my); if (d < bd) { bd = d; best = n.id; } }
    if (bd < G.step * 0.45) { App.selected = best; poll(); }
  });
  window.addEventListener("resize", resize);
  return { resize, start() { resize(); requestAnimationFrame(frame); } };
})();

/* ------------------------------------------------------------------- boot */
(async function boot() {
  try {
    App.topo = await api("/api/topology");
    buildControls();
    Map2D.start();
    App.ready = true;
    window.dispatchEvent(new Event("appready"));
    await poll();
    setInterval(poll, 350);
  } catch (e) {
    $("#pulse").classList.add("off"); $("#pulse-text").textContent = "server not reachable";
  }
})();
