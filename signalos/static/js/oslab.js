/* OS lab: CPU scheduling arena, synchronisation, deadlock, memory. */
const OSLab = (() => {
  const built = {};
  const root = (id) => document.getElementById(id);
  const visible = (id) => App.tab === "os" && root(id).classList.contains("on");

  /* ------------------------------------------------------ CPU scheduling */
  function buildArena() {
    root("os-arena").innerHTML = `
      <p class="lead">An intersection is a CPU and each lane is a process. The arena runs the same city, the same demand and the same random seed under all five policies, so any difference comes from the scheduler alone.</p>
      <div class="panel"><div class="sign"><span>Policy arena</span><small>headless runs, same seed</small></div>
        <div class="body">
          <div class="row">
            <div class="field" style="width:190px"><label for="ar-ticks">Length: <b id="ar-ticks-v">600</b> ticks</label><input type="range" id="ar-ticks" min="200" max="1000" step="100" value="600"></div>
            <div class="field" style="width:210px"><label for="ar-demand">Demand: <b id="ar-demand-v">2.8</b>x (above 2.5 the city saturates)</label><input type="range" id="ar-demand" min="1.5" max="3.4" step="0.1" value="2.8"></div>
            <button class="btn primary" id="ar-run">Run all five policies</button>
          </div>
          <div id="ar-out" class="stack" style="margin-top:.9rem"><div class="empty">Press run. It takes a few seconds.</div></div>
        </div></div>
      <div class="grid2" style="margin-top:.9rem">
        <div class="panel"><div class="sign guide"><span>How each policy picks a lane</span></div><div class="body tiny" style="font-size:.9rem">
          <p><b>FCFS</b> serves the lane that has waited longest and keeps the green until its queue is empty.</p>
          <p><b>Round robin</b> gives each lane the same green time (the quantum) and rotates.</p>
          <p><b>SRTF</b> prefers the shortest queue and can pre-empt. Aging stops long queues from starving.</p>
          <p><b>Priority</b> ranks ambulances, then buses, then cars, and ages waiting lanes upward.</p>
          <p><b>MLFQ</b> starts every lane at the top level, demotes lanes that use their whole quantum and boosts everything every 30 ticks.</p></div></div>
        <div class="panel"><div class="sign guide"><span>What a context switch costs</span></div><div class="body" style="font-size:.9rem">
          <p>Moving the green from one lane to another needs an all-red clearance tick. In the Gantt strip in the command centre it shows as a hatched cell.</p>
          <p>A policy that switches too often wastes green time, just like a CPU that spends its time saving and loading registers.</p>
          <div class="callout info">Try it live: set Round robin with a quantum of 1 and watch the hatched cells multiply.</div></div></div>
      </div>`;
    root("ar-ticks").oninput = (e) => (root("ar-ticks-v").textContent = e.target.value);
    root("ar-demand").oninput = (e) => (root("ar-demand-v").textContent = (+e.target.value).toFixed(1));
    root("ar-run").onclick = runArena;
  }
  async function runArena() {
    const btn = root("ar-run"), out = root("ar-out");
    btn.disabled = true; btn.textContent = "Running...";
    out.innerHTML = `<div class="empty">Simulating five cities...</div>`;
    try {
      const r = await api(`/api/os/arena?ticks=${root("ar-ticks").value}&demand=${root("ar-demand").value}`);
      const rows = r.rows;
      const best = (k, dir) => rows.reduce((a, b) => ((dir === "min" ? b[k] < a[k] : b[k] > a[k]) ? b : a));
      const worst = (k, dir) => best(k, dir === "min" ? "max" : "min");
      const chart = (title, key, dir, unit = "") => `<div><div class="lbl">${title}</div>${barChart(rows.map((x) => ({ label: x.policy, value: x[key] ?? 0 })), { best: dir, unit })}</div>`;
      const w = worst("max_wait", "min"), b = best("avg_wait", "min"), t = best("exited", "max");
      out.innerHTML = `
        <div class="grid2">${chart("Trips completed (more is better)", "exited", "max")}${chart("Average wait at a light (fewer ticks is better)", "avg_wait", "min")}
        ${chart("Longest wait: the starvation check", "max_wait", "min")}${chart("Ambulance wait", "amb_avg_wait", "min")}</div>
        <div class="callout ok"><b>${t.policy}</b> completed the most trips (${t.exited}) and <b>${b.policy}</b> had the lowest average wait (${b.avg_wait} ticks).
        <b>${w.policy}</b> left one driver waiting ${w.max_wait} ticks at a single light, the clearest sign of starvation.</div>
        <div class="scroll"><table class="tbl"><thead><tr><th>Policy</th><th class="num">Trips</th><th class="num">Avg wait</th><th class="num">Longest wait</th><th class="num">Avg trip time</th><th class="num">Context switches</th><th class="num">Pre-emptions</th><th class="num">Ambulance wait</th><th class="num">Still in city</th></tr></thead><tbody>
        ${rows.map((x) => `<tr class="${x.policy === b.policy ? "best" : x.policy === w.policy ? "worst" : ""}"><td>${x.policy}</td><td class="num">${x.exited}</td><td class="num">${x.avg_wait}</td><td class="num">${x.max_wait}</td><td class="num">${x.avg_turnaround}</td><td class="num">${x.context_switches}</td><td class="num">${x.preemptions}</td><td class="num">${fmt(x.amb_avg_wait)}</td><td class="num">${x.in_city_end}</td></tr>`).join("")}
        </tbody></table></div><div class="tiny">Computed in ${r.seconds}s. Green row: lowest average wait. Red row: longest single wait.</div>`;
    } catch (e) { out.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
    btn.disabled = false; btn.textContent = "Run all five policies";
  }

  /* -------------------------------------------------------- synchronisation */
  function buildSync() {
    root("os-sync").innerHTML = `
      <p class="lead">Several things share state at once, so the city needs the classic synchronisation tools. Everything on this page is live.</p>
      <div class="grid2">
        <div class="panel"><div class="sign"><span>Producer and consumer</span><small>bounded buffer, real threads</small></div><div class="body stack" id="sy-buffer"></div></div>
        <div class="panel"><div class="sign"><span>Mutex on the intersection box</span><small id="sy-node"></small></div><div class="body stack" id="sy-mutex"></div></div>
        <div class="panel"><div class="sign"><span>Counting semaphores on roads</span><small>capacity = free slots</small></div><div class="body" id="sy-roads"></div></div>
        <div class="panel"><div class="sign"><span>Readers-writer lock</span><small>engine/sync.py</small></div><div class="body" style="font-size:.92rem">
          <p>The tick thread <b>writes</b> the whole city state once per tick. Every browser request that draws the map is a <b>reader</b>. Many readers can look at once, but a writer waits for none and readers wait for a writer.</p>
          <p>The lock prefers writers, so a stream of map requests can never starve the simulation.</p>
          <pre class="mono">with lock.write():   sim.step()
with lock.read():    return sim.snapshot()</pre></div></div>
      </div>`;
  }
  function updSync(s) {
    const b = s.sync.buffer, pct = Math.round((b.size / b.capacity) * 100);
    root("sy-buffer").innerHTML = `
      <div class="row spread"><span class="lbl">Entry buffer</span><b>${b.size} / ${b.capacity}</b></div>
      <div class="gantt" style="height:16px"><i style="flex:none;width:${pct}%;background:var(--guide)"></i></div>
      <table class="tbl"><tbody>
        <tr><td>Produced by the four gate threads</td><td class="num">${b.produced}</td></tr>
        <tr><td>Consumed by the simulation loop</td><td class="num">${b.consumed}</td></tr>
        <tr><td>Times a producer had to wait (buffer full)</td><td class="num">${b.blocked_puts}</td></tr>
        <tr><td>Cars waiting at the border for a free road slot</td><td class="num">${s.sync.backlog}</td></tr>
        <tr><td>Database writer queue (second producer/consumer pair)</td><td class="num">${s.db_queue}</td></tr></tbody></table>
      <div class="tiny">Threads: gate-N, gate-E, gate-S, gate-W (producers), tick (consumer), db-writer.</div>`;
    const bx = s.sync.box;
    root("sy-node").textContent = "at " + nodeName(s.scheduler.node);
    root("sy-mutex").innerHTML = `
      <p style="margin:0">Only the lane that holds the mutex may drive through the box. Crossing lanes would collide, so they wait.</p>
      <div class="row"><span class="lbl">Holder</span>${bx.holders.length ? `<span class="badge run">lane ${esc(bx.holders[0])}</span>` : `<span class="badge idle">free</span>`}
      <span class="lbl">Waiting</span>${bx.waiters.length ? bx.waiters.map((w) => `<span class="badge ready">lane ${esc(w)}</span>`).join(" ") : `<span class="tiny">none</span>`}</div>
      <div class="tiny">Click another intersection on the map to inspect it. The mutex is released and re-acquired every time the scheduler changes the green.</div>`;
    const roads = Object.values(s.sync.road_sem).sort((a, b) => a.name.localeCompare(b.name));
    root("sy-roads").innerHTML = `<table class="tbl"><thead><tr><th>Road</th><th>Slots</th><th class="num">Free</th><th>Cars waiting for a slot</th></tr></thead><tbody>
      ${roads.map((r) => { const used = r.capacity - r.value; return `<tr><td>${esc(roadName(r.name.replace("road ", "")))}</td>
        <td><span class="cap-pips">${Array.from({ length: r.capacity }, (_, i) => `<i class="${i < used ? (used >= r.capacity ? "full" : "on") : ""}"></i>`).join("")}</span></td>
        <td class="num">${r.value}</td><td class="tiny">${r.waiters.length ? r.waiters.slice(0, 3).map((w) => "#" + w).join(", ") : "-"}</td></tr>`; }).join("")}</tbody></table>
      <div class="tiny" style="margin-top:.4rem">A car must acquire a slot on the next road (P) before it leaves the current one (V). Holding one while waiting for another is how gridlock starts.</div>`;
  }

  /* ------------------------------------------------------------- deadlock */
  let bk = null;
  function buildDead() {
    root("os-dead").innerHTML = `
      <p class="lead">Gridlock is a textbook deadlock: a car holds its road slot (hold and wait), nobody can be forced out (no pre-emption), a slot can serve one car at a time (mutual exclusion) and four roads can wait on each other in a ring (circular wait).</p>
      <div class="grid2">
        <div class="panel"><div class="sign"><span>Live wait-for graph</span><small id="dd-mode"></small></div><div class="body stack" id="dd-live"></div></div>
        <div class="panel"><div class="sign"><span>Three strategies, same gridlock</span></div><div class="body stack">
          <p style="margin:0;font-size:.92rem">Each strategy gets an identical city and the same forced gridlock. This takes a couple of seconds.</p>
          <button class="btn primary" id="dd-cmp">Compare ignore, detect and avoid</button><div id="dd-cmp-out"></div></div></div>
      </div>
      <div class="panel" style="margin-top:.9rem"><div class="sign guide"><span>Banker's algorithm workbench</span><small>safe-state test and resource requests</small></div><div class="body stack" id="dd-bank"></div></div>`;
    root("dd-cmp").onclick = async () => {
      root("dd-cmp").disabled = true;
      try {
        const r = await api("/api/os/deadlock-modes");
        const name = { off: "Ignore", detect: "Detect and recover", avoid: "Avoid" };
        root("dd-cmp-out").innerHTML = `<table class="tbl"><thead><tr><th>Strategy</th><th class="num">Roads still stuck</th><th class="num">Cars towed</th><th class="num">Entries refused</th><th class="num">Trips done</th></tr></thead><tbody>
          ${r.map((x) => `<tr class="${x.mode === "off" ? "worst" : x.mode === "avoid" ? "best" : ""}"><td>${name[x.mode]}</td><td class="num">${x.stuck_roads}</td><td class="num">${x.towed}</td><td class="num">${x.denied}</td><td class="num">${x.exited}</td></tr>`).join("")}</tbody></table>
          <div class="callout info" style="margin-top:.6rem"><b>Ignore</b> leaves four roads frozen for good. <b>Detect</b> finds the cycle and tows a low-priority victim, so traffic resumes but cars are lost. <b>Avoid</b> refuses any move that would complete a cycle, so nothing is towed.</div>`;
      } catch (e) { root("dd-cmp-out").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
      root("dd-cmp").disabled = false;
    };
    api("/api/os/banker/preset").then((p) => { bk = p; renderBank(); });
  }
  function updDead(s) {
    const d = s.deadlock;
    root("dd-mode").textContent = { off: "ignoring", detect: "detect and recover", avoid: "avoidance on" }[d.mode];
    const waits = d.wait_for;
    const hot = new Set(d.cycles.flat());
    root("dd-live").innerHTML = (d.cycles.length
      ? `<div class="callout bad"><b>Deadlock.</b> ${d.cycles.length} cycle(s). Every road in the ring is full and its first car needs the next road.</div>` +
        d.cycles.map((c) => `<div class="chips">${c.map((r) => `<span class="chip fault">${esc(roadName(r))}</span>`).join(" &rarr; ")} &rarr; back to start</div>`).join("")
      : `<div class="callout ok">No cycle in the wait-for graph. ${waits.length} road${waits.length === 1 ? "" : "s"} waiting on a full road ahead.</div>`) +
      (waits.length ? `<div class="scroll" style="max-height:170px"><table class="tbl"><thead><tr><th>Road</th><th>waits for</th></tr></thead><tbody>${waits.map((w) => `<tr class="${hot.has(w.from) ? "worst" : ""}"><td>${esc(roadName(w.from))}</td><td>${esc(roadName(w.to))}</td></tr>`).join("")}</tbody></table></div>` : "") +
      `<div class="tiny">Go to the command centre and press "Force a gridlock" with "Ignore" selected to see this turn red.</div>`;
  }
  function renderBank() {
    const n = bk.maximum.length, m = bk.available.length;
    const grid = (name, mat) => `<div><div class="lbl">${name}</div><div class="matrix" style="grid-template-columns:repeat(${m},auto)">${mat.map((row, i) => row.map((v, j) => `<input type="number" min="0" data-m="${name}" data-i="${i}" data-j="${j}" value="${v}" aria-label="${name} car ${i} type ${j}">`).join("")).join("")}</div></div>`;
    root("dd-bank").innerHTML = `<p style="margin:0;font-size:.92rem">Rows are cars, columns are resource types (road slots of two kinds). Edit any number and test whether the state is safe.</p>
      <div class="row" style="align-items:flex-start;gap:1.5rem">${grid("allocation", bk.allocation)}${grid("maximum", bk.maximum)}
      <div><div class="lbl">available</div><div class="matrix" style="grid-template-columns:repeat(${m},auto)">${bk.available.map((v, j) => `<input type="number" min="0" data-m="available" data-i="0" data-j="${j}" value="${v}" aria-label="available type ${j}">`).join("")}</div></div></div>
      <div class="row"><button class="btn primary" id="bk-check">Check safety</button>
        <span class="lbl">Car</span><input type="number" id="bk-pid" min="0" max="${n - 1}" value="1" style="width:4rem">
        <span class="lbl">requests</span>${Array.from({ length: m }, (_, j) => `<input type="number" min="0" class="bk-req" value="${j === 0 ? 1 : 0}" style="width:4rem" aria-label="request type ${j}">`).join("")}
        <button class="btn" id="bk-req">Test request</button><button class="btn small" id="bk-reset">Reset</button></div>
      <div id="bk-out"></div>`;
    const read = () => {
      const v = { available: Array(m).fill(0), maximum: bk.maximum.map((r) => r.slice()), allocation: bk.allocation.map((r) => r.slice()) };
      $$("#dd-bank input[data-m]").forEach((inp) => { const val = Math.max(0, +inp.value || 0), k = inp.dataset.m; if (k === "available") v.available[+inp.dataset.j] = val; else v[k][+inp.dataset.i][+inp.dataset.j] = val; });
      return v;
    };
    const run = async (withReq) => {
      const body = read();
      if (withReq) { body.pid = +root("bk-pid").value; body.request = $$(".bk-req").map((i) => +i.value || 0); }
      try {
        const r = await api("/api/os/banker", body);
        root("bk-out").innerHTML = `<div class="callout ${r.safe ? "ok" : "bad"}"><b>${r.safe ? "Safe state." : "Unsafe state."}</b>
          ${r.safe ? `A safe sequence exists: ${r.sequence.map((i) => `<span class="chip">car ${i}</span>`).join(" &rarr; ")}` : "No order lets every car finish, so a deadlock is possible."}</div>
          ${r.request_result ? `<div class="callout ${r.request_result.granted ? "ok" : "warn"}" style="margin-top:.5rem"><b>${r.request_result.granted ? "Request granted." : "Request refused."}</b> ${esc(r.request_result.reason)}</div>` : ""}
          <div class="lbl" style="margin-top:.6rem">Need = maximum - allocation</div><div class="chips">${r.need.map((row, i) => `<span class="chip">car ${i}: [${row.join(", ")}]</span>`).join("")}</div>
          ${r.steps.length ? `<div class="scroll" style="max-height:180px;margin-top:.5rem"><table class="tbl"><thead><tr><th>Step</th><th>Car finishes</th><th>Work before</th><th>Work after</th></tr></thead><tbody>${r.steps.map((st, k) => `<tr><td>${k + 1}</td><td>car ${st.process}</td><td>[${st.work_before.join(", ")}]</td><td>[${st.work_after.join(", ")}]</td></tr>`).join("")}</tbody></table></div>` : ""}`;
      } catch (e) { root("bk-out").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
    };
    root("bk-check").onclick = () => run(false);
    root("bk-req").onclick = () => run(true);
    root("bk-reset").onclick = async () => { bk = await api("/api/os/banker/preset"); renderBank(); };
  }

  /* --------------------------------------------------------------- memory */
  function buildMem() {
    root("os-mem").innerHTML = `
      <p class="lead">The downtown garage works like physical memory: every bay is a page frame and every permit is a page. A permit that is not parked yet is a page fault, and when the garage is full a victim is evicted to the overflow lot.</p>
      <div class="grid2">
        <div class="panel"><div class="sign"><span>Live garage</span><small id="mm-stats"></small></div><div class="body stack">
          <div class="row"><span class="lbl">Eviction policy</span><div class="seg" id="mm-seg">${["FIFO", "LRU", "LFU", "CLOCK"].map((p) => `<button data-v="${p}" aria-pressed="false">${p}</button>`).join("")}</div></div>
          <div class="garage" id="mm-garage"></div>
          <div><div class="lbl">Recent requests</div><div class="chips" id="mm-log"></div></div>
          <div class="tiny">Bay badges: age = ticks since last use, freq = times used. A green dot is the Clock reference bit. A yellow outline is the Clock hand.</div></div></div>
        <div class="panel"><div class="sign"><span>Replay the real request stream</span><small>every policy, same stream</small></div><div class="body stack">
          <p style="margin:0;font-size:.92rem">Takes the last parking requests from the live city and replays them under each policy, plus OPT, which looks into the future and sets the best possible score.</p>
          <button class="btn primary" id="mm-replay">Replay the stream</button><div id="mm-replay-out"></div></div></div>
      </div>
      <div class="panel" style="margin-top:.9rem"><div class="sign guide"><span>Page-replacement workbench</span><small>your own reference string</small></div><div class="body stack">
        <div class="row"><div class="field" style="flex:1;min-width:260px"><label for="pw-refs">Reference string</label><input type="text" id="pw-refs" value="7 0 1 2 0 3 0 4 2 3 0 3 2 1 2 0 1 7 0 1"></div>
          <div class="field" style="width:110px"><label for="pw-frames">Frames</label><input type="number" id="pw-frames" min="1" max="12" value="3"></div>
          <button class="btn primary" id="pw-run">Run</button><button class="btn warn" id="pw-belady">Show Belady's anomaly</button></div>
        <div id="pw-out"></div></div></div>`;
    root("mm-seg").onclick = (e) => { const b = e.target.closest("button"); if (b) setParam({ parking_policy: b.dataset.v }); };
    root("mm-replay").onclick = async () => {
      try {
        const r = await api("/api/os/parking-replay");
        const items = Object.entries(r.results).map(([k, v]) => ({ label: k, value: Math.round(v.hit_ratio * 1000) / 10, color: k === "OPT" ? "#1f5fbf" : undefined }));
        root("mm-replay-out").innerHTML = `<div class="tiny">${r.references} requests, ${r.frames} bays. Hit ratio in percent, higher is better.</div>` + barChart(items.filter((i) => i.label !== "OPT").concat(items.filter((i) => i.label === "OPT")), { best: "max", unit: "%" });
      } catch (e) { root("mm-replay-out").innerHTML = `<div class="callout warn">${esc(e.message)}</div>`; }
    };
    root("pw-run").onclick = () => runPages(root("pw-refs").value, +root("pw-frames").value);
    root("pw-belady").onclick = belady;
  }
  function grid(res, label) {
    const steps = res.steps, nf = Math.max(...steps.map((s) => s.frames.length), 1);
    return `<div class="stack"><div class="row spread"><b>${label}</b><span class="badge ${res.faults > 0 ? "bad" : "ok"}">${res.faults} faults</span></div>
      <div class="scroll" style="max-height:none"><table class="pgrid"><thead><tr><th></th>${steps.map((s) => `<th>${s.page}</th>`).join("")}</tr></thead><tbody>
      ${Array.from({ length: nf }, (_, r) => `<tr><th>f${r + 1}</th>${steps.map((s) => `<td class="${s.fault ? "f" : "h"}">${s.frames[r] ?? ""}</td>`).join("")}</tr>`).join("")}
      <tr><th></th>${steps.map((s) => `<td class="${s.fault ? "f" : "h"}">${s.fault ? "F" : "H"}</td>`).join("")}</tr></tbody></table></div></div>`;
  }
  async function runPages(refs, frames) {
    try {
      const r = await api("/api/os/pages", { refs, frames });
      const items = Object.entries(r).map(([k, v]) => ({ label: k, value: v.faults, color: k === "OPT" ? "#1f5fbf" : undefined }));
      root("pw-out").innerHTML = `<div class="grid2"><div><div class="lbl">Page faults, fewer is better</div>${barChart(items, { best: "min" })}</div>
        <div class="callout info">OPT is a lower bound that no real policy can reach, because it needs to know future requests. LRU approximates it by assuming the recent past predicts the near future.</div></div>
        <div class="grid2" style="margin-top:.8rem">${["FIFO", "LRU", "LFU", "CLOCK", "OPT"].map((p) => grid(r[p], p)).join("")}</div>`;
    } catch (e) { root("pw-out").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  async function belady() {
    const refs = "1 2 3 4 1 2 5 1 2 3 4 5";
    root("pw-refs").value = refs;
    try {
      const [a, b] = await Promise.all([api("/api/os/pages", { refs, frames: 3 }), api("/api/os/pages", { refs, frames: 4 })]);
      root("pw-out").innerHTML = `<div class="callout bad"><b>Belady's anomaly.</b> FIFO has ${a.FIFO.faults} faults with 3 frames but ${b.FIFO.faults} faults with 4. More memory made it worse, because FIFO ignores how recently a page was used. LRU never does this (${a.LRU.faults} then ${b.LRU.faults}).</div>
        <div class="grid2" style="margin-top:.8rem">${grid(a.FIFO, "FIFO with 3 frames")}${grid(b.FIFO, "FIFO with 4 frames")}</div>`;
    } catch (e) { root("pw-out").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
  function updMem(s) {
    const m = s.memory;
    $$("#mm-seg button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === m.policy));
    root("mm-stats").textContent = `${(m.hit_ratio * 100).toFixed(0)}% hits, ${m.faults} faults, ${m.evictions} evictions`;
    root("mm-garage").innerHTML = m.frames.map((f, i) => f
      ? `<div class="bay ${m.policy === "CLOCK" && m.hand === i ? "hand" : ""}">${m.policy === "CLOCK" && f.ref ? `<span class="ref" title="reference bit"></span>` : ""}<b>${esc(f.page)}</b>age ${f.age}<br>freq ${f.freq}</div>`
      : `<div class="bay empty"><b>free</b>bay ${i + 1}</div>`).join("");
    root("mm-log").innerHTML = m.log.map((l) => `<span class="chip ${l.hit ? "hit" : "fault"}" title="${l.hit ? "hit" : "fault"}${l.evicted ? ", evicted " + l.evicted : ""}">${esc(l.plate)}${l.evicted ? " (out: " + esc(l.evicted) + ")" : ""}</span>`).join("");
  }

  /* --------------------------------------------------------------- wiring */
  function onState(s) {
    if (visible("os-sync")) updSync(s);
    if (visible("os-dead")) updDead(s);
    if (visible("os-mem")) updMem(s);
  }
  function init() {
    buildArena(); buildSync(); buildDead(); buildMem();
    wireSubnav("#os-nav", () => { if (App.state) onState(App.state); });
    App.listeners.push(onState);
    runPages("7 0 1 2 0 3 0 4 2 3 0 3 2 1 2 0 1 7 0 1", 3);
  }
  return { init };
})();
window.addEventListener("appready", OSLab.init);
