const AUTO_REFRESH_MS = 30000;
const MANIFEST_URL = "data/manifest.json";
let manifest = { symbols: ["NVDA"] };
let currentSymbol = new URLSearchParams(location.search).get("symbol")?.toUpperCase() || "NVDA";

function fmt(n, d = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "--";
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
}
function clsFor(n) {
  if (Number(n) > 0) return "good";
  if (Number(n) < 0) return "bad";
  return "warn";
}
function dataUrlFor(symbol) {
  const clean = (symbol || "NVDA").toUpperCase().replace(/[^A-Z0-9.\-]/g, "");
  return clean === "NVDA" ? "data/symbols/NVDA.json" : `data/symbols/${clean}.json`;
}
async function loadManifest() {
  try {
    const res = await fetch(`${MANIFEST_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (res.ok) manifest = await res.json();
  } catch (_) {}
  const select = document.getElementById("watchlistSelect");
  const symbols = manifest.symbols || ["NVDA"];
  select.innerHTML = symbols.map(s => `<option value="${s}">${s}</option>`).join("");
  if (!symbols.includes(currentSymbol)) {
    const opt = document.createElement("option");
    opt.value = currentSymbol;
    opt.textContent = `${currentSymbol}（未在 watchlist）`;
    select.prepend(opt);
  }
  select.value = currentSymbol;
}
async function loadData(symbol = currentSymbol) {
  currentSymbol = (symbol || "NVDA").toUpperCase().replace(/[^A-Z0-9.\-]/g, "");
  const status = document.getElementById("refreshStatus");
  const input = document.getElementById("symbolInput");
  const select = document.getElementById("watchlistSelect");
  if (input) input.value = currentSymbol;
  if (select && [...select.options].some(o => o.value === currentSymbol)) select.value = currentSymbol;
  status.textContent = `loading ${currentSymbol}...`;

  let res = await fetch(`${dataUrlFor(currentSymbol)}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) {
    status.textContent = `${currentSymbol} 尚未產生資料；請把它加入 config/watchlist.json，等 GitHub Actions 更新。`;
    renderEmpty(currentSymbol);
    return;
  }
  const data = await res.json();
  render(data);
  status.textContent = `last: ${data.generated_at || "unknown"}`;
}
function renderEmpty(symbol) {
  render({
    generated_at: "--",
    snapshot: { symbol, price: null, change_pct: null },
    risk: { score: "--", label: "此 ticker 尚未在 watchlist 產生資料" },
    prepricing: { score: "--", label: "N/A" },
    options_pressure: { dealer_delta_hedge_shares: 0, label: "N/A" },
    price_series: [],
    events: [],
    cross_market: [],
    probability_distribution: {},
    narrative: `目前 GitHub Pages 版不能直接用前端 API key 即時查任意股票。請把 ${symbol} 加進 config/watchlist.json 的 watchlist，下一次 GitHub Actions 更新後就能搜尋。`
  });
}
function render(data) {
  const snap = data.snapshot || {};
  const risk = data.risk || {};
  const pre = data.prepricing || {};
  const opt = data.options_pressure || {};
  document.getElementById("riskScore").textContent = risk.score ?? "--";
  document.getElementById("riskLabel").textContent = risk.label || "--";
  document.getElementById("symbol").textContent = snap.symbol || "--";
  document.getElementById("price").innerHTML = `${fmt(snap.price)} <span class="${clsFor(snap.change_pct)}">${fmt(snap.change_pct)}%</span>`;
  document.getElementById("prepricing").textContent = pre.score !== undefined ? `${pre.score}/100` : "--";
  document.getElementById("prepricingLabel").textContent = pre.label || "--";
  document.getElementById("hedgePressure").textContent = opt.dealer_delta_hedge_shares !== undefined ? `${fmt(opt.dealer_delta_hedge_shares, 0)} 股` : "--";
  document.getElementById("hedgeLabel").textContent = opt.label || "--";
  renderPriceChart(data);
  renderDistribution(data);
  renderEvents(data);
  renderCrossMarket(data);
  document.getElementById("narrative").textContent = data.narrative || "";
}
function renderPriceChart(data) {
  const prices = data.price_series || [];
  const events = data.events || [];
  const x = prices.map(p => p.timestamp);
  const y = prices.map(p => p.close);
  const shapes = events.map(e => ({ type: "line", x0: e.timestamp, x1: e.timestamp, y0: 0, y1: 1, xref: "x", yref: "paper", line: { dash: "dot", width: 1, color: "#fbbf24" } }));
  const annotations = events.map(e => ({ x: e.timestamp, y: 1, xref: "x", yref: "paper", showarrow: false, text: e.event_type || "event", yanchor: "bottom", font: { size: 10, color: "#fbbf24" } }));
  Plotly.react("priceChart", [{ x, y, type: "scatter", mode: "lines+markers", name: data.snapshot?.symbol || "price", line: { width: 2, color: "#7dd3fc" } }], {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: "#edf2ff" }, margin: { l: 45, r: 20, t: 15, b: 40 }, xaxis: { gridcolor: "#25304a" }, yaxis: { gridcolor: "#25304a" }, shapes, annotations
  }, { responsive: true, displayModeBar: false });
}
function renderDistribution(data) {
  const q = data.probability_distribution || {};
  const labels = ["P10", "P25", "Median", "P75", "P90"];
  const values = [q.p10, q.p25, q.median, q.p75, q.p90];
  Plotly.react("distChart", [{ x: labels, y: values, type: "bar", marker: { color: "#a5b4fc" } }], {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: "#edf2ff" }, margin: { l: 45, r: 20, t: 15, b: 40 }, yaxis: { title: "return %", gridcolor: "#25304a" }, xaxis: { gridcolor: "#25304a" }, annotations: [{ x: "Median", y: q.median || 0, text: `Up prob: ${fmt((q.prob_up || 0) * 100, 1)}%`, showarrow: true, arrowhead: 2, font: { color: "#fde68a" } }]
  }, { responsive: true, displayModeBar: false });
}
function renderEvents(data) {
  const el = document.getElementById("eventList");
  const events = data.events || [];
  el.innerHTML = events.length ? events.map(e => `<div class="item"><strong>${e.title || "--"}</strong><div class="meta">${e.timestamp || ""}</div><span class="tag">${e.event_type || "event"}</span><span class="tag">severity ${e.severity ?? "--"}</span><span class="tag">sentiment ${e.sentiment ?? "--"}</span><div class="meta">${e.source || ""}</div></div>`).join("") : `<div class="item"><strong>尚無事件資料</strong><div class="meta">等待下一次資料更新</div></div>`;
}
function renderCrossMarket(data) {
  const el = document.getElementById("crossMarket");
  const rows = data.cross_market || [];
  el.innerHTML = rows.length ? rows.map(r => `<div class="item"><strong>${r.name || r.symbol}</strong><span class="tag">${r.symbol || ""}</span><span class="${clsFor(r.change_pct)}">${fmt(r.change_pct)}%</span><div class="meta">${r.interpretation || ""}</div></div>`).join("") : `<div class="item"><strong>尚無跨市場資料</strong><div class="meta">下一版會接 NQ / WTI / US2Y / VIX</div></div>`;
}
function setSymbol(symbol) {
  currentSymbol = (symbol || "NVDA").toUpperCase().replace(/[^A-Z0-9.\-]/g, "");
  const url = new URL(location.href);
  url.searchParams.set("symbol", currentSymbol);
  history.replaceState(null, "", url);
  loadData(currentSymbol).catch(err => { document.getElementById("refreshStatus").textContent = err.message; });
}

document.getElementById("refreshBtn").addEventListener("click", () => loadData(currentSymbol).catch(err => { document.getElementById("refreshStatus").textContent = err.message; }));
document.getElementById("symbolForm").addEventListener("submit", (e) => { e.preventDefault(); setSymbol(document.getElementById("symbolInput").value); });
document.getElementById("watchlistSelect").addEventListener("change", (e) => setSymbol(e.target.value));

loadManifest().then(() => loadData(currentSymbol)).catch(err => { document.getElementById("refreshStatus").textContent = err.message; });
setInterval(() => loadData(currentSymbol).catch(console.error), AUTO_REFRESH_MS);
