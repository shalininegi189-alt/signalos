/* Dehradun panel: demand scenarios, field-count upload, signal-plan download. */
const Dehradun = (() => {
  let scenarios = [];
  const setMsg = (t, bad) => { const m = $("#counts-msg"); m.textContent = t; m.style.color = bad ? "var(--stop)" : ""; };

  async function loadCounts() {
    try {
      const c = await api("/api/real/counts");
      setMsg(c.active ? `Your counts are active for ${Object.keys(c.weights).length} entry points.` : "Using the built-in weights.");
    } catch (e) { /* server not ready yet */ }
  }
  async function upload(file) {
    try {
      const r = await api("/api/real/counts", { csv: await file.text() });
      setMsg(r.message + (r.ignored.length ? ` Ignored: ${r.ignored.slice(0, 2).join("; ")}${r.ignored.length > 2 ? "..." : ""}` : ""));
      toast("Field counts applied");
    } catch (e) { setMsg(e.message, true); }
  }
  function sync(s) {
    const cur = s.params.scenario || "normal";
    $$("#seg-scn button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === cur));
    const sc = scenarios.find((x) => x.id === cur);
    if (sc) $("#scn-desc").textContent = sc.desc;
  }
  async function init() {
    scenarios = await api("/api/scenarios");
    $("#seg-scn").innerHTML = scenarios.map((s) => `<button data-v="${s.id}" aria-pressed="false">${esc(s.label)}</button>`).join("");
    $("#seg-scn").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setParam({ scenario: b.dataset.v }); });
    $("#counts-file").addEventListener("change", (e) => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ""; });
    $("#counts-clear").addEventListener("click", async () => { await api("/api/real/counts", { clear: true }); setMsg("Using the built-in weights."); });
    App.listeners.push(sync);
    loadCounts();
  }
  return { init };
})();
window.addEventListener("appready", Dehradun.init);
