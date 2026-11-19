"use strict";
const $ = s => document.querySelector(s);
const state = {coin: localStorage.getItem("aha.coin") || "BTC", days: 365, dirty: {}, req: 0};
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (x, d = 2) => x == null ? "–" : Number(x).toLocaleString("it-IT", {maximumFractionDigits: d});
const pct = x => x == null ? "–" : (x * 100).toFixed(0) + "%";
const price = x => x == null ? "–" : fmt(x, x >= 100 ? 2 : x >= 1 ? 3 : 5);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

const NAMES = {ema: "EMA (trend)", rsi: "RSI", macd: "MACD", bb: "Bollinger", mvrv: "MVRV", exchange_flow: "Flussi exchange",
  sopr: "SOPR", nvt: "NVT", fear_greed: "Fear & Greed"};
const GROUPS = [
  ["EMA", [["ema_fast", "Veloce", 1, 2], ["ema_slow", "Lenta", 1, 5]]],
  ["RSI", [["rsi_n", "Periodo", 1, 2], ["rsi_lo", "Ipervenduto", 1, 1], ["rsi_hi", "Ipercomprato", 1, 1]]],
  ["MACD", [["macd_f", "Veloce", 1, 2], ["macd_s", "Lenta", 1, 3], ["macd_sig", "Segnale", 1, 2]]],
  ["Bande di Bollinger", [["bb_n", "Periodo", 1, 5], ["bb_k", "Deviazioni", 0.1, 0.5]]],
  ["On-chain e sentiment", [["mvrv_lo", "MVRV fondo", 0.1, 0], ["mvrv_hi", "MVRV picco", 0.1, 0], ["sopr_lo", "SOPR sotto", 0.01, 0.5],
    ["sopr_hi", "SOPR sopra", 0.01, 0.5], ["nvt_pct", "NVT percentile", 0.05, 0.5], ["flow_z", "Flussi z-score", 0.1, 0.5],
    ["fg_lo", "Paura sotto", 1, 0], ["fg_hi", "Avidità sopra", 1, 0]]],
  ["Operazione", [["horizon", "Orizzonte (giorni)", 1, 2], ["atr_stop", "Stop (×ATR)", 0.1, 0.3], ["atr_tp", "Target (×ATR)", 0.1, 0.3],
    ["min_votes", "Voti minimi", 1, 1], ["cost_bps", "Costi (bps)", 1, 0], ["slippage_bps", "Slippage (bps)", 1, 0]]],
];

async function api(path, opt) {
  const r = await fetch(path, opt);
  if (!r.ok) {
    let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {}
    throw new Error(m);
  }
  return r.json();
}

function query() {
  const q = new URLSearchParams({days: state.days});
  for (const [k, v] of Object.entries(state.dirty)) q.set(k, v);
  return q.toString();
}

// ---------------------------------------------------------------- segnale
function renderSignal(s) {
  const el = $("#signal");
  if (!s.params) { el.innerHTML = `<div class="err">${esc(s.why || s.action)}</div>`; return; }
  const h = s.history, o = h.out_of_sample, i = h.in_sample, b = h.baseline_out_of_sample, rel = s.reliability;
  const cls = s.side === "long" ? "long" : s.side === "short" ? "short" : "wait";
  const levels = s.side ? `<div class="kv">
      <div><span>Ingresso</span><b>${price(s.entry)}</b></div>
      <div><span>Stop loss</span><b style="color:var(--dn)">${price(s.stop_loss)}</b></div>
      <div><span>Take profit</span><b style="color:var(--up)">${price(s.take_profit)}</b></div>
      <div><span>Orizzonte · R/R</span><b>${s.horizon_days} gg · ${fmt(s.risk_reward, 1)}</b></div></div>` : `<p class="muted">Nessun segnale attivo: il punteggio (${fmt(s.score, 0)}) non raggiunge i ${s.min_votes} voti concordi richiesti. Prezzo ${price(s.price)} al ${esc(s.asof)}.</p>`;
  const stat = o.n ? `<b>${pct(o.win_rate)}</b> di operazioni in positivo su <b>${o.n}</b> fuori campione (dal ${esc(h.split_date)}),
      netto medio ${fmt(o.mean_net_pct)}%; target ${pct(o.target_rate)}, stop ${pct(o.stop_rate)}.
      ${b.n ? `Ingresso casuale nella stessa direzione: ${pct(b.win_rate)}.` : ""}
      In campione: ${i.n ? `${pct(i.win_rate)} su ${i.n}` : "–"}.` : "Nessuna operazione storica fuori campione.";
  const votes = s.votes.map(v => `<span class="chip ${v.vote > 0 ? "p" : v.vote < 0 ? "n" : ""}" title="${esc(v.family)}">${v.vote > 0 ? "▲" : v.vote < 0 ? "▼" : "•"} ${NAMES[v.indicator] || v.indicator}</span>`).join("");
  const miss = s.onchain_missing.length ? `<p class="muted small">Dati non disponibili per questa moneta, quindi non votano: ${s.onchain_missing.map(k => NAMES[k] || k).join(", ")}.</p>` : "";
  el.innerHTML = `<div class="row between"><div><span class="badge ${cls}">${esc(s.action)}</span>
      <span class="muted"> ${esc(state.coin)} · ${esc(s.asof)}</span></div>
      <div>Affidabilità <b class="${rel.grade === "alta" ? "alta" : rel.grade === "media" ? "media" : "bassa"}">${esc(rel.grade)}</b></div></div>
    ${s.positioning ? `<p><b>Posizionamento:</b> ${esc(s.positioning)}</p>` : ""}
    ${levels}<div class="chips">${votes}</div>
    <p class="small">${stat}</p><p class="muted small">${esc(rel.why)}.</p>${miss}
    <p class="muted small">${esc(s.note)}</p>`;
}

// ---------------------------------------------------------------- parametri
function renderControls(p) {
  const body = $("#ctl-body");
  if (!body.dataset.built) {
    body.innerHTML = GROUPS.map(([g, fs]) => `<div class="grp"><h3>${g}</h3><div class="fields">` +
      fs.map(([k, l, st]) => `<label>${l}<input type="number" inputmode="decimal" step="${st}" data-k="${k}"></label>`).join("") +
      `</div></div>`).join("");
    body.dataset.built = "1";
    body.addEventListener("input", e => {
      const k = e.target.dataset.k; if (!k || e.target.value === "") return;
      state.dirty[k] = e.target.value; e.target.classList.add("dirty");
      clearTimeout(state.t); state.t = setTimeout(load, 450);
    });
  }
  body.querySelectorAll("input").forEach(inp => {
    const k = inp.dataset.k;
    if (!(k in state.dirty) && document.activeElement !== inp) inp.value = p[k];
    inp.classList.toggle("dirty", k in state.dirty);
  });
  const n = Object.keys(state.dirty).length;
  $("#ctl-hint").textContent = n ? `· ${n} impostat${n === 1 ? "o" : "i"} da te` : "· automatici";
}

// ---------------------------------------------------------------- grafici
function renderChart(d) {
  const s = d.series, sig = d.signal, p = sig.params, t = s.t;
  const tx = css("--tx"), mut = css("--mut"), grid = css("--grid"), up = css("--up"), dn = css("--dn"), acc = css("--acc"), warn = css("--warn");
  const panels = [{id: "price", h: 340}];
  const add = (id, h = 120) => { if (s[id]) panels.push({id, h}); };
  if (s.rsi) panels.push({id: "rsi", h: 120});
  if (s.macd) panels.push({id: "macd", h: 130});
  ["mvrv", "netflow_7d", "sopr", "nvt", "fear_greed"].forEach(k => add(k));
  const total = panels.reduce((a, x) => a + x.h, 0) + panels.length * 14;
  let used = 0; const dom = {};
  [...panels].reverse().forEach(x => { dom[x.id] = [used / total, (used + x.h) / total]; used += x.h + 14; });
  const idx = Object.fromEntries(panels.map((x, i) => [x.id, i + 1]));
  const ax = id => idx[id] === 1 ? "" : idx[id];
  const traces = [], shapes = [], ann = [];
  const line = (id, name, y, color, w = 1.4, extra = {}) => traces.push(Object.assign({x: t, y, name, type: "scatter", mode: "lines",
    line: {color, width: w}, xaxis: "x" + ax(id), yaxis: "y" + ax(id), hovertemplate: "%{y:,.4~g}"}, extra));
  const hline = (id, y, color) => shapes.push({type: "line", xref: "paper", x0: 0, x1: 1, yref: "y" + ax(id), y0: y, y1: y,
    line: {color, width: 1, dash: "dot"}});

  traces.push({x: t, open: s.o, high: s.h, low: s.l, close: s.c, type: "candlestick", name: state.coin, xaxis: "x", yaxis: "y",
    increasing: {line: {color: up}}, decreasing: {line: {color: dn}}, showlegend: false});
  line("price", `EMA ${p.ema_fast}`, s.ema_fast, acc);
  line("price", `EMA ${p.ema_slow}`, s.ema_slow, warn);
  line("price", "BB sup.", s.bb_up, mut, 1, {line: {color: mut, width: 1, dash: "dot"}});
  line("price", "BB inf.", s.bb_lo, mut, 1, {line: {color: mut, width: 1, dash: "dot"}});
  // operazioni storiche del segnale
  const pos = Object.fromEntries(t.map((d0, i) => [d0, i]));
  const tr = (sig.history.recent_trades || []).filter(x => x.date in pos);
  if (tr.length) traces.push({x: tr.map(x => x.date), y: tr.map(x => s.c[pos[x.date]]), type: "scatter", mode: "markers", name: "Operazioni storiche",
    marker: {symbol: tr.map(x => x.dir > 0 ? "triangle-up" : "triangle-down"), size: 11, color: tr.map(x => x.net > 0 ? up : dn), line: {color: tx, width: 1}},
    text: tr.map(x => `${x.dir > 0 ? "long" : "short"} · esito ${x.exit} · ${(x.net * 100).toFixed(1)}%`), hovertemplate: "%{text}", showlegend: false});
  if (sig.side) {
    const x0 = t[Math.max(0, t.length - 25)], x1 = t[t.length - 1];
    [[sig.entry, tx, "ingresso"], [sig.stop_loss, dn, "stop"], [sig.take_profit, up, "target"]].forEach(([y, c, n]) => {
      shapes.push({type: "line", xref: "x", yref: "y", x0, x1, y0: y, y1: y, line: {color: c, width: 1.5, dash: "dash"}});
      ann.push({x: x1, y, xref: "x", yref: "y", text: n, showarrow: false, xanchor: "left", font: {color: c, size: 11}});
    });
  }
  if (s.rsi) { line("rsi", `RSI ${p.rsi_n}`, s.rsi, acc); hline("rsi", p.rsi_lo, up); hline("rsi", p.rsi_hi, dn); }
  if (s.macd) {
    traces.push({x: t, y: s.macd_hist, type: "bar", name: "Istogramma", xaxis: "x" + ax("macd"), yaxis: "y" + ax("macd"),
      marker: {color: s.macd_hist.map(v => v >= 0 ? up : dn)}, showlegend: false});
    line("macd", "MACD", s.macd, acc); line("macd", "Segnale", s.macd_signal, warn);
  }
  if (s.mvrv) { line("mvrv", "MVRV", s.mvrv, acc); hline("mvrv", p.mvrv_lo, up); hline("mvrv", p.mvrv_hi, dn); }
  if (s.netflow_7d) traces.push({x: t, y: s.netflow_7d, type: "bar", name: "Netflow exchange 7g (USD)", xaxis: "x" + ax("netflow_7d"),
    yaxis: "y" + ax("netflow_7d"), marker: {color: s.netflow_7d.map(v => v > 0 ? dn : up)}, showlegend: false});
  if (s.sopr) { line("sopr", "SOPR", s.sopr, acc); hline("sopr", 1, mut); }
  if (s.nvt) line("nvt", "NVT", s.nvt, acc);
  if (s.fear_greed) { line("fear_greed", "Fear & Greed", s.fear_greed, acc); hline("fear_greed", p.fg_lo, up); hline("fear_greed", p.fg_hi, dn); }

  const TITLES = {price: `Prezzo ${state.coin} · EMA · Bollinger`, rsi: "RSI", macd: "MACD", mvrv: "MVRV (verde = fondo, rosso = picco)",
    netflow_7d: "Netflow exchange 7g (rosso = afflusso)", sopr: "SOPR (sotto 1 = vendite in perdita)", nvt: "NVT",
    fear_greed: "Fear & Greed (paura / avidità)"};
  const layout = {height: total, margin: {l: 52, r: 44, t: 8, b: 28}, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: {color: tx, size: 11}, showlegend: false, shapes, annotations: ann, dragmode: "pan", hovermode: "x unified"};
  panels.forEach((x, i) => {
    const last = i === panels.length - 1, n = i + 1, sfx = n === 1 ? "" : n;
    layout["xaxis" + sfx] = {domain: [0, 1], anchor: "y" + sfx, matches: n === 1 ? undefined : "x", showgrid: true, gridcolor: grid,
      rangeslider: {visible: false}, showticklabels: last, tickfont: {color: mut}};
    layout["yaxis" + sfx] = {domain: dom[x.id], anchor: "x" + sfx, gridcolor: grid, tickfont: {color: mut}, zeroline: false, fixedrange: true};
    ann.push({xref: "paper", yref: "paper", x: 0, y: dom[x.id][1], xanchor: "left", yanchor: "bottom", showarrow: false,
      text: TITLES[x.id], font: {color: mut, size: 11}});
  });
  Plotly.react("chart", traces, layout, {displayModeBar: false, responsive: true, scrollZoom: false});
  $("#chart-note").textContent = "Trascina per spostarti nel tempo. I triangoli sono le operazioni storiche del segnale (verde = chiusa in positivo).";
}

// ---------------------------------------------------------------- caricamento
async function load() {
  const my = ++state.req;
  $("#status").textContent = "Calcolo…";
  try {
    const d = await api(`/api/aha/chart/${state.coin}?${query()}`);
    if (my !== state.req) return;
    renderSignal(d.signal); renderControls(d.signal.params || {}); renderChart(d);
    $("#status").textContent = "";
  } catch (e) {
    if (my !== state.req) return;
    $("#status").innerHTML = `<span class="err">${esc(e.message)}</span>`;
    $("#signal").innerHTML = `<div class="muted">Nessun dato per ${esc(state.coin)}: ${esc(e.message)}. Premi “Aggiorna”.</div>`;
  }
}

async function loadList() {
  try {
    const d = await api("/api/aha/signals");
    const sel = $("#coin");
    if (!sel.options.length || sel.options.length !== d.signals.length) {
      sel.innerHTML = d.signals.map(r => `<option value="${esc(r.id)}">${esc(r.id)} · ${esc(r.name)}</option>`).join("");
    }
    if (![...sel.options].some(o => o.value === state.coin) && sel.options.length) state.coin = sel.options[0].value;
    sel.value = state.coin;
    $("#list tbody").innerHTML = d.signals.map(r => `<tr data-id="${esc(r.id)}" class="${r.id === state.coin ? "sel" : ""}">
      <td><b>${esc(r.id)}</b></td>
      <td class="${r.side === "long" ? "alta" : r.side === "short" ? "bassa" : "muted"}">${esc(r.action)}</td>
      <td class="${(r.reliability || {}).grade === "alta" ? "alta" : (r.reliability || {}).grade === "media" ? "media" : "bassa"}">${esc((r.reliability || {}).grade || "–")}</td>
      <td>${r.n_oos ? `${pct(r.win_rate_oos)} <span class="muted">(${r.n_oos})</span>` : "–"}</td></tr>`).join("");
    return d.signals.length;
  } catch (e) { return 0; }
}

function pick(id) {
  if (id === state.coin) return;
  state.coin = id; state.dirty = {}; localStorage.setItem("aha.coin", id);
  $("#coin").value = id; document.querySelectorAll("#list tbody tr").forEach(r => r.classList.toggle("sel", r.dataset.id === id));
  load(); window.scrollTo({top: 0, behavior: "smooth"});
}

async function pollStatus() {
  try {
    const s = await api("/api/status");
    const st = s.state || s;
    if (st.running) { $("#status").textContent = "Aggiornamento in corso: " + (st.progress || ""); setTimeout(pollStatus, 4000); }
    else if (state.wasRunning) { state.wasRunning = false; await loadList(); load(); }
    state.wasRunning = !!st.running;
  } catch (e) {}
}

$("#coin").addEventListener("change", e => pick(e.target.value));
$("#list tbody").addEventListener("click", e => { const tr = e.target.closest("tr"); if (tr) pick(tr.dataset.id); });
$("#auto").addEventListener("click", () => { state.dirty = {}; load(); });
$("#range").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  state.days = +b.dataset.d; document.querySelectorAll("#range button").forEach(x => x.classList.toggle("on", x === b)); load();
});
$("#refresh").addEventListener("click", async () => {
  try { await api("/api/refresh", {method: "POST"}); state.wasRunning = true; pollStatus(); } catch (e) { $("#status").textContent = e.message; }
});
$("#theme").addEventListener("click", () => {
  const r = document.documentElement; r.dataset.theme = r.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem("aha.theme", r.dataset.theme); } catch (e) {}
  load();
});
try { const th = localStorage.getItem("aha.theme"); if (th) document.documentElement.dataset.theme = th; } catch (e) {}
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

window.addEventListener("DOMContentLoaded", async () => {
  const n = await loadList();
  if (n) load(); else { $("#signal").innerHTML = `<div class="muted">Nessuna analisi disponibile: premi “Aggiorna” per scaricare i dati.</div>`; pollStatus(); }
  pollStatus();
});
