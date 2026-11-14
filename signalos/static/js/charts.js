/* Tiny SVG chart helpers (no libraries, works offline). */
const C = {
  ink: "#1b2127", muted: "#7b8790", grid: "#e3e8e8", sign: "#0a5c3a", guide: "#1f5fbf", stop: "#e2373c", warn: "#f2a007",
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function lineChart(data, { w = 360, h = 120, color = C.sign, fill = true, min = 0, fmt = (v) => v } = {}) {
  if (!data || data.length < 2) return `<div class="empty">Collecting data...</div>`;
  const pad = { l: 34, r: 8, t: 8, b: 16 };
  const max = Math.max(1, ...data) * 1.1;
  const lo = Math.min(min, ...data);
  const x = (i) => pad.l + (i / (data.length - 1)) * (w - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / (max - lo)) * (h - pad.t - pad.b);
  const pts = data.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  const ticks = [0, 0.5, 1].map((t) => lo + (max - lo) * t);
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img">
    ${ticks.map((t) => `<line x1="${pad.l}" x2="${w - pad.r}" y1="${y(t)}" y2="${y(t)}" stroke="${C.grid}"/><text x="${pad.l - 5}" y="${y(t) + 4}" text-anchor="end" font-size="10" fill="${C.muted}">${fmt(Math.round(t * 10) / 10)}</text>`).join("")}
    ${fill ? `<polygon points="${pad.l},${y(lo)} ${pts.join(" ")} ${w - pad.r},${y(lo)}" fill="${color}" opacity=".12"/>` : ""}
    <polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round"/>
    <circle cx="${x(data.length - 1)}" cy="${y(data[data.length - 1])}" r="3.5" fill="${color}"/>
  </svg>`;
}

/* Horizontal bars. items: [{label, value, color?, note?}] */
function barChart(items, { w = 420, unit = "", best = "min", fmt = (v) => v } = {}) {
  if (!items.length) return `<div class="empty">No data yet.</div>`;
  const max = Math.max(...items.map((i) => i.value), 0.0001);
  const vals = items.map((i) => i.value);
  const target = best === "min" ? Math.min(...vals) : best === "max" ? Math.max(...vals) : null;
  const rowH = 30, labelW = 92, valW = 62;
  const h = items.length * rowH + 4;
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" style="max-width:${Math.round(w * 1.45)}px" role="img">${items.map((it, k) => {
    const bw = ((w - labelW - valW) * it.value) / max;
    const isBest = target !== null && it.value === target;
    const col = it.color || (isBest ? C.sign : "#8c9a9f");
    return `<g transform="translate(0,${k * rowH + 2})">
      <text x="${labelW - 8}" y="18" text-anchor="end" font-size="13" font-weight="600" fill="${C.ink}">${esc(it.label)}</text>
      <rect x="${labelW}" y="4" width="${Math.max(2, bw)}" height="20" rx="3" fill="${col}"/>
      <text x="${labelW + Math.max(2, bw) + 6}" y="19" font-size="12.5" fill="${C.ink}" font-weight="${isBest ? 700 : 500}">${fmt(it.value)}${unit}</text>
    </g>`;
  }).join("")}</svg>`;
}

/* Vertical columns. items: [{label, value, color?}] */
function columns(items, { w = 420, h = 150, color = C.guide, fmt = (v) => v } = {}) {
  if (!items.length) return `<div class="empty">No data yet.</div>`;
  const max = Math.max(...items.map((i) => i.value), 1);
  const bw = (w - 20) / items.length;
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" style="max-width:${Math.round(w * 1.45)}px" role="img">${items.map((it, k) => {
    const bh = ((h - 34) * it.value) / max;
    return `<g><rect x="${10 + k * bw + 4}" y="${h - 20 - bh}" width="${bw - 8}" height="${bh}" rx="3" fill="${it.color || color}"/>
      <text x="${10 + k * bw + bw / 2}" y="${h - 22 - bh}" text-anchor="middle" font-size="11" fill="${C.ink}">${fmt(it.value)}</text>
      <text x="${10 + k * bw + bw / 2}" y="${h - 5}" text-anchor="middle" font-size="11" fill="${C.muted}">${esc(it.label)}</text></g>`;
  }).join("")}</svg>`;
}

/* Directed graph with circular layout. nodes: [id], edges: [{from,to,hot}] */
function graphSvg(nodes, edges, { w = 300, h = 220, prefix = "T" } = {}) {
  if (!nodes.length) return "";
  const cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 34;
  const pos = {};
  nodes.forEach((n, i) => {
    const a = (i / nodes.length) * Math.PI * 2 - Math.PI / 2;
    pos[n] = [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  });
  const arrows = edges.map((e) => {
    const [x1, y1] = pos[e.from], [x2, y2] = pos[e.to];
    if (e.from === e.to) return "";
    const dx = x2 - x1, dy = y2 - y1, d = Math.hypot(dx, dy) || 1;
    const twoWay = edges.some((o) => o.from === e.to && o.to === e.from), off = twoWay ? 7 : 0;
    const ox = (-dy / d) * off, oy = (dx / d) * off;   // shift so opposite arrows do not overlap
    const sx = x1 + (dx / d) * 20 + ox, sy = y1 + (dy / d) * 20 + oy, ex = x2 - (dx / d) * 22 + ox, ey = y2 - (dy / d) * 22 + oy;
    const col = e.hot ? C.stop : "#5d6b73";
    return `<line x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" stroke="${col}" stroke-width="2" marker-end="url(#ah${e.hot ? "h" : ""})"/>`;
  }).join("");
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" style="max-width:${w}px" role="img">
    <defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#5d6b73"/></marker>
    <marker id="ahh" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="${C.stop}"/></marker></defs>
    ${arrows}
    ${nodes.map((n) => `<g><circle cx="${pos[n][0]}" cy="${pos[n][1]}" r="18" fill="#fff" stroke="${C.ink}" stroke-width="2"/><text x="${pos[n][0]}" y="${pos[n][1] + 5}" text-anchor="middle" font-size="13" font-weight="700">${prefix}${n}</text></g>`).join("")}
  </svg>`;
}
