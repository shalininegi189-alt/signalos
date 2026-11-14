/* Database lab: schema, SQL console, transactions, concurrency, indexing, triggers, crash recovery. */

/* Shared by the analytics page too. */
function dataTable(columns, rows, { max = 200 } = {}) {
  if (!rows || !rows.length) return `<div class="empty">No rows.</div>`;
  const cell = (v) => (v === null || v === undefined ? `<span class="tiny">NULL</span>` : esc(typeof v === "number" && !Number.isInteger(v) ? +v.toFixed(3) : v));
  return `<div class="scroll"><table class="tbl"><thead><tr>${columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
    ${rows.slice(0, max).map((r) => `<tr>${r.map((v) => `<td class="${typeof v === "number" ? "num" : ""}">${cell(v)}</td>`).join("")}</tr>`).join("")}
  </tbody></table></div>`;
}
const money = (v) => "Rs " + Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const DBLab = (() => {
  const root = (id) => document.getElementById(id);
  const loaded = new Set();
  const current = () => $("#db-nav [aria-selected=true]").dataset.sub;

  /* ---------------------------------------------------------------- schema */
  async function loadSchema() {
    const el = root("db-schema");
    try {
      const s = await api("/api/db/schema");
      const card = (t) => `<div class="module"><div class="sign"><span>${esc(t.name)}</span><small>${t.rows.toLocaleString()} rows</small></div>
        <div class="body"><table class="tbl"><tbody>${t.columns.map((c) => {
          const fk = t.fks.find((f) => f.from === c.name);
          return `<tr><td><b>${esc(c.name)}</b></td><td class="tiny">${esc(c.type || "")}</td>
            <td>${c.pk ? `<span class="badge ok">PK</span>` : ""}${fk ? `<span class="badge ready" title="references ${esc(fk.table)}(${esc(fk.to)})">FK ${esc(fk.table)}</span>` : ""}${c.notnull && !c.pk ? `<span class="tiny"> not null</span>` : ""}</td></tr>`;
        }).join("")}</tbody></table></div></div>`;
      el.innerHTML = `<p class="lead">${s.tables.length} tables in third normal form. Owners, vehicles, fines and payments are separate so no fact is stored twice. Foreign keys are enforced, and ${s.triggers.length} triggers and ${s.views.length} views sit on top.</p>
        <div class="grid3">${s.tables.map(card).join("")}</div>
        <div class="grid3" style="margin-top:.9rem">
          <div class="panel"><div class="sign guide"><span>Triggers</span></div><div class="body chips">${s.triggers.map((x) => `<span class="chip">${esc(x.name)} on ${esc(x.tbl_name)}</span>`).join("")}</div></div>
          <div class="panel"><div class="sign guide"><span>Views</span></div><div class="body chips">${s.views.map((x) => `<span class="chip">${esc(x.name)}</span>`).join("")}</div></div>
          <div class="panel"><div class="sign guide"><span>Indexes</span></div><div class="body chips">${s.indexes.map((x) => `<span class="chip">${esc(x.name)} on ${esc(x.tbl_name)}</span>`).join("")}</div></div>
        </div>`;
    } catch (e) { el.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }

  /* ----------------------------------------------------------- SQL console */
  const EXAMPLES = [
    ["Worst offenders", "SELECT * FROM v_top_offenders LIMIT 10"],
    ["Join three tables", "SELECT o.name, f.plate, f.amount, f.status\nFROM fine f JOIN vehicle v ON v.plate = f.plate\nJOIN owner o ON o.owner_id = v.owner_id\nORDER BY f.amount DESC LIMIT 10"],
    ["Busiest localities", "SELECT locality, zone, avg_queue, peak_queue FROM v_locality_load ORDER BY avg_queue DESC"],
    ["Busiest intersections", "SELECT node_id, ROUND(AVG(q_n + q_e + q_s + q_w), 2) AS avg_queue, MAX(q_n + q_e + q_s + q_w) AS peak\nFROM sensor_reading GROUP BY node_id ORDER BY avg_queue DESC LIMIT 8"],
    ["Window function", "SELECT plate, amount,\n  SUM(amount) OVER (PARTITION BY plate ORDER BY fine_id) AS running_total\nFROM fine ORDER BY plate LIMIT 15"],
    ["Query plan", "EXPLAIN QUERY PLAN SELECT * FROM fine WHERE status = 'UNPAID'"],
  ];
  function buildSql() {
    root("db-sql").innerHTML = `
      <p class="lead">A read-only console on the live database. Write queries here while the simulation keeps inserting rows underneath you. Only SELECT, WITH and EXPLAIN are accepted.</p>
      <div class="panel"><div class="sign"><span>SQL console</span><small>Ctrl+Enter to run</small></div><div class="body stack">
        <div class="chips">${EXAMPLES.map((e, i) => `<button class="btn small" data-ex="${i}">${esc(e[0])}</button>`).join("")}</div>
        <textarea id="sql-in" rows="5" spellcheck="false" aria-label="SQL query">${esc(EXAMPLES[0][1])}</textarea>
        <div class="row"><button class="btn primary" id="sql-run">Run query</button><span class="tiny" id="sql-meta"></span></div>
        <div id="sql-out"></div></div></div>`;
    $$("[data-ex]", root("db-sql")).forEach((b) => (b.onclick = () => { root("sql-in").value = EXAMPLES[+b.dataset.ex][1]; runSql(); }));
    root("sql-run").onclick = runSql;
    root("sql-in").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) runSql(); });
    runSql();
  }
  async function runSql() {
    const out = root("sql-out");
    try {
      const r = await api("/api/db/query", { sql: root("sql-in").value });
      root("sql-meta").textContent = `${r.rows.length}${r.truncated ? "+" : ""} rows in ${r.ms} ms`;
      out.innerHTML = dataTable(r.columns, r.rows);
    } catch (e) { root("sql-meta").textContent = ""; out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  function openInConsole(sql) {
    root("sql-in").value = sql;
    $('#db-nav [data-sub="db-sql"]').click();
    runSql();
  }

  /* ---------------------------------------------------------- transactions */
  function buildTx() {
    root("db-tx").innerHTML = `
      <p class="lead">Paying a fine changes three tables: the driver's wallet, the city treasury and the fine itself. These changes must succeed together or not at all. Inject a failure at any step and watch the database undo everything.</p>
      <div class="grid2">
        <div class="panel"><div class="sign"><span>Pay one fine atomically</span><small>BEGIN IMMEDIATE ... COMMIT</small></div><div class="body stack">
          <div class="field"><label for="tx-fine">Unpaid fine</label><select id="tx-fine"></select></div>
          <div class="field"><label for="tx-fail">Inject a failure</label><select id="tx-fail">
            <option value="">None: let it commit</option>
            <option value="after_debit">Power cut after the wallet is debited</option>
            <option value="after_credit">Power cut after the treasury is credited</option>
            <option value="overdraw">Debit more than the wallet holds (CHECK fails)</option></select></div>
          <div class="row"><button class="btn primary" id="tx-run">Run transaction</button><button class="btn small" id="tx-refresh">Refresh list</button></div>
          <div id="tx-out"></div></div></div>
        <div class="panel"><div class="sign guide"><span>Pay all fines for one owner</span><small>one SAVEPOINT per fine</small></div><div class="body stack">
          <p style="margin:0;font-size:.92rem">Owners with two or more unpaid fines. If the wallet runs dry halfway, only the fine that failed rolls back to its savepoint. The fines already paid stay paid.</p>
          <div id="tx-owners"></div><div id="tx-all-out"></div></div></div>
      </div>
      <div class="grid2" style="margin-top:.9rem">
        <div class="callout info"><b>Atomicity</b>: all three updates or none. <b>Consistency</b>: CHECK (balance &gt;= 0), foreign keys and triggers refuse impossible states. <b>Isolation</b>: BEGIN IMMEDIATE takes the write lock first. <b>Durability</b>: the commit is written to the write-ahead log before it returns.</div>
        <div class="callout">The invariant to watch: wallet + treasury never changes in total. Money only moves, it is never created or lost, even when the power cuts out.</div>
      </div>`;
    root("tx-run").onclick = runTx;
    root("tx-refresh").onclick = loadTx;
  }
  async function loadTx() {
    try {
      const c = await api("/api/db/acid/candidates");
      root("tx-fine").innerHTML = c.fines.length
        ? c.fines.map((f) => `<option value="${f.fine_id}">#${f.fine_id}  ${esc(f.plate)}  ${money(f.amount)}  (${esc(f.label)}; wallet ${money(f.balance)})</option>`).join("")
        : `<option value="">No unpaid fines yet. Let the city run.</option>`;
      root("tx-owners").innerHTML = c.owners.length
        ? `<table class="tbl"><thead><tr><th>Owner</th><th class="num">Unpaid</th><th class="num">Owes</th><th class="num">Wallet</th><th></th></tr></thead><tbody>
          ${c.owners.map((o) => `<tr class="${o.balance < o.owed ? "worst" : ""}"><td>${esc(o.name)}</td><td class="num">${o.unpaid}</td><td class="num">${money(o.owed)}</td><td class="num">${money(o.balance)}</td>
            <td><button class="btn small" data-pay="${o.owner_id}">Pay all</button></td></tr>`).join("")}</tbody></table>
          <div class="tiny">Red rows cannot afford everything, so they show the savepoints at work.</div>`
        : `<div class="empty">No owner has two unpaid fines yet.</div>`;
      $$("[data-pay]", root("tx-owners")).forEach((b) => (b.onclick = () => payAll(+b.dataset.pay)));
    } catch (e) { root("tx-fine").innerHTML = ""; root("tx-out").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  const stateBox = (o, title) => `<div class="kv-box"><div class="lbl">${title}</div><div class="kv">
      <span>Fine</span><b>${esc(o.fine_status)}</b><span>Wallet</span><b>${money(o.wallet)}</b><span>Treasury</span><b>${money(o.treasury)}</b>
      <span>Total</span><b>${money(o.wallet + o.treasury)}</b></div></div>`;
  async function runTx() {
    const id = root("tx-fine").value, out = root("tx-out");
    if (!id) { toast("Pick a fine first"); return; }
    root("tx-run").disabled = true;
    try {
      const r = await api("/api/db/acid", { fine_id: +id, fail_at: root("tx-fail").value || null });
      out.innerHTML = `<div class="callout ${r.ok ? "ok" : "bad"}"><b>${r.ok ? "Committed." : "Rolled back."}</b> ${r.error ? esc(r.error) : "All three tables changed together."}
        <span class="badge ${r.invariant_holds ? "ok" : "bad"}">${r.invariant_holds ? "total money unchanged" : "INVARIANT BROKEN"}</span></div>
        <div class="grid2" style="margin-top:.5rem">${stateBox(r.before, "Before")}${stateBox(r.after, "After")}</div>
        <div class="lbl" style="margin-top:.6rem">What the database did</div>
        <div>${r.steps.map((s, i) => `<div class="stepline"><span class="phase ${/ROLLBACK/.test(s) ? "UNDO" : /COMMIT/.test(s) ? "REDO" : "ANALYSIS"}">${i + 1}</span><span class="mono">${esc(s)}</span></div>`).join("")}</div>`;
    } catch (e) { out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
    root("tx-run").disabled = false;
    loadTx();
  }
  async function payAll(owner) {
    try {
      const r = await api("/api/db/pay-all", { owner_id: owner });
      const paid = r.results.filter((x) => x.paid).length;
      root("tx-all-out").innerHTML = `<div class="callout ${paid === r.results.length ? "ok" : "warn"}"><b>${paid} of ${r.results.length} fines paid.</b> Wallet left: ${money(r.wallet_after)}</div>
        <div class="chips" style="margin-top:.4rem">${r.results.map((x) => `<span class="chip ${x.paid ? "hit" : "fault"}" title="${x.paid ? "paid" : "rolled back to savepoint"}">#${x.fine_id} ${money(x.amount)} ${x.paid ? "paid" : "rolled back"}</span>`).join("")}</div>`;
    } catch (e) { root("tx-all-out").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
    loadTx();
  }

  /* ----------------------------------------------------------- concurrency */
  const SCHEDULES = [
    ["Lost update", "R1(A) R2(A) W1(A) W2(A) C1 C2"],
    ["Serializable", "R1(A) W1(A) R2(A) W2(A) C1 C2"],
    ["Dirty read", "W1(A) R2(A) C1 C2"],
    ["Deadlock", "W1(A) W2(B) W1(B) W2(A) C1 C2"],
  ];
  function buildConc() {
    root("db-conc").innerHTML = `
      <p class="lead">When two transactions touch the same row, the order of their reads and writes decides whether the result is correct. Type a schedule, such as R1(A) means transaction 1 reads A, and the lab checks it two ways.</p>
      <div class="grid2">
        <div class="panel"><div class="sign"><span>Schedule analyser</span><small>precedence graph + strict 2PL</small></div><div class="body stack">
          <div class="chips">${SCHEDULES.map((s, i) => `<button class="btn small" data-sc="${i}">${esc(s[0])}</button>`).join("")}</div>
          <div class="field"><label for="cc-in">Schedule (R, W, C followed by the transaction number)</label><input type="text" id="cc-in" value="${SCHEDULES[0][1]}"></div>
          <button class="btn primary" id="cc-run">Analyse</button><div id="cc-out"></div></div></div>
        <div class="panel"><div class="sign guide"><span>A lost update, for real</span><small>4 threads, 40 increments each</small></div><div class="body stack">
          <p style="margin:0;font-size:.92rem">Four real threads each add 1 to the same counter forty times. The expected total is 160. Once with plain read-then-write, once inside BEGIN IMMEDIATE.</p>
          <button class="btn primary" id="lu-run">Run the experiment</button><div id="lu-out"></div></div></div>
      </div>`;
    $$("[data-sc]", root("db-conc")).forEach((b) => (b.onclick = () => { root("cc-in").value = SCHEDULES[+b.dataset.sc][1]; runConc(); }));
    root("cc-run").onclick = runConc;
    root("cc-in").addEventListener("keydown", (e) => { if (e.key === "Enter") runConc(); });
    root("lu-run").onclick = runLost;
    runConc();
  }
  async function runConc() {
    const out = root("cc-out");
    try {
      const r = await api("/api/db/concurrency", { schedule: root("cc-in").value });
      const sz = r.serializability, lk = r.locking;
      const inCycle = (a, b) => sz.cycles.some((c) => c.includes(a) && c.includes(b));
      const edges = sz.edges.map((e) => ({ from: e.from, to: e.to, hot: inCycle(e.from, e.to) }));
      out.innerHTML = `
        <div class="callout ${sz.serializable ? "ok" : "bad"}"><b>${sz.serializable ? "Conflict-serialisable." : "Not serialisable."}</b>
          ${sz.serializable ? `It behaves like running ${sz.serial_order.map((t) => "T" + t).join(" then ")} one after the other.` : `The precedence graph has a cycle: ${sz.cycles.map((c) => c.map((t) => "T" + t).join(" &rarr; ") + " &rarr; T" + c[0]).join("; ")}.`}</div>
        <div class="grid2" style="margin-top:.5rem"><div><div class="lbl">Precedence graph</div>${graphSvg(sz.txns, edges, { w: 240, h: 180 })}</div>
          <div><div class="lbl">Conflicts found</div>${sz.edges.length ? sz.edges.map((e) => `<div class="stepline"><b>T${e.from} &rarr; T${e.to}</b><span class="tiny">${esc(e.why)}</span></div>`).join("") : `<div class="tiny">No conflicting operations.</div>`}</div></div>
        <div class="lbl" style="margin-top:.7rem">Strict two-phase locking runs the same schedule</div>
        ${lk.log.map((l) => `<div class="stepline"><span class="mono">${esc(l)}</span></div>`).join("")}
        ${lk.deadlocks.length ? `<div class="callout bad" style="margin-top:.4rem"><b>Deadlock</b> between ${lk.deadlocks[0].cycle.join(" and ")}. The lock manager aborted ${esc(lk.deadlocks[0].victim)} as the victim.</div>` : ""}`;
    } catch (e) { out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  async function runLost() {
    const btn = root("lu-run"), out = root("lu-out");
    btn.disabled = true; btn.textContent = "Running...";
    try {
      const r = await api("/api/db/lost-update");
      const row = (label, x, bad) => `<tr class="${bad ? "worst" : "best"}"><td>${label}</td><td class="num">${x.expected}</td><td class="num">${x.final}</td><td class="num">${x.lost}</td></tr>`;
      out.innerHTML = `<table class="tbl"><thead><tr><th></th><th class="num">Expected</th><th class="num">Final</th><th class="num">Lost</th></tr></thead><tbody>
        ${row("Plain read then write", r.unsafe, r.unsafe.lost > 0)}${row("Inside BEGIN IMMEDIATE", r.transactional, r.transactional.lost > 0)}</tbody></table>
        <div class="callout info" style="margin-top:.5rem">Without isolation, threads read the same old value and overwrite each other's work: <b>${r.unsafe.lost}</b> increments vanished. With the write lock taken first, none did.</div>`;
    } catch (e) { out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
    btn.disabled = false; btn.textContent = "Run the experiment";
  }

  /* -------------------------------------------------------------- indexing */
  function buildIdx() {
    root("db-idx").innerHTML = `
      <p class="lead">The same query on 200,000 sensor readings, three times. Only the index changes. The plan shows how SQLite decides to answer it, and the clock shows what that decision costs.</p>
      <div class="panel"><div class="sign"><span>Index benchmark</span><small>EXPLAIN QUERY PLAN + timings</small></div><div class="body stack">
        <button class="btn primary" id="ix-run">Run the benchmark</button><div id="ix-out"><div class="empty">Press run. It builds 200,000 rows in memory first.</div></div></div></div>`;
    root("ix-run").onclick = runIdx;
  }
  async function runIdx() {
    const btn = root("ix-run"), out = root("ix-out");
    btn.disabled = true; btn.textContent = "Running...";
    out.innerHTML = `<div class="empty">Building and querying 200,000 rows...</div>`;
    try {
      const r = await api("/api/db/benchmark");
      const best = r.stages[r.stages.length - 1];
      out.innerHTML = `<div class="mono tiny" style="margin-bottom:.4rem">${esc(r.query)}</div>
        <div class="lbl">Time per query, lower is better</div>
        ${barChart(r.stages.map((s) => ({ label: s.name.split(" ")[0] === "No" ? "No index" : s.name.split(" ")[0], value: s.ms })), { unit: " ms", fmt: (v) => fmt(v, 2), w: 460 })}
        <div class="scroll" style="margin-top:.6rem"><table class="tbl"><thead><tr><th>Stage</th><th>Plan SQLite chose</th><th class="num">Time</th><th class="num">Speed-up</th></tr></thead><tbody>
          ${r.stages.map((s) => `<tr><td style="white-space:normal"><b>${esc(s.name)}</b>${s.ddl ? `<div class="mono tiny">${esc(s.ddl)}</div>` : ""}</td><td class="mono" style="white-space:normal;min-width:240px">${s.plan.map(esc).join("<br>")}</td><td class="num">${fmt(s.ms, 2)} ms</td><td class="num"><b>${fmt(s.speedup, 1)}x</b></td></tr>`).join("")}</tbody></table></div>
        <div class="grid2" style="margin-top:.7rem"><div class="callout info"><b>SCAN</b> reads all ${r.rows.toLocaleString()} rows. <b>SEARCH ... USING INDEX</b> walks the B-tree straight to the matching range. Column order matters: the index starts with the equality column (node_id), then the range column (tick).</div>
          <div class="callout ok"><b>Covering index</b>: it also stores the four queue columns the query reads, so SQLite never touches the table at all. That is the ${fmt(best.speedup, 0)}x row.</div></div>`;
    } catch (e) { out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
    btn.disabled = false; btn.textContent = "Run the benchmark again";
  }

  /* -------------------------------------------------------------- triggers */
  const TRG = {
    trg_violation_issue_fine: ["AFTER INSERT on violation", "Every violation automatically becomes a fine. Repeat offenders pay up to 25% more for each earlier violation (capped at double), so the business rule lives in the database, not in the application."],
    trg_fine_audit: ["AFTER UPDATE OF status on fine", "Every change of a fine's status writes who-changed-what to audit_log, so there is a permanent trail of money movements."],
    trg_paid_fine_final: ["BEFORE UPDATE OF status on fine", "A paid fine is final. Any attempt to set it back raises an error and aborts the statement."],
    trg_wallet_audit: ["AFTER UPDATE OF balance on wallet", "Every wallet movement is audited with the old and the new balance, so money can always be traced."],
  };
  const VIEW_NOTE = {
    v_flow_by_bucket: "Trips, average wait and tows per 100 ticks", v_intersection_load: "Average and peak queue per intersection",
    v_parking_efficiency: "Garage hit ratio for each eviction policy", v_policy_performance: "Average queue under each scheduling policy",
    v_top_offenders: "Plates ranked by violations and unpaid fines",
    v_locality_load: "Average and peak queue per Dehradun locality, by name", v_signal_plan_input: "Average queue per approach: input of the signal-timing planner",
  };
  function buildTrg() {
    root("db-trg").innerHTML = `
      <p class="lead">Triggers run inside the database whenever a row changes, so the rules cannot be skipped by any program. Views save a query under a name so analytics can reuse it.</p>
      <div class="grid2">
        <div class="panel"><div class="sign"><span>Fire a trigger</span><small>insert a violation, watch the fine</small></div><div class="body stack">
          <div class="field"><label for="tg-code">Violation type</label><select id="tg-code"></select></div>
          <button class="btn primary" id="tg-run">Issue a violation</button><div id="tg-out"></div></div></div>
        <div class="panel"><div class="sign guide"><span>Audit log</span><small>written by trg_fine_audit</small></div><div class="body" id="tg-audit"></div></div>
      </div>
      <div class="lbl" style="margin:.9rem 0 .4rem">Triggers in this database</div><div class="grid3" id="tg-list"></div>
      <div class="lbl" style="margin:.9rem 0 .4rem">Views in this database</div><div class="grid3" id="tg-views"></div>`;
    root("tg-run").onclick = fireTrigger;
  }
  async function loadTrg() {
    try {
      const [codes, trg, schema, an] = await Promise.all([
        api("/api/db/query", { sql: "SELECT code, label, base_fine FROM violation_type ORDER BY base_fine DESC" }),
        api("/api/db/query", { sql: "SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name" }),
        api("/api/db/schema"), api("/api/db/analytics")]);
      root("tg-code").innerHTML = codes.rows.map((c) => `<option value="${esc(c[0])}">${esc(c[1])} (base ${money(c[2])})</option>`).join("");
      root("tg-list").innerHTML = trg.rows.map(([name, sql]) => `<div class="module"><div class="sign"><span>${esc(name)}</span></div><div class="body stack">
        <span class="badge ready">${esc((TRG[name] || ["trigger"])[0])}</span><div>${esc((TRG[name] || ["", ""])[1])}</div><pre class="mono tiny scroll" style="padding:.4rem;max-height:170px">${esc(sql)}</pre></div></div>`).join("");
      root("tg-views").innerHTML = schema.views.map((v) => `<div class="module"><div class="sign guide"><span>${esc(v.name)}</span></div><div class="body stack"><div>${esc(VIEW_NOTE[v.name] || "")}</div>
        <button class="btn small" data-view="${esc(v.name)}">Query it in the console</button></div></div>`).join("");
      $$("[data-view]", root("tg-views")).forEach((b) => (b.onclick = () => openInConsole(`SELECT * FROM ${b.dataset.view}`)));
      root("tg-audit").innerHTML = an.audit.length
        ? dataTable(["#", "table", "row", "old", "new", "at"], an.audit.map((a) => [a.audit_id, a.table_name, a.row_key, a.old_value, a.new_value, a.at]))
        : `<div class="empty">Empty. Pay a fine in the Transactions page and a row appears here.</div>`;
    } catch (e) { root("tg-out").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  async function fireTrigger() {
    try {
      const r = await api("/api/db/trigger-demo", { code: root("tg-code").value });
      const up = r.fine.amount > r.base_fine;
      root("tg-out").innerHTML = `<div class="callout ${up ? "bad" : "ok"}"><b>Violation #${r.violation_id}</b> issued to <b>${esc(r.plate)}</b>, who already had <b>${r.prior_violations}</b> earlier violation${r.prior_violations === 1 ? "" : "s"}.
        The trigger created fine #${r.fine.fine_id}: base ${money(r.base_fine)} became <b>${money(r.fine.amount)}</b>${up ? " (repeat-offender escalation)" : ""}.</div>`;
    } catch (e) { root("tg-out").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }

  /* -------------------------------------------------------------- recovery */
  function buildRec() {
    root("db-rec").innerHTML = `
      <p class="lead">The power fails while four transactions are in flight. Some committed work never reached the data pages, and some uncommitted work did. The write-ahead log is what lets the database repair itself: analysis, redo and undo, as in the ARIES algorithm.</p>
      <div class="grid2">
        <div class="panel"><div class="sign"><span>Crash and recover</span><small>write-ahead log, steal / no-force</small></div><div class="body stack">
          <button class="btn danger" id="rc-run">Pull the plug</button><div id="rc-out"><div class="empty">Press the button to replay the scenario.</div></div></div></div>
        <div class="panel"><div class="sign guide"><span>The live database's own log</span><small>SQLite WAL mode</small></div><div class="body stack" id="rc-wal"></div></div>
      </div>`;
    root("rc-run").onclick = runRec;
  }
  async function loadWal() {
    try {
      const w = await api("/api/db/wal");
      root("rc-wal").innerHTML = `<table class="tbl"><tbody><tr><td>Journal mode</td><td class="num"><b>${esc(w.journal_mode.toUpperCase())}</b></td></tr>
        <tr><td>Pages in the log</td><td class="num">${w.wal_pages}</td></tr><tr><td>Pages already checkpointed</td><td class="num">${w.checkpointed}</td></tr>
        <tr><td>Log file size</td><td class="num">${(w.wal_bytes / 1024).toFixed(0)} KB</td></tr></tbody></table>
        <div class="tiny">Every commit is appended to the log first and copied into the main file later by a checkpoint. If the process dies in between, SQLite replays the log on the next open.</div>`;
    } catch (e) { root("rc-wal").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  async function runRec() {
    const btn = root("rc-run"), out = root("rc-out");
    btn.disabled = true;
    try {
      const r = await api("/api/db/recovery");
      const keys = Object.keys(r.initial);
      const rec = (l) => `<div class="stepline"><span class="phase">LSN ${l.lsn}</span><span>T${l.txn} ${l.type}${l.type === "UPDATE" ? ` ${esc(l.key)}: ${l.before} &rarr; ${l.after}` : ""}</span></div>`;
      out.innerHTML = `
        <div class="lbl">What happened before the crash</div><ol style="margin:.2rem 0 .8rem 1.1rem;padding:0;font-size:.92rem">${r.timeline.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>
        <div class="grid2"><div><div class="lbl">Log on disk (flushed up to LSN ${r.flushed_lsn})</div><div class="scroll" style="max-height:200px">${r.log_before_crash.filter((l) => l.lsn <= r.flushed_lsn).map(rec).join("")}</div></div>
          <div><div class="lbl">Lost with the crash (still in memory)</div>${r.lost_log_records.map(rec).join("")}</div></div>
        <div class="lbl" style="margin-top:.8rem">Recovery</div><div class="scroll" style="max-height:230px">${r.steps.map((s) => `<div class="stepline"><span class="phase ${s.phase}">${s.phase}</span><span>${esc(s.text)}</span></div>`).join("")}</div>
        <div class="lbl" style="margin-top:.8rem">Result</div>
        <table class="tbl"><thead><tr><th>Item</th><th class="num">Start</th><th class="num">Disk at crash</th><th class="num">Should be</th><th class="num">Recovered</th><th></th></tr></thead><tbody>
          ${keys.map((k) => { const ok = r.final[k] === r.expected[k]; return `<tr class="${ok ? "best" : "worst"}"><td>${k}</td><td class="num">${r.initial[k]}</td><td class="num">${r.disk_at_crash[k]}</td><td class="num">${r.expected[k]}</td><td class="num">${r.final[k]}</td><td>${ok ? "ok" : "WRONG"}</td></tr>`; }).join("")}</tbody></table>
        <div class="callout ${r.correct ? "ok" : "bad"}" style="margin-top:.6rem"><b>${r.correct ? "Recovered correctly." : "Recovery failed."}</b> Committed transactions ${r.winners.map((t) => "T" + t).join(", ")} were redone. Uncommitted ${r.losers.map((t) => "T" + t).join(", ")} was undone, even though its page had reached the disk.</div>`;
    } catch (e) { out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
    btn.disabled = false; btn.textContent = "Pull the plug again";
    loadWal();
  }

  /* ---------------------------------------------------------------- wiring */
  function show(sub) {
    if (sub === "db-schema") loadSchema();
    if (sub === "db-tx") loadTx();
    if (sub === "db-trg") loadTrg();
    if (sub === "db-rec") loadWal();
    if (sub === "db-idx" && !loaded.has(sub)) runIdx();
    loaded.add(sub);
  }
  function init() {
    buildSql(); buildTx(); buildConc(); buildIdx(); buildTrg(); buildRec();
    wireSubnav("#db-nav", show);
    window.addEventListener("tabshow", (e) => { if (e.detail === "db") show(current()); });
  }
  return { init };
})();
window.addEventListener("appready", DBLab.init);
