/* Analytics (reads the SQL views) and the Syllabus map. */
const Analytics = (() => {
  const root = () => document.getElementById("analytics-body");
  let timer = null;

  function heat(rows) {
    if (!rows.length) return `<div class="empty">Waiting for sensor readings.</div>`;
    const max = Math.max(...rows.map((r) => r.avg_queue), 0.01);
    const by = Object.fromEntries(rows.map((r) => [r.node_id, r]));
    const cells = [];
    for (let r = 0; r < App.topo.rows; r++) for (let c = 0; c < App.topo.cols; c++) {
      const n = by[`${r}-${c}`];
      if (!n) { cells.push(`<div style="background:#98a4a8">-</div>`); continue; }
      const hue = 140 - 140 * (n.avg_queue / max);
      cells.push(`<div style="background:hsl(${hue.toFixed(0)} 62% 34%)" title="${esc(nodeName(n.node_id))}: average queue ${n.avg_queue}, peak ${n.peak_queue}">${n.avg_queue.toFixed(1)}<small>${esc(nodeInfo(n.node_id).short)}</small></div>`);
    }
    return `<div class="heat" style="grid-template-columns:repeat(${App.topo.cols},1fr)">${cells.join("")}</div>
      <div class="tiny" style="margin-top:.4rem">Average cars queued at each intersection, from the sensor_reading table. Red is the bottleneck.</div>`;
  }

  async function refresh() {
    if (App.tab !== "analytics") return;
    try {
      const a = await api("/api/db/analytics");
      const t = a.totals[0];
      const k = (v, l) => `<div class="kpi"><b>${Number(v).toLocaleString()}</b><span>${l}</span></div>`;
      root().innerHTML = `
        <p class="lead">Every number on this page is a SQL query over the data the simulation has been writing. The views are defined once in the schema and reused here.</p>
        <div class="panel" style="margin-bottom:.9rem"><div class="kpis" style="grid-template-columns:repeat(4,1fr)">
          ${k(t.trips, "trips stored")}${k(t.readings, "sensor readings")}${k(t.violations, "violations issued")}${k(t.treasury, "collected by the treasury (Rs)")}</div></div>
        <div class="grid2">
          <div class="panel"><div class="sign"><span>Which scheduling policy kept queues shortest?</span><small>v_policy_performance</small></div><div class="body">
            ${a.policy.length ? barChart(a.policy.map((p) => ({ label: p.policy, value: p.avg_queue })), { w: 460 }) : `<div class="empty">No data.</div>`}
            <div class="tiny">Average cars queued per sensor sample. Switch policies in the command centre and let each run for a while to fill this chart. Ranked with RANK() OVER.</div></div></div>
          <div class="panel"><div class="sign"><span>Where is the bottleneck?</span><small>v_intersection_load</small></div><div class="body">${heat(a.load)}</div></div>
          <div class="panel"><div class="sign"><span>Trips completed per 100 ticks</span><small>v_flow_by_bucket</small></div><div class="body">
            ${columns(a.flow.slice(-12).map((f) => ({ label: String(f.bucket * 100), value: f.trips })), { w: 460, h: 160 })}
            <div class="tiny">Last 12 buckets, label = first tick of the bucket. Average wait in each: ${a.flow.slice(-12).map((f) => fmt(f.avg_wait, 1)).join(", ")} ticks.</div></div></div>
          <div class="panel"><div class="sign"><span>Garage hit ratio by eviction policy</span><small>v_parking_efficiency</small></div><div class="body">
            ${a.parking.length ? barChart(a.parking.map((p) => ({ label: p.policy, value: p.hit_pct })), { best: "max", unit: "%", w: 460 }) : `<div class="empty">No parking data yet.</div>`}
            <div class="tiny">Switch the eviction policy in OS lab, Memory, and let it run to compare.</div></div></div>
          <div class="panel"><div class="sign"><span>Worst offenders</span><small>v_top_offenders</small></div><div class="body">
            ${dataTable(["Plate", "Owner", "Violations", "Fines (Rs)", "Unpaid"], a.offenders.map((o) => [o.plate, o.name, o.violations, o.total_fines, o.unpaid]))}</div></div>
          <div class="panel"><div class="sign"><span>Fines and violations</span></div><div class="body stack">
            ${dataTable(["Status", "Fines", "Total (Rs)"], a.fines.map((f) => [f.status, f.fines, f.total]))}
            ${columns(a.violations.map((v) => ({ label: v.code, value: v.n })), { w: 460, h: 130, color: "#c26a3d" })}</div></div>
          <div class="panel"><div class="sign"><span>Deadlock events</span><small>deadlock_event</small></div><div class="body">
            ${a.deadlocks.length ? dataTable(["Handling mode", "Events", "Resolved"], a.deadlocks.map((d) => [d.mode, d.events, d.resolved])) : `<div class="empty">No gridlock yet. Press "Force a gridlock" in the command centre.</div>`}</div></div>
          <div class="panel"><div class="sign"><span>Audit trail</span><small>audit_log</small></div><div class="body">
            ${a.audit.length ? dataTable(["#", "Table", "Row", "Old", "New", "At"], a.audit.map((x) => [x.audit_id, x.table_name, x.row_key, x.old_value, x.new_value, x.at])) : `<div class="empty">Empty until a fine is paid.</div>`}</div></div>
        </div>`;
    } catch (e) { root().innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  function init() {
    window.addEventListener("tabshow", (e) => {
      clearInterval(timer);
      if (e.detail === "analytics") { refresh(); timer = setInterval(refresh, 5000); }
    });
  }
  return { init };
})();

const About = (() => {
  const card = (m, cls) => `<div class="module"><div class="sign ${cls}"><span class="id">${esc(m.id)}</span><span>${esc(m.name)}</span><small class="mono">${esc(m.file)}</small></div>
    <div class="body stack"><div>${esc(m.idea)}</div>
    <div class="tiny"><b>See it:</b> ${esc(m.see)}</div>
    <div class="callout info"><b>Viva:</b> ${esc(m.viva)}</div></div></div>`;
  async function init() {
    const el = document.getElementById("about-body");
    try {
      const m = await api("/api/modules");
      const flow = ["Gate threads (producers)", "Bounded buffer", "Tick loop: scheduler, semaphores, deadlock, garage", "DB writer queue", "SQLite in WAL mode", "SQL views", "This dashboard"];
      el.innerHTML = `
        <p class="lead">The brief asked for at least three modules from each subject. SignalOS uses <b>${m.os.length} OS modules</b> and <b>${m.dbms.length} DBMS modules</b>. Each one has a real job in the city, a place to see it running, and the questions an examiner is most likely to ask.</p>
        <div class="panel" style="margin-bottom:.9rem"><div class="sign warn"><span>The big idea</span></div><div class="body stack">
          <div>Most traffic projects just switch lights on a timer. SignalOS treats the city as a computer: an <b>intersection is a CPU</b>, a <b>lane is a process</b>, the green light is CPU time, a <b>road slot is a semaphore</b>, gridlock is a <b>deadlock</b>, and the downtown garage is <b>virtual memory</b>. Everything that happens is stored in a transactional database, so scheduling policies can be compared with SQL.</div>
          <div class="chips" style="align-items:center">${flow.map((f, i) => `<span class="chip">${esc(f)}</span>${i < flow.length - 1 ? "&rarr;" : ""}`).join(" ")}</div></div></div>
        <h2 style="margin:1rem 0 .6rem;font-family:var(--font-cond)">Operating systems</h2><div class="grid2">${m.os.map((x) => card(x, "")).join("")}</div>
        <h2 style="margin:1rem 0 .6rem;font-family:var(--font-cond)">Database management systems</h2><div class="grid2">${m.dbms.map((x) => card(x, "guide")).join("")}</div>`;
    } catch (e) { el.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  return { init };
})();

window.addEventListener("appready", () => { Analytics.init(); About.init(); });
