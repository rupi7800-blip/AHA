// Global Financial Intelligence Platform - frontend (vanilla JS + Plotly)
const CLIENT_VERSION = "0.3.0";
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
// richieste con timeout: su rete mobile debole meglio un errore chiaro che un'attesa infinita
const api = async (p, opt = {}) => {
  const ctrl = new AbortController(), t = setTimeout(() => ctrl.abort(), opt.timeout || 30000);
  let r;
  try { r = await fetch("/api/" + p, { ...opt, signal: ctrl.signal }); }
  catch (e) { throw new Error(e.name === "AbortError" ? "il server non risponde (timeout)" : "server non raggiungibile: controlla la connessione"); }
  finally { clearTimeout(t); }
  setOffline(r.headers.get("X-AHA-Offline") === "1");
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const setOffline = on => { const o = document.getElementById("offline"); if (o) o.style.display = on ? "" : "none"; };
// Plotly è caricato in modo non bloccante: i grafici aspettano che sia pronto
const plotlyReady = () => window.Plotly ? Promise.resolve() : new Promise(res => { const i = setInterval(() => { if (window.Plotly) { clearInterval(i); res(); } }, 50); });
const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const toast = m => { const t = $("#toast"); t.textContent = m; t.style.display = "block"; setTimeout(() => t.style.display = "none", 3500); };
const fmt = (x, d = 2) => x == null || isNaN(x) ? "—" : Number(x).toLocaleString("it-IT", { maximumFractionDigits: d, minimumFractionDigits: Math.min(d, 2) });
const px = x => x == null ? "—" : Math.abs(x) >= 1000 ? fmt(x, 0) : Math.abs(x) >= 10 ? fmt(x, 2) : fmt(x, 4);
const pct = (x, d = 2) => x == null || isNaN(x) ? "—" : `<span class="${x > 0 ? "up" : x < 0 ? "down" : ""}">${x > 0 ? "+" : ""}${fmt(x * 100, d)}%</span>`;
const chgCell = (x, level) => level ? (x == null ? "—" : `<span class="${x > 0 ? "up" : x < 0 ? "down" : ""}">${x > 0 ? "+" : ""}${fmt(x * 100, 0)} pb</span>`) : pct(x);
const fresh = f => !f ? "" : f.startsWith("aggiornato") ? `<span class="chip ok">aggiornato</span>` : f === "mancante" ? `<span class="chip bad">mancante</span>` : `<span class="chip w" title="${esc(f)}">obsoleto</span>`;
const famName = { tecnica: "Tecnica", statistica: "Statistica", complessita: "Sistemi complessi", quantitativa: "Quantitativa", posizionamento: "Posizionamento", predittiva: "Modello predittivo", news: "News" };
const clsName = { down: "ribasso", flat: "laterale", up: "rialzo" };
let UNIV = null;
const layoutBase = (extra = {}) => ({ paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { family: "IBM Plex Sans, system-ui, sans-serif", color: css("--text"), size: 12 }, margin: { l: 50, r: 16, t: 24, b: 36 }, xaxis: { gridcolor: css("--line"), zerolinecolor: css("--line") }, yaxis: { gridcolor: css("--line"), zerolinecolor: css("--line") }, legend: { orientation: "h", y: 1.12 }, showlegend: innerWidth > 760, ...extra });
// topojsonURL: la mappa del mondo è inclusa nella piattaforma (funziona offline, nessun server esterno)
const plot = async (id, data, layout, cfg) => { await plotlyReady(); return Plotly.newPlot(id, data, layoutBase(layout), { displaylogo: false, responsive: true, topojsonURL: "/static/vendor/topojson/", ...cfg }); };

// ---------------------------------------------------------------- shell
$("#theme").onclick = () => { const h = document.documentElement; h.dataset.theme = h.dataset.theme === "dark" ? "light" : "dark"; try { localStorage.theme = h.dataset.theme } catch (e) { } route(); };
try { if (localStorage.theme) document.documentElement.dataset.theme = localStorage.theme } catch (e) { }
$("#newver").onclick = () => location.reload();
$("#ver") && ($("#ver").textContent = "Versione " + CLIENT_VERSION);
$("#burger").onclick = () => $("#side").classList.toggle("open");
$("#refresh").onclick = async () => { const r = await post("refresh"); toast(r.started ? "Aggiornamento avviato" : r.message); };
async function pollStatus() {
  try {
    const s = await api("status");
    $("#ver").textContent = "Versione " + CLIENT_VERSION + (s.version && s.version !== CLIENT_VERSION ? ` (server ${s.version})` : "");
    if (s.version && s.version !== CLIENT_VERSION) {
      if ($("#newver").style.display === "none") toast("È disponibile una nuova versione: premi \"Nuova versione: aggiorna\"");
      $("#newver").style.display = "";
    }
    if (!s.version) $("#ver").textContent += " · server vecchio: riavvia avvia.bat";
    $("#status").textContent = s.running ? `In corso: ${s.progress}` : s.last_run ? `Ultimo aggiornamento ${new Date(s.last_run).toLocaleString("it-IT")} · ${s.n_instruments} strumenti` : "Nessun aggiornamento completato: il primo ciclo richiede alcuni minuti";
    if (s.last_error) $("#status").textContent += " · errore nell'ultimo ciclo";
  } catch (e) { $("#status").textContent = "server non raggiungibile"; setOffline(true); }
}
// controllo dello stato: ogni 5 s durante un aggiornamento, ogni 30 s a riposo, mai con l'app in background
let _pollT = null;
async function pollLoop() {
  clearTimeout(_pollT);
  if (document.hidden) return;
  await pollStatus();
  _pollT = setTimeout(pollLoop, /^In corso/.test($("#status").textContent) ? 5000 : 30000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollLoop(); });
pollLoop();

// ---------------------------------------------------------------- app installabile
let _installEvt = null;
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(e => console.warn("service worker:", e));
}
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); _installEvt = e; $("#install").style.display = ""; });
window.addEventListener("appinstalled", () => { $("#install").style.display = "none"; toast("App installata"); });
$("#install").onclick = async () => { if (!_installEvt) return; _installEvt.prompt(); await _installEvt.userChoice; _installEvt = null; $("#install").style.display = "none"; };
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone;
window.addEventListener("hashchange", route);

async function route() {
  const h = location.hash.slice(1) || "panoramica";
  document.querySelectorAll(".side a").forEach(a => a.classList.toggle("active", a.getAttribute("href") === "#" + h.split("-")[0] || (h.startsWith("asset") && false)));
  $("#side").classList.remove("open");
  view.innerHTML = `<div class="empty">Caricamento…</div>`;
  try {
    if (!UNIV) UNIV = await api("universe");
    if (h.startsWith("asset-")) return await viewAsset(decodeURIComponent(h.slice(6)));
    const f = { panoramica: viewOverview, segnali: viewSimple, scanner: viewScanner, radar: viewRadar, news: viewNews, watchlist: viewWatch, backtest: viewBacktest, calibrazione: viewCalib, impostazioni: viewSettings }[h] || viewOverview;
    await f();
  } catch (e) { view.innerHTML = `<div class="card">Errore: ${esc(e.message)}</div>` }
}

// ---------------------------------------------------------------- panoramica
const GEO = { SPX: [40.7, -74], NDX: [40.7, -74], DJI: [40.7, -74], RUT: [40.7, -74], FTSEMIB: [45.46, 9.19], DAX: [50.11, 8.68], CAC: [48.85, 2.35], SX5E: [50.85, 4.35], UKX: [51.5, -0.12], N225: [35.68, 139.7], HSI: [22.3, 114.17], SSEC: [31.23, 121.47], SZSC: [22.54, 114.06], KOSPI: [37.57, 126.98], NIFTY: [19.07, 72.88], BVSP: [-23.55, -46.63] };

async function viewOverview() {
  const d = await api("overview");
  const g = d.global || {};
  const byMkt = {};
  d.instruments.forEach(i => (byMkt[i.market] = byMkt[i.market] || []).push(i));
  const vix = d.instruments.find(i => i.id === "VIX");
  const tile = (k, v, s) => `<div class="card tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s || ""}</div></div>`;
  const ar = g.network;
  view.innerHTML = `<h1>Panoramica globale</h1>
  <div class="grid g4">
    ${tile("VIX", vix ? px(vix.last) : "—", vix ? `regime volatilità: ${vix.vol_regime?.label ?? "—"} · ${vix.last_bar ?? ""}` : "")}
    ${tile("Spread high yield USA", g.USHY ? fmt(g.USHY.level, 2) + "%" : "—", g.USHY ? `1 mese: ${g.USHY.chg_1m != null ? (g.USHY.chg_1m * 100).toFixed(0) + " pb" : "—"} · FRED ${g.USHY.last_bar}` : "dato non disponibile")}
    ${tile("Curva USA 10a-2a", g.US10Y2Y ? fmt(g.US10Y2Y.level, 2) + " pt" : "—", g.US10Y2Y ? `${g.US10Y2Y.level < 0 ? "invertita" : "positiva"} · FRED ${g.US10Y2Y.last_bar}` : "dato non disponibile")}
    ${tile("Accoppiamento dei mercati", ar ? fmt(ar.absorption_ratio * 100, 1) + "%" : "—", ar ? `absorption ratio, ${fmt(ar.absorption_ratio_percentile, 0)}° percentile storico · ${ar.n_assets} asset` : "serve almeno un ciclo di analisi")}
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h2>Mappa dei mercati azionari (variazione 1 giorno)</h2><div id="map" class="plot" style="min-height:330px"></div></div>
    <div class="card"><h2>Rotazione tra asset class (media variazioni, proxy di flusso)</h2><div id="rot" class="plot" style="min-height:330px"></div>
      <div class="note">Calcolata dai prezzi: indica dove il mercato premia il capitale, non misura flussi reali di fondi o ETF (non disponibili da fonti gratuite).
      ${g.risk_on_off_1m ? `Risk-on vs beni rifugio a 1 mese: <b>${pct(g.risk_on_off_1m.spread)}</b> (azionario, rame, bitcoin contro oro e franco).` : ""}</div></div>
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h2>Principali anomalie e alert</h2>${d.alerts.length ? d.alerts.slice(0, 8).map(a => `<div class="row" style="padding:4px 0;border-bottom:1px solid var(--line)"><a href="#asset-${a.inst}">${esc(a.payload.asset)}</a> <span>${esc(a.payload.what)}</span> <span class="muted" style="margin-left:auto">${a.data_asof}</span></div>`).join("") : `<div class="muted">Nessun alert generato.</div>`}<div style="margin-top:8px"><a href="#watchlist">Tutti gli alert →</a></div></div>
    <div class="card"><h2>Eventi macro e notizie</h2>
      ${d.calendar && d.calendar.events?.length ? `<h3>Prossimi rilasci (FRED)</h3>${d.calendar.events.slice(0, 8).map(e => `<div>${e.date} · ${esc(e.release)}</div>`).join("")}` : `<div class="note">Calendario macro: imposta una chiave FRED gratuita in Impostazioni per vedere i prossimi rilasci (CPI, occupazione, PIL, PCE).</div>`}
      <h3>Temi in evidenza (GDELT)</h3>
      <table><tr><th>Tema</th><th class="n">Copertura (z)</th><th class="n">Tono (z)</th><th class="n">Rilevanza</th><th></th></tr>
      ${d.news_topics.sort((a, b) => (b.market_relevance || 0) - (a.market_relevance || 0)).map(t => `<tr class="click" onclick="location.hash='news'"><td>${esc(t.name)}</td><td class="n">${fmt(t.coverage_z, 1)}</td><td class="n">${fmt(t.tone_z, 1)}</td><td class="n">${fmt(t.market_relevance, 0)}</td><td>${t.status !== "ok" ? `<span class="chip bad">${esc(t.status || "n.d.")}</span>` : t.is_noise ? `<span class="chip">rumore</span>` : `<span class="chip w">notizia rilevante</span>`}</td></tr>`).join("")}</table>
    </div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Mercati</h2><div class="scroll">
  ${Object.entries(byMkt).map(([m, lst]) => `<h3>${esc(d.markets[m] || m)}</h3><table><tr><th>Strumento</th><th class="n">Ultimo</th><th class="n">1g</th><th class="n">1s</th><th class="n">1m</th><th class="n">3m</th><th class="n">1a</th><th>Volatilità</th><th class="n">Segnali</th><th>Dati</th></tr>
    ${lst.map(i => `<tr class="click" onclick="location.hash='asset-${i.id}'"><td>${esc(i.name)}</td><td class="n">${px(i.last)}</td>${["1g", "1s", "1m", "3m", "1a"].map(k => `<td class="n">${chgCell(i.chg?.[k], i.level)}</td>`).join("")}<td>${i.vol_regime ? `<span class="chip ${i.vol_regime.label === "alta" || i.vol_regime.label === "estrema" ? "w" : ""}">${i.vol_regime.label}</span>` : ""}</td><td class="n">${i.n_signals ?? ""}</td><td>${i.error ? `<span class="chip bad">${esc(i.error)}</span>` : i.status ? `<span class="chip">${esc(i.status)}</span>` : fresh(i.freshness)} <span class="muted" title="${esc(i.source)} · ${esc(i.delay)}">${i.last_bar || ""}</span></td></tr>`).join("")}</table>`).join("")}
  </div></div>`;
  const eq = d.instruments.filter(i => GEO[i.id] && i.chg);
  if (eq.length) {
    const v = eq.map(i => (i.chg["1g"] || 0) * 100);
    const mx = Math.max(1, ...v.map(Math.abs));
    plot("map", [{ type: "scattergeo", lat: eq.map(i => GEO[i.id][0] + (["NDX", "DJI", "RUT"].indexOf(i.id) + 1) * 1.6), lon: eq.map(i => GEO[i.id][1]), text: eq.map((i, k) => `${i.name}: ${v[k] >= 0 ? "+" : ""}${v[k].toFixed(2)}%`), hoverinfo: "text", mode: "markers+text", textposition: "top center", textfont: { size: 10, color: css("--muted") },
      marker: { size: 14, color: v, cmin: -mx, cmax: mx, colorscale: [[0, css("--down")], [0.5, css("--panel2")], [1, css("--up")]], line: { color: css("--line"), width: 1 }, colorbar: { title: "%", thickness: 10 } } }],
      { geo: { projection: { type: "natural earth" }, showland: true, landcolor: css("--panel2"), bgcolor: "rgba(0,0,0,0)", showocean: false, showcountries: true, countrycolor: css("--line"), coastlinecolor: css("--line"), showframe: false }, margin: { l: 0, r: 0, t: 0, b: 0 } });
  } else $("#map").innerHTML = `<div class="empty">In attesa dei dati</div>`;
  const rot = g.rotation || {};
  const ks = Object.keys(rot).filter(k => k !== "rates");
  if (ks.length) plot("rot", ["1s", "1m", "3m"].map((h, j) => ({ type: "bar", orientation: "h", name: h, y: ks.map(k => d.markets[k] || k), x: ks.map(k => (rot[k][h] || 0) * 100), marker: { color: [css("--accent"), "#8a7cff", css("--warn")][j] } })), { barmode: "group", xaxis: { title: "% media", gridcolor: css("--line") }, margin: { l: 150, r: 10, t: 30, b: 40 } });
  else $("#rot").innerHTML = `<div class="empty">In attesa dei dati</div>`;
}

// ---------------------------------------------------------------- segnali semplici
const getLS = (k, d) => { try { return localStorage[k] ?? d } catch (e) { return d } };
const setLS = (k, v) => { try { localStorage[k] = v } catch (e) { } };
function sigCard(x, cap, riskPct) {
  const cls = x.type === "attesa" ? "wait" : x.direction > 0 ? "buy" : "sell";
  const h = x.history || {};
  const perUnit = Math.abs(x.entry - x.stop);
  const qty = cap > 0 && perUnit > 0 ? (cap * riskPct / 100) / perUnit : null;
  const invest = qty ? qty * x.entry : null;
  const hist = h.n ? `Nel passato su ${esc(x.name)} questo segnale, con queste stesse regole di entrata e uscita, si è presentato <b>${h.n} volte</b>: <b>${fmt(h.win_rate * 100, 0)}%</b> chiuse in guadagno, risultato medio <b class="${h.avg_pct > 0 ? "up" : "down"}">${h.avg_pct > 0 ? "+" : ""}${fmt(h.avg_pct, 2)}%</b> per operazione dopo i costi. Caso peggiore ${fmt(h.worst_pct, 1)}%, migliore +${fmt(h.best_pct, 1)}%. Obiettivo raggiunto nel ${fmt(h.share_target * 100, 0)}% dei casi, stop nel ${fmt(h.share_stop * 100, 0)}%.`
    : `<span class="muted">${esc(h.note || "Non abbastanza casi nel passato per una verifica.")}</span>`;
  return `<div class="sig"><div class="hd"><span class="act ${cls}">${esc(x.action)}</span><a class="nm" href="#asset-${x.id}">${esc(x.name)}</a>
   <span style="margin-left:auto"><span class="dot ${x.quality}"></span>${esc(x.quality_text)}</span></div>
   <p>${esc(x.simple)}</p>
   <div class="lv"><div><div class="k">Entrata</div><div class="v">${px(x.entry)}</div><div class="p muted">${esc(x.type === "attesa" ? (x.direction > 0 ? "solo se sale sopra" : "solo se scende sotto") : "prezzo di oggi")}</div></div>
   <div><div class="k">Esci in perdita (stop)</div><div class="v down">${px(x.stop)}</div><div class="p down">${x.stop_pct > 0 ? "+" : ""}${fmt(x.stop_pct, 1)}%</div></div>
   <div><div class="k">Esci in guadagno (obiettivo)</div><div class="v up">${px(x.target)}</div><div class="p up">${x.target_pct > 0 ? "+" : ""}${fmt(x.target_pct, 1)}%</div></div></div>
   <p><b>Esci comunque entro:</b> ${x.exit_by} (${x.exit_by_bars} sedute${x.type === "attesa" ? ", compresa l'attesa del livello" : ""}) se non è scattato né lo stop né l'obiettivo · <b>Guadagno potenziale / rischio:</b> ${fmt(x.rr, 1)} a 1</p>
   ${qty ? `<p><b>Quanto investire</b> per rischiare al massimo ${fmt(riskPct, 1)}% del capitale (${fmt(cap * riskPct / 100, 0)} se scatta lo stop): circa <b>${fmt(qty, qty < 10 ? 4 : 0)}</b> unità, cioè ${fmt(invest, 0)} di controvalore${invest > cap ? ` <span class="warn">(più del capitale: serve leva, non adatto a chi inizia)</span>` : ""}.</p>` : ""}
   <p><b>Verifica sul passato:</b> ${hist}</p>
   <p><b>Quando lasciar perdere:</b> ${esc(x.invalidation)}</p>
   ${x.conflict ? `<div class="box-warn">Su questo strumento ci sono segnali in direzioni opposte: situazione incerta.</div>` : ""}
   <details><summary>Dettagli tecnici</summary><div class="note">${esc(x.signal_name)} · regola ${esc(x.rule)} (${famName[x.family] || x.family}) · intensità ${fmt(x.intensity, 0)} · ${esc(x.why)} · dati ${esc(x.source)} al ${x.asof} · ${esc(x.freshness)}</div></details></div>`;
}

async function viewSimple() {
  const d = await api("segnali");
  const show = getLS("simShow2", "all"), kind = getLS("simKind", "tutti");
  const cap = +getLS("simCap", 0), risk = +getLS("simRisk", 1);
  let L = d.signals.filter(x => show === "all" || (show === "vg" ? ["verde", "giallo"].includes(x.quality) : x.quality === "verde"));
  if (kind === "acquisto") L = L.filter(x => x.type === "diretto" && x.direction > 0);
  if (kind === "vendita") L = L.filter(x => x.type === "diretto" && x.direction < 0);
  if (kind === "attesa") L = L.filter(x => x.type === "attesa");
  const cnt = q => d.signals.filter(x => x.quality === q).length;
  const g = d.diag || {}, st = d.status || {};
  let diag = "";
  if (!d.version) diag = `Il programma in esecuzione è la versione precedente. Chiudi la finestra nera e fai di nuovo doppio clic su avvia.bat.`;
  else if (st.running && (g.analyzed_old || g.not_analyzed)) diag = `Analisi in corso con la nuova versione: ${g.analyzed_current} strumenti su ${g.instruments} pronti (${esc(st.progress)}). La pagina si aggiorna da sola.`;
  else if (g.analyzed_old || g.not_analyzed) diag = `${g.analyzed_old + g.not_analyzed} strumenti su ${g.instruments} non sono ancora stati analizzati con la nuova versione. Premi "Aggiorna" in alto a destra e attendi qualche minuto.`;
  if (st.running && !window._simTimer) window._simTimer = setTimeout(() => { window._simTimer = null; if (location.hash === "#segnali") viewSimple(); }, 15000);
  view.innerHTML = `<h1>Segnali semplici</h1>
  ${diag ? `<div class="box-warn">${diag}</div>` : ""}
  <div class="note">Strumenti analizzati: ${g.analyzed_current ?? "—"} su ${g.instruments ?? "—"} · con segnali attivi oggi: ${new Set(d.signals.map(x => x.id)).size} · senza segnali: ${g.no_signal ?? "—"} · non acquistabili direttamente (tassi, VIX): ${g.not_tradable ?? "—"}</div>
  <div class="card"><b>Come leggerli</b>
   <p class="note" style="color:var(--text)"><b>Entrata</b>: il prezzo a cui si entrerebbe. <b>Stop</b>: se il prezzo arriva lì si esce, accettando una piccola perdita, per non rischiarne una grande. <b>Obiettivo</b>: se il prezzo arriva lì si esce incassando il guadagno. <b>Esci comunque entro</b>: se dopo quella data non è successo nulla, si chiude. <b>Il semaforo</b> dice come ha funzionato lo stesso piano sullo stesso strumento negli anni passati: <span class="dot verde"></span>costante (${cnt("verde")}) <span class="dot giallo"></span>incerto (${cnt("giallo")}) <span class="dot rosso"></span>non ha funzionato (${cnt("rosso")}) <span class="dot grigio"></span>non verificabile (${cnt("grigio")}).</p>
   <p class="note" style="color:var(--text)"><b>Vendita</b> significa guadagnare se il prezzo scende: richiede strumenti appositi (vendita allo scoperto, ETF short, CFD) con rischi maggiori. Se stai iniziando, considera solo gli acquisti.</p>
   <div class="filters" style="margin:8px 0 0"><label>Mostra <select id="sshow"><option value="v" ${show === "v" ? "selected" : ""}>solo verdi</option><option value="vg" ${show === "vg" ? "selected" : ""}>verdi e gialli</option><option value="all" ${show === "all" ? "selected" : ""}>tutti</option></select></label>
   <label>Tipo <select id="skind">${["tutti", "acquisto", "vendita", "attesa"].map(k => `<option ${k === kind ? "selected" : ""}>${k}</option>`).join("")}</select></label>
   <label>Il tuo capitale <input type="number" id="scap" value="${cap || ""}" placeholder="es. 10000" style="width:110px"></label>
   <label>Rischio massimo per operazione <input type="number" id="srisk" value="${risk}" step="0.5" min="0.1" max="5" style="width:60px">%</label></div>
   <div class="note">Il capitale serve solo a calcolare quanto investire: resta nel tuo browser. Una regola diffusa tra i professionisti è non rischiare più dell'1-2% del capitale su una singola operazione.</div></div>
  <div class="box-warn">Questi segnali sono il risultato di regole statistiche applicate a dati pubblici, verificate sul passato. Il passato non garantisce il futuro e non sono una consulenza finanziaria: decidi sempre tu se e quanto investire.</div>
  <div class="grid g2" style="margin-top:14px">${L.map(x => sigCard(x, cap, risk)).join("") || `<div class="card empty">${d.signals.length ? `Ci sono ${d.signals.length} segnali, ma nessuno corrisponde ai filtri scelti. Imposta "Mostra: tutti" e "Tipo: tutti".` : diag ? "I segnali compariranno al termine dell'analisi." : "Oggi nessuno strumento presenta segnali: è normale nei periodi tranquilli. Ricontrolla dopo il prossimo aggiornamento."}</div>`}</div>`;
  $("#sshow").onchange = e => { setLS("simShow2", e.target.value); viewSimple(); };
  $("#skind").onchange = e => { setLS("simKind", e.target.value); viewSimple(); };
  $("#scap").onchange = e => { setLS("simCap", e.target.value || 0); viewSimple(); };
  $("#srisk").onchange = e => { setLS("simRisk", e.target.value || 1); viewSimple(); };
}

// ---------------------------------------------------------------- scanner
async function viewScanner() {
  const kind = sessionStorage.scanKind || "";
  const d = await api("scanner" + (kind ? `?kind=${encodeURIComponent(kind)}` : ""));
  view.innerHTML = `<h1>Opportunity Scanner</h1>
  <div class="filters"><label>Tipo di opportunità <select id="kind"><option value="">tutte</option>${d.types.map(t => `<option ${t === kind ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
  <span class="note">Punteggio = somma delle intensità per famiglia di analisi × pesi impostati, +25% se ≥3 famiglie concordano, × affidabilità dei dati. Rischio = ATR in % del prezzo.</span></div>
  <div class="card scroll">${d.results.length ? `<table><tr><th>#</th><th>Strumento</th><th class="n">Punteggio</th><th>Direzione</th><th>Famiglie</th><th class="n">Prob. stimata</th><th class="n">Movimento atteso</th><th class="n">Rischio (ATR%)</th><th>Orizzonte</th><th>Dati</th><th>Segnali</th></tr>
  ${d.results.map((r, k) => `<tr class="click" onclick="location.hash='asset-${r.id}'"><td>${k + 1}</td><td><b>${esc(r.name)}</b><div class="muted">${px(r.price)}</div></td><td class="n">${fmt(r.score, 0)}</td>
   <td>${r.direction > 0 ? `<span class="up">rialzo</span>` : r.direction < 0 ? `<span class="down">ribasso</span>` : `<span class="muted">non direzionale</span>`}${r.convergence ? ` <span class="chip w">convergenza</span>` : ""}</td>
   <td>${r.families.map(f => `<span class="chip">${famName[f] || f}</span>`).join("")}</td>
   <td class="n">${r.probability ? `↑${fmt(r.probability.up * 100, 0)}% ↓${fmt(r.probability.down * 100, 0)}%` : `<span class="muted" title="Nessun modello con vantaggio fuori campione dimostrato">n.d.</span>`}</td>
   <td class="n">${r.potential_move ? "±" + px(r.potential_move) : "—"}</td><td class="n">${fmt(r.risk_atr_pct, 2)}</td><td>${esc(r.potential_horizon || "")}</td><td>${fresh(r.freshness)}</td>
   <td>${r.signals.map(s => `<div title="${esc(s.why)}">${s.direction > 0 ? "▲" : s.direction < 0 ? "▼" : "◆"} ${esc(s.name)} <span class="muted">(${fmt(s.intensity, 0)})</span></div>`).join("")}</td></tr>`).join("")}</table>` : `<div class="empty">Nessuna opportunità con i filtri attuali, oppure l'analisi non è ancora stata eseguita.</div>`}</div>`;
  $("#kind").onchange = e => { sessionStorage.scanKind = e.target.value; viewScanner(); };
}

// ---------------------------------------------------------------- radar
async function viewRadar() {
  const d = await api("radar");
  view.innerHTML = `<h1>Market Radar</h1>
  <div class="card"><h2>Condizioni dei mercati (colore = posizione nella distribuzione tra gli strumenti, valore reale al passaggio del mouse)</h2><div id="heat" class="plot" style="min-height:${Math.max(420, d.rows.length * 18 + 80)}px"></div></div>
  <div class="card" style="margin-top:14px"><h2>Confronto tra strumenti</h2><div class="filters">${[0, 1, 2].map(i => `<select id="cmp${i}"><option value="">—</option>${d.rows.map(r => `<option value="${r.id}" ${i === 0 && r.id === "SPX" || i === 1 && r.id === "GOLD" || i === 2 && r.id === "BTC" ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select>`).join("")}</div><div id="polar" class="plot" style="min-height:420px"></div>
  <div class="note">Ogni asse è il rango percentile dello strumento rispetto agli altri strumenti monitorati sullo stesso indicatore.</div></div>`;
  if (!d.rows.length) { $("#heat").innerHTML = `<div class="empty">In attesa dell'analisi</div>`; return; }
  const F = d.fields;
  const rankCol = k => { const vals = d.rows.map(r => r.values[k]).filter(v => v != null).sort((a, b) => a - b); return r => r.values[k] == null ? null : vals.filter(v => v < r.values[k]).length / Math.max(1, vals.length - 1); };
  const rankers = F.map(f => rankCol(f.key));
  const z = d.rows.map(r => rankers.map(fn => fn(r)));
  const txt = d.rows.map(r => F.map(f => `${r.name}<br>${f.label}: ${r.values[f.key] == null ? "n.d." : fmt(r.values[f.key], 2)}`));
  plot("heat", [{ type: "heatmap", z, x: F.map(f => f.label), y: d.rows.map(r => r.name), text: txt, hoverinfo: "text", colorscale: [[0, "#2b6cb0"], [0.5, css("--panel2")], [1, "#d9822b"]], zmin: 0, zmax: 1, showscale: false, xgap: 1, ygap: 1 }],
    { margin: { l: 190, r: 10, t: 10, b: 110 }, xaxis: { tickangle: -35 }, yaxis: { autorange: "reversed" } });
  const drawPolar = () => {
    const ids = [0, 1, 2].map(i => $("#cmp" + i).value).filter(Boolean);
    plot("polar", ids.map(id => { const r = d.rows.find(x => x.id === id); const vals = rankers.map(fn => fn(r)); return { type: "scatterpolar", r: [...vals, vals[0]], theta: [...F.map(f => f.label), F[0].label], fill: "toself", name: r.name, opacity: .6 }; }), { polar: { bgcolor: "rgba(0,0,0,0)", radialaxis: { range: [0, 1], gridcolor: css("--line") }, angularaxis: { gridcolor: css("--line") } }, margin: { l: 60, r: 60, t: 30, b: 30 } });
  };
  [0, 1, 2].forEach(i => $("#cmp" + i).onchange = drawPolar); drawPolar();
}

// ---------------------------------------------------------------- asset
function probBar(p) {
  if (!p) return "—";
  return `<div class="prob" title="ribasso ${fmt(p.down * 100, 0)}% · laterale ${fmt(p.flat * 100, 0)}% · rialzo ${fmt(p.up * 100, 0)}%"><span style="width:${p.down * 100}%;background:var(--down)"></span><span style="width:${p.flat * 100}%;background:var(--muted)"></span><span style="width:${p.up * 100}%;background:var(--up)"></span></div><div class="muted" style="font-size:11.5px">↓${fmt(p.down * 100, 0)}% · ↔${fmt(p.flat * 100, 0)}% · ↑${fmt(p.up * 100, 0)}%</div>`;
}
const kvRows = obj => `<div class="kv">${obj.map(([k, v]) => `<div>${k}</div><div>${v}</div>`).join("")}</div>`;

async function viewAsset(id) {
  const a = await api("asset/" + encodeURIComponent(id));
  if (a.pending) { view.innerHTML = `<h1>${esc(a.meta.name)}</h1><div class="card">Analisi non ancora disponibile. ${a.meta.fetch ? `Ultimo download: ${esc(a.meta.fetch.status)} ${esc(a.meta.fetch.message || "")}` : "Dati non ancora scaricati."}</div>`; return; }
  const m = a.meta, t = a.technical || {}, s = a.statistical || {}, c = a.complexity || {}, q = a.quant || {};
  if (a.error) { view.innerHTML = `<h1>${esc(m.name)}</h1><div class="card">${esc(a.error)}</div>`; return; }
  const P = a.predictions || {};
  const vr = s.vol_regime || {};
  view.innerHTML = `<div class="row"><h1 style="margin-right:auto">${esc(m.name)} <span class="muted" style="font-size:14px">${px(m.last_close)} ${chgCell(m.chg_1d, m.level_series)}</span></h1>
    <button class="btn ghost small" id="addwatch">+ Watchlist</button></div>
  <div class="note">Fonte: ${esc(m.source)} · ritardo: ${esc(m.delay)} · ultima barra ${m.last_bar} · ${m.n_bars} barre dal ${m.first_bar} · scaricato ${m.fetched_at ? new Date(m.fetched_at).toLocaleString("it-IT") : "—"} · ${fresh(m.freshness)} ${m.note ? "· " + esc(m.note) : ""}</div>
  <div class="card"><div id="price" class="plot" style="min-height:520px"></div></div>
  <div class="grid g3" style="margin-top:14px">
   <div class="card"><h2>A · Analisi tecnica</h2>${kvRows([
    ["Trend primario (settimanale)", t.trend_primario], ["Trend secondario (giornaliero)", t.trend_secondario], ["Trend intraday", t.trend_intraday ?? "n.d."], ["Timeframe", t.allineamento_timeframe],
    ["RSI 14", fmt(t.rsi14, 1)], ["ADX", fmt(t.adx, 1) + (t.adx > 25 ? " (trend)" : " (debole)")], ["MACD istogramma (norm.)", fmt(t.macd_hist_norm, 2)], ["ATR", px(t.atr) + ` (${fmt(t.atr_pct, 2)}%)`],
    ["Posizione nelle Bollinger", fmt(t.bb_pos, 2)], ["Ampiezza Bollinger (percentile 1a)", fmt(t.bb_width_pct, 0)], ["Media 50 / 200", px(t.sma50) + " / " + px(t.sma200)], ["Distanza VWAP 20g (ATR)", fmt(t.vwap20_dist_atr, 2)],
    ["Supporti", (t.supporti || []).map(px).join(" · ") || "—"], ["Resistenze", (t.resistenze || []).map(px).join(" · ") || "—"],
    ["Fibonacci (ultimo anno)", t.fibonacci ? Object.entries(t.fibonacci.levels).map(([k, v]) => `${k}: ${px(v)}`).join("<br>") : "—"], ["Punto di controllo volumi (120g)", t.volume_profile ? px(t.volume_profile.poc) : "n.d."]])}</div>
   <div class="card"><h2>B · Analisi statistica</h2>${kvRows([
    ["Z-score 20g", fmt(s.zscore20, 2)], ["Z-score vs media 200g", fmt(s.zscore_sma200, 2)],
    ["Regressione 120g: pendenza annua", s.regression_120g ? fmt(s.regression_120g.slope_ann_pct, 1) + "% (R² " + fmt(s.regression_120g.r2, 2) + ")" : "—"],
    ["Posizione nel canale (z)", s.regression_120g ? fmt(s.regression_120g.z_in_channel, 2) : "—"],
    ["Asimmetria / curtosi in eccesso", s.distribution ? fmt(s.distribution.skew, 2) + " / " + fmt(s.distribution.excess_kurtosis, 1) : "—"],
    ["Normalità (Jarque-Bera p)", s.distribution ? (s.distribution.jarque_bera_p < 0.01 ? "rifiutata (p<0,01)" : fmt(s.distribution.jarque_bera_p, 3)) : "—"],
    ["GARCH(1,1) vol. annua attuale", s.garch ? fmt(s.garch.vol_ann_now * 100, 1) + "%" : "—"], ["GARCH vol. di lungo periodo", s.garch ? fmt(s.garch.vol_ann_long_run * 100, 1) + "%" : "—"],
    ["Persistenza (α+β)", s.garch ? fmt(s.garch.persistence, 3) : "—"], ["Regime di volatilità", vr.label ? `${vr.label} (${fmt(vr.percentile, 0)}° pct, ${fmt(vr.vol20_ann * 100, 1)}% annuo)` : "—"],
    ["Mean reversion: dimezzamento", s.mean_reversion && isFinite(s.mean_reversion.half_life) ? fmt(s.mean_reversion.half_life, 0) + " giorni" : "non mean-reverting"],
    ["Test ADF (p)", s.mean_reversion ? fmt(s.mean_reversion.adf_p, 3) + (s.mean_reversion.adf_p < 0.05 ? " stazionaria" : " non stazionaria") : "—"],
    ["Cambi di regime recenti (CUSUM)", (s.regime_breaks || []).slice(-3).map(b => `${b.date.slice(0, 10)} ${b.type}`).join("<br>") || "nessuno"]])}</div>
   <div class="card"><h2>C · Sistemi complessi</h2>
    ${[["Esponente di Hurst (DFA)", c.hurst_dfa, v => fmt(v.value, 3) + ` · ultimi 500g: ${fmt(v.recent_500, 3)}`], ["Entropia di permutazione", c.permutation_entropy, v => fmt(v.value, 3)], ["Esponente di coda (Hill)", c.tail_alpha, v => fmt(v.value, 2)],
      ["Allerta precoce (varianza+autocorr.)", c.early_warning, v => v.kendall_tau_variance == null ? "—" : `τ var ${fmt(v.kendall_tau_variance, 2)} · τ AC ${fmt(v.kendall_tau_autocorr, 2)}${v.flag ? " · attivo" : ""}`], ["Dimensione frattale", c.fractal_dimension, v => fmt(v.value, 3)]]
      .map(([n, v, f]) => v ? `<div style="margin-bottom:8px"><b>${n}</b> <span class="chip ${v.validity}">${v.validity}</span><div style="font-family:var(--mono)">${f(v)}</div><div class="note">${esc(v.note)}</div></div>` : "").join("")}
    <div><b>Centralità nella rete di correlazioni</b> <span class="chip validato">validato</span><div style="font-family:var(--mono)">${a.centrality != null ? fmt(a.centrality, 2) + " (1 = il più connesso)" : "—"}</div></div>
    ${a.spillover?.length ? `<div style="margin-top:8px"><b>Diffusione degli shock verso questo asset</b><div class="note">Rendimenti passati di altri asset correlati con quello di oggi (significativi dopo correzione di Bonferroni).</div>${a.spillover.map(x => `<div>${esc(x.source)} con ${x.lag} g di anticipo: ρ ${fmt(x.corr, 2)}</div>`).join("")}</div>` : ""}
   </div>
  </div>
  <div class="grid g2" style="margin-top:14px">
   <div class="card"><h2>D · Analisi quantitativa</h2>${kvRows([
    ["Momentum 1m / 3m / 6m / 12m (σ)", ["mom_21g_volaj", "mom_63g_volaj", "mom_126g_volaj", "mom_252g_volaj"].map(k => fmt(q[k], 2)).join(" / ")],
    ["Volume (z-score)", fmt(q.volume_z, 2)], ["Chaikin Money Flow 20g", fmt(q.cmf20, 3)],
    ...(q.taker_buy_ratio_7g != null ? [["Acquisti aggressivi 7g / 90g", fmt(q.taker_buy_ratio_7g * 100, 1) + "% / " + fmt(q.taker_buy_ratio_90g * 100, 1) + "%"]] : [])])}
    ${q.taker_note ? `<div class="note">${esc(q.taker_note)}</div>` : ""}
    ${a.positioning ? `<h3>Posizionamento (CFTC COT)</h3>${kvRows([["Speculatori netti / open interest", fmt(a.positioning.spec_net_pct_oi * 100, 1) + "%"], ["Percentile 3 anni", fmt(a.positioning.percentile_3a, 0)], ["Variazione settimanale", fmt(a.positioning.spec_net_change_1w, 0) + " contratti"], ["Report", a.positioning.report_date + " · " + esc(a.positioning.market || "")]])}<div id="cot" class="plot" style="min-height:200px"></div>` : ""}
   </div>
   <div class="card"><h2>Dati non disponibili per questo strumento</h2>${(a.unavailable || []).map(u => `<div style="margin-bottom:6px"><b>${esc(u.what)}</b><div class="note" style="margin:0">${esc(u.reason)}</div></div>`).join("") || "—"}</div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Motore predittivo</h2>
   <div class="note">Classi: rialzo/ribasso se il rendimento supera ±0,5 volatilità attese sull'orizzonte, altrimenti laterale. Le probabilità del modello sono mostrate solo se battono il tasso storico fuori campione (Brier skill &gt; 0 con limite inferiore al 90% &gt; 0); in caso contrario si mostra il tasso storico.</div>
   <div class="scroll"><table><tr><th>Orizzonte</th><th>Stima mostrata</th><th>Fonte della stima</th><th>Tasso storico</th><th class="n">Skill logit</th><th class="n">Skill analoghi</th><th class="n">Prev. fuori campione</th><th class="n">Vol. attesa</th><th class="n">Banda 5-95%</th><th class="n">Soglia classe</th></tr>
   ${Object.entries(P).map(([h, w]) => { const lg = w.models.logit || {}, an = w.models.analog || {}, br = w.models.base_rate || {}; const b = w.bands;
     return `<tr><td><b>${esc(h)}</b>${w.timeframe ? ` <span class="muted">(${w.timeframe})</span>` : ""}</td><td>${probBar(w.probabilities)}</td>
     <td>${w.shown_as_probability ? `<span class="chip ok">modello ${w.selected}</span>` : `<span class="chip w" title="Nessun modello ha vantaggio dimostrato fuori campione">solo tasso storico</span>`}</td>
     <td>${br.current ? `↓${fmt(br.current[0] * 100, 0)} ↔${fmt(br.current[1] * 100, 0)} ↑${fmt(br.current[2] * 100, 0)}` : "—"}</td>
     <td class="n">${lg.brier_skill != null ? fmt(lg.brier_skill, 3) : esc(lg.status || "—")}</td><td class="n">${an.brier_skill != null ? fmt(an.brier_skill, 3) : esc(an.status || "—")}</td>
     <td class="n">${lg.n_oos || 0}${lg.oos_start ? `<div class="muted">dal ${lg.oos_start}</div>` : ""}</td><td class="n">${w.expected_vol_pct != null ? fmt(w.expected_vol_pct, 1) + "%" : px(w.expected_move_abs)}</td>
     <td class="n">${b ? px(b.p5) + " – " + px(b.p95) : "—"}</td><td class="n">${m.level_series ? px(w.threshold_now) : "±" + fmt((w.threshold_now || 0) * 100, 2) + "%"}</td></tr>`; }).join("")}</table></div>
   <div class="grid g2" style="margin-top:10px"><div><h3>Calibrazione fuori campione (classe rialzo, 1 settimana)</h3><div id="rel" class="plot" style="min-height:260px"></div></div>
   <div><h3>Livelli di inversione e accelerazione</h3>${kvRows([["Accelerazione sopra", px(a.levels?.accelerazione_sopra)], ["Accelerazione sotto", px(a.levels?.accelerazione_sotto)], ["Possibili inversioni (resistenze)", (a.levels?.inversione_resistenze || []).map(px).join(" · ") || "—"], ["Possibili inversioni (supporti)", (a.levels?.inversione_supporti || []).map(px).join(" · ") || "—"]])}<div class="note">${esc(a.levels?.note)}</div>
   <h3>Registro previsioni (ultime)</h3><div class="scroll" style="max-height:200px"><table><tr><th>Creata</th><th>Orizzonte</th><th>Modello</th><th>↓ ↔ ↑</th><th>Esito</th></tr>${(a.prediction_log || []).slice(0, 20).map(p => `<tr><td>${p.created_at.slice(0, 10)}<div class="muted">dati al ${String(p.data_asof).slice(0, 10)}</div></td><td>${esc(p.horizon)}</td><td>${p.model}</td><td>${fmt(p.p_down * 100, 0)} ${fmt(p.p_flat * 100, 0)} ${fmt(p.p_up * 100, 0)}</td><td>${p.realized_class ? clsName[p.realized_class] : `<span class="muted">aperta</span>`}</td></tr>`).join("")}</table></div></div></div>
  </div>
  <div class="grid g2" style="margin-top:14px">
   ${(a.simple?.plans || []).length ? `<div style="grid-column:1/-1"><h2>Segnali semplici</h2><div class="grid g2">${a.simple.plans.map(p => sigCard({ ...p, signal_name: p.name, id, name: m.name, source: m.source, asof: a.simple.asof, freshness: m.freshness, conflict: a.simple.conflict }, +getLS("simCap", 0), +getLS("simRisk", 1))).join("")}</div></div>` : ""}
   <div class="card"><h2>Segnali attivi</h2>${(a.signals || []).length ? a.signals.map(x => `<div class="alert"><div class="t">${x.direction > 0 ? "▲" : x.direction < 0 ? "▼" : "◆"} ${esc(x.name)} <span class="chip">${famName[x.family]}</span> <span class="muted">intensità ${fmt(x.intensity, 0)}</span></div><div>${esc(x.why)}</div><div class="note">Invalidazione: ${esc(x.invalidation)}</div><a href="#backtest" onclick="sessionStorage.bt='${id}|${x.rule}'">Verifica storica di questo segnale →</a></div>`).join("") : `<div class="muted">Nessun segnale attivo.</div>`}</div>
   <div class="card"><h2>Notizie collegate</h2>${(a.related_news || []).map(n => `<h3>${esc(n.topic)} <span class="muted">rilevanza ${fmt(n.summary.market_relevance, 0)}</span></h3>${n.articles.map(x => `<div style="margin-bottom:4px"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a> <span class="muted">${esc(x.domain)}</span></div>`).join("")}`).join("") || `<div class="muted">Nessun tema di notizie collegato a questo strumento.</div>`}</div>
  </div>`;
  $("#addwatch").onclick = async () => { const st = await api("settings"); if (!st.watchlist.find(w => w.id === id)) st.watchlist.push({ id }); await post("settings", { watchlist: st.watchlist }); toast("Aggiunto alla watchlist"); };
  const ch = a.chart;
  const traces = [{ type: "candlestick", x: ch.t, open: ch.o, high: ch.h, low: ch.l, close: ch.c, name: "prezzo", increasing: { line: { color: css("--up") } }, decreasing: { line: { color: css("--down") } }, yaxis: "y" },
    { x: ch.t, y: ch.sma50, name: "media 50", line: { width: 1, color: css("--accent") } }, { x: ch.t, y: ch.sma200, name: "media 200", line: { width: 1, color: css("--warn") } },
    { x: ch.t, y: ch.bb_up, name: "Bollinger", line: { width: .7, dash: "dot", color: css("--muted") } }, { x: ch.t, y: ch.bb_lo, name: "Bollinger inf.", showlegend: false, line: { width: .7, dash: "dot", color: css("--muted") } },
    { x: ch.t, y: ch.rsi, name: "RSI", yaxis: "y2", line: { width: 1, color: "#8a7cff" } }];
  if (ch.v.some(v => v > 0)) traces.push({ type: "bar", x: ch.t, y: ch.v, name: "volume", yaxis: "y3", marker: { color: css("--line") } });
  if (ch.garch_vol) traces.push({ x: ch.t, y: ch.garch_vol, name: "vol. GARCH annua %", yaxis: "y4", line: { width: 1, color: css("--down") } });
  plot("price", traces, { xaxis: { rangeslider: { visible: false }, gridcolor: css("--line"), range: [ch.t[Math.max(0, ch.t.length - 260)], ch.t[ch.t.length - 1]] }, yaxis: { domain: [0.42, 1], gridcolor: css("--line") }, yaxis2: { domain: [0.28, 0.4], range: [0, 100], gridcolor: css("--line"), title: "RSI" }, yaxis3: { domain: [0.14, 0.26], gridcolor: css("--line"), title: "vol" }, yaxis4: { domain: [0, 0.12], gridcolor: css("--line"), title: "GARCH" }, margin: { l: 55, r: 10, t: 30, b: 30 } });
  if (a.positioning?.series) { const sr = a.positioning.series; plot("cot", [{ x: Object.keys(sr), y: Object.values(sr).map(v => v * 100), type: "scatter", fill: "tozeroy", line: { color: css("--accent") }, name: "% OI" }], { margin: { l: 40, r: 10, t: 10, b: 30 }, showlegend: false }); }
  const rel = (P["1 settimana"]?.models?.[P["1 settimana"]?.selected !== "base_rate" ? P["1 settimana"].selected : "logit"] || {}).reliability_up;
  if (rel?.length) plot("rel", [{ x: [0, 1], y: [0, 1], mode: "lines", line: { dash: "dot", color: css("--muted") }, name: "calibrazione perfetta" }, { x: rel.map(r => r.pred), y: rel.map(r => r.obs), text: rel.map(r => `n=${r.n}`), mode: "lines+markers", name: "modello", line: { color: css("--accent") } }], { xaxis: { title: "probabilità prevista", range: [0, Math.max(.6, ...rel.map(r => r.pred)) + .05], gridcolor: css("--line") }, yaxis: { title: "frequenza osservata", range: [0, Math.max(.6, ...rel.map(r => r.obs)) + .05], gridcolor: css("--line") }, margin: { l: 50, r: 10, t: 10, b: 40 } });
  else $("#rel").innerHTML = `<div class="empty">Non disponibile</div>`;
}

// ---------------------------------------------------------------- news
async function viewNews() {
  const f = JSON.parse(sessionStorage.newsF || "{}");
  const qs = new URLSearchParams(Object.entries(f).filter(([k, v]) => v)).toString();
  const d = await api("news?" + qs);
  view.innerHTML = `<h1>News Intelligence</h1>
  <div class="filters">
   <select id="ftopic"><option value="">tutti i temi</option>${d.topics.map(t => `<option value="${t.id}" ${f.topic === t.id ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select>
   <select id="fasset"><option value="">tutti gli asset</option>${UNIV.instruments.map(i => `<option value="${i.id}" ${f.asset === i.id ? "selected" : ""}>${esc(i.name)}</option>`).join("")}</select>
   <select id="fsent"><option value="">ogni sentiment</option>${["positivo", "negativo", "neutro"].map(x => `<option ${f.sentiment === x ? "selected" : ""}>${x}</option>`).join("")}</select>
   <select id="frel"><option value="">ogni fonte</option>${["alta", "media", "non classificata"].map(x => `<option ${f.reliability === x ? "selected" : ""}>${x}</option>`).join("")}</select>
   <label><input type="checkbox" id="fnov" ${f.only_novel ? "checked" : ""}> solo notizie nuove</label></div>
  <div class="note">Fonte: ${esc(d.source)}. Il tono per tema è calcolato da GDELT sul testo degli articoli; il sentiment del singolo titolo usa un lessico finanziario semplice ed è indicativo. "Rumore" = copertura e tono nella norma degli ultimi 90 giorni.</div>
  <div class="grid g2"><div class="card"><h2>Temi</h2><table><tr><th>Tema</th><th class="n">Articoli 48h</th><th class="n">Copertura z</th><th class="n">Tono</th><th class="n">Tono z</th><th class="n">Novità</th><th class="n">Fonti alte</th><th class="n">Rilevanza</th></tr>
   ${d.topics.map(t => { const s = t.summary; return `<tr><td>${esc(t.name)} ${t.status !== "ok" ? `<span class="chip bad" title="${esc(t.message)}">errore fonte</span>` : s.is_noise ? `<span class="chip">rumore</span>` : `<span class="chip w">rilevante</span>`}</td><td class="n">${s.n_articles ?? "—"}</td><td class="n">${fmt(s.coverage_z, 1)}</td><td class="n">${fmt(s.tone_now, 2)}</td><td class="n">${fmt(s.tone_z, 1)}</td><td class="n">${s.share_novel != null ? fmt(s.share_novel * 100, 0) + "%" : "—"}</td><td class="n">${s.share_high_reliability != null ? fmt(s.share_high_reliability * 100, 0) + "%" : "—"}</td><td class="n">${fmt(s.market_relevance, 0)}</td></tr>`; }).join("")}</table></div>
  <div class="card"><h2>Tono nel tempo</h2><div id="tone" class="plot"></div></div></div>
  <div class="card" style="margin-top:14px"><h2>Articoli (${d.articles.length})</h2>${d.articles.map(x => `<div style="padding:6px 0;border-bottom:1px solid var(--line)"><span class="${x.sentiment > 0 ? "up" : x.sentiment < 0 ? "down" : "muted"}">${x.sentiment > 0 ? "▲" : x.sentiment < 0 ? "▼" : "●"}</span> <a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>
    <div class="muted" style="font-size:12px">${esc(x.domain)} · fonte ${esc(x.reliability)} · ${new Date(x.date).toLocaleString("it-IT")} · ${esc(x.topic)} ${x.novel ? "" : "· già nota"} · asset: ${x.assets.join(", ")}</div></div>`).join("") || `<div class="empty">Nessun articolo.</div>`}</div>`;
  const save = () => { sessionStorage.newsF = JSON.stringify({ topic: $("#ftopic").value, asset: $("#fasset").value, sentiment: $("#fsent").value, reliability: $("#frel").value, only_novel: $("#fnov").checked }); viewNews(); };
  ["#ftopic", "#fasset", "#fsent", "#frel", "#fnov"].forEach(s => $(s).onchange = save);
  const tr = d.topics.filter(t => t.summary.tone_series).map(t => ({ x: Object.keys(t.summary.tone_series), y: Object.values(t.summary.tone_series), name: t.name, mode: "lines", line: { width: 1.2 } }));
  if (tr.length) plot("tone", tr, { yaxis: { title: "tono medio GDELT", gridcolor: css("--line") }, margin: { l: 50, r: 10, t: 40, b: 30 } }); else $("#tone").innerHTML = `<div class="empty">Non disponibile</div>`;
}

// ---------------------------------------------------------------- watchlist & alert
async function viewWatch() {
  const [st, al, ov] = await Promise.all([api("settings"), api("alerts"), api("overview")]);
  const map = Object.fromEntries(ov.instruments.map(i => [i.id, i]));
  view.innerHTML = `<h1>Watchlist e alert</h1>
  <div class="card"><h2>Watchlist</h2><div class="filters"><select id="wadd">${UNIV.instruments.map(i => `<option value="${i.id}">${esc(i.name)}</option>`).join("")}</select><button class="btn small" id="wbtn">Aggiungi</button></div>
  <table><tr><th>Strumento</th><th class="n">Ultimo</th><th class="n">1g</th><th class="n">Segnali</th><th>Soglia sopra</th><th>Soglia sotto</th><th></th></tr>
  ${st.watchlist.map((w, k) => { const i = map[w.id] || {}; return `<tr><td><a href="#asset-${w.id}">${esc(i.name || w.id)}</a></td><td class="n">${px(i.last)}</td><td class="n">${chgCell(i.chg?.["1g"], i.level)}</td><td class="n">${i.n_signals ?? ""}</td><td><input type="number" step="any" data-k="${k}" data-f="above" value="${w.above ?? ""}" style="width:110px"></td><td><input type="number" step="any" data-k="${k}" data-f="below" value="${w.below ?? ""}" style="width:110px"></td><td><button class="btn ghost small" data-del="${k}">rimuovi</button></td></tr>`; }).join("") || `<tr><td colspan="7" class="muted">Watchlist vuota.</td></tr>`}</table>
  <div class="note">Le soglie generano un alert al primo ciclo di analisi in cui vengono superate.</div></div>
  <div class="card" style="margin-top:14px"><h2>Alert (${al.alerts.length})</h2>
  ${al.alerts.map(a => { const p = a.payload; return `<div class="alert" style="${a.read ? "opacity:.6" : ""}"><div class="row"><span class="t"><a href="#asset-${a.inst}">${esc(p.asset)}</a> · ${esc(p.what)}</span><span class="chip">${esc(a.kind)}</span><span class="muted" style="margin-left:auto">dati al ${a.data_asof} · generato ${new Date(a.created_at).toLocaleString("it-IT")}</span>${a.read ? "" : `<button class="btn ghost small" data-read="${a.id}">letto</button>`}</div>
    <div><b>Indicatori:</b> ${p.indicators.map(esc).join(", ")}</div><div><b>Perché può contare:</b> ${esc(p.why)}</div>
    ${p.scenarios?.length ? `<div><b>Scenari possibili:</b><ul>${p.scenarios.map(s => `<li>${esc(s)}</li>`).join("")}</ul></div>` : ""}
    <div><b>Cosa invaliderebbe il segnale:</b> ${esc(p.invalidation)}</div><div class="note">${esc(p.data)}</div></div>`; }).join("") || `<div class="muted">Nessun alert.</div>`}</div>`;
  const saveW = async wl => { await post("settings", { watchlist: wl }); viewWatch(); };
  $("#wbtn").onclick = () => { const id = $("#wadd").value; if (!st.watchlist.find(w => w.id === id)) st.watchlist.push({ id }); saveW(st.watchlist); };
  view.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { st.watchlist.splice(+b.dataset.del, 1); saveW(st.watchlist); });
  view.querySelectorAll("input[data-k]").forEach(inp => inp.onchange = () => { st.watchlist[+inp.dataset.k][inp.dataset.f] = inp.value === "" ? null : +inp.value; post("settings", { watchlist: st.watchlist }).then(() => toast("Soglia salvata")); });
  view.querySelectorAll("[data-read]").forEach(b => b.onclick = async () => { await post(`alerts/${b.dataset.read}/read`); viewWatch(); });
}

// ---------------------------------------------------------------- backtest
async function viewBacktest() {
  const [preset_id, preset_rule] = (sessionStorage.bt || "SPX|momentum_strong").split("|");
  const rules = UNIV.rules.filter(r => !["cot_extreme", "model_edge"].includes(r.id));
  view.innerHTML = `<h1>Backtest e verifica dei segnali</h1>
  <div class="filters"><select id="bti">${UNIV.instruments.filter(i => i.frequency !== "monthly").map(i => `<option value="${i.id}" ${i.id === preset_id ? "selected" : ""}>${esc(i.name)}</option>`).join("")}</select>
  <select id="btr">${rules.map(r => `<option value="${r.id}" ${r.id === preset_rule ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select>
  <label>orizzonte (barre) <input type="number" id="bth" style="width:70px" placeholder="auto"></label><button class="btn small" id="btgo">Esegui</button><button class="btn ghost small" id="btall">Stessa regola su tutti gli strumenti</button></div>
  <div class="note">Ingresso alla chiusura del giorno dopo il segnale, uscita dopo l'orizzonte, un'operazione alla volta, costi e slippage da Impostazioni su andata e ritorno. "Vantaggio" = rendimento medio netto meno rendimento medio incondizionato sullo stesso orizzonte e direzione.</div>
  <div id="btout"></div>`;
  const run = async () => {
    const id = $("#bti").value, rule = $("#btr").value, h = $("#bth").value; sessionStorage.bt = `${id}|${rule}`;
    $("#btout").innerHTML = `<div class="empty">Calcolo…</div>`;
    try {
      const r = await api(`backtest/${id}?rule=${rule}${h ? "&horizon=" + h : ""}`);
      const st = x => x.n ? [["Operazioni", x.n], ["% positive", fmt(x.hit_rate * 100, 1) + "%"], ["Rendimento medio netto", fmt(x.mean_net_pct, 3) + "%"], ["Sharpe (annuo)", fmt(x.sharpe, 2)], ["Sortino (annuo)", fmt(x.sortino, 2)], ["Max drawdown", fmt(x.max_drawdown_pct, 2) + "%"], ["Guadagno/perdita medi", fmt(x.payoff, 2)]] : [["Operazioni", 0]];
      $("#btout").innerHTML = `<div class="grid g3"><div class="card"><h2>Intero periodo</h2>${kvRows(st(r.all))}<div class="kv" style="margin-top:6px"><div>Incondizionato</div><div>${fmt(r.unconditional_mean_pct, 3)}%</div><div>Vantaggio</div><div>${r.edge_vs_unconditional_pct != null ? fmt(r.edge_vs_unconditional_pct, 3) + "%" : "n &lt; 20"}</div><div>t-stat</div><div>${fmt(r.t_stat, 2)}</div></div></div>
       <div class="card"><h2>Prima metà</h2>${kvRows(st(r.first_half))}</div><div class="card"><h2>Seconda metà (fuori campione rispetto alla prima)</h2>${kvRows(st(r.second_half))}</div></div>
       <div class="card" style="margin-top:14px"><h2>Curva cumulata (somma rendimenti netti %, ${r.direction > 0 ? "long" : "short"}, orizzonte ${r.horizon_bars} barre, costi ${r.costs_bps_round_trip} pb)</h2><div id="eq" class="plot"></div></div>
       <div class="box-warn">Un t-stat sotto 2 o metà del campione in disaccordo indicano che il segnale potrebbe essere rumore. Provare molte regole su molti strumenti produce falsi positivi: il risultato di un singolo test va letto con cautela.</div>`;
      plot("eq", [{ y: r.equity, mode: "lines", line: { color: css("--accent") } }], { showlegend: false, xaxis: { title: "operazione n.", gridcolor: css("--line") }, yaxis: { title: "%", gridcolor: css("--line") } });
    } catch (e) { $("#btout").innerHTML = `<div class="card">${esc(e.message)}</div>`; }
  };
  $("#btgo").onclick = run;
  $("#btall").onclick = async () => {
    $("#btout").innerHTML = `<div class="empty">Calcolo su tutti gli strumenti…</div>`;
    const r = await api("backtest_all/" + $("#btr").value);
    $("#btout").innerHTML = `<div class="card scroll"><table><tr><th>Strumento</th><th class="n">Oper.</th><th class="n">% positive</th><th class="n">Medio netto %</th><th class="n">Vantaggio %</th><th class="n">t-stat</th><th class="n">Sharpe</th><th class="n">1ª metà medio</th><th class="n">2ª metà medio</th></tr>
     ${r.results.sort((a, b) => (b.t_stat ?? -99) - (a.t_stat ?? -99)).map(x => `<tr><td>${esc(x.name)}</td><td class="n">${x.all.n}</td><td class="n">${x.all.n ? fmt(x.all.hit_rate * 100, 1) : "—"}</td><td class="n">${fmt(x.all.mean_net_pct, 3)}</td><td class="n">${fmt(x.edge, 3)}</td><td class="n ${x.t_stat > 2 ? "up" : x.t_stat < -2 ? "down" : ""}">${fmt(x.t_stat, 2)}</td><td class="n">${fmt(x.all.sharpe, 2)}</td><td class="n">${fmt(x.first_half.mean_net_pct, 3)}</td><td class="n">${fmt(x.second_half.mean_net_pct, 3)}</td></tr>`).join("")}</table>
     <div class="note">Con ${r.results.length} strumenti testati, circa ${Math.round(r.results.length * 0.05)} risulteranno "significativi" (|t|&gt;2) per puro caso.</div></div>`;
  };
  run();
}

// ---------------------------------------------------------------- calibrazione live
async function viewCalib() {
  const d = await api("calibration");
  view.innerHTML = `<h1>Verifica delle previsioni registrate</h1>
  <div class="note">Ogni ciclo registra le stime con data, ora e data dell'ultimo dato usato. Quando l'orizzonte è trascorso, la previsione viene confrontata con l'esito reale. Questa è la verifica più severa: nessuna parte è ricostruita a posteriori.</div>
  <div class="card">${d.n ? `<table><tr><th>Modello</th><th class="n">Previsioni risolte</th><th class="n">Brier (più basso = meglio)</th><th class="n">Classe più probabile corretta</th></tr>${Object.entries(d.by_model).map(([m, x]) => `<tr><td>${m}</td><td class="n">${x.n}</td><td class="n">${fmt(x.brier, 4)}</td><td class="n">${fmt(x.hit_rate * 100, 1)}%</td></tr>`).join("")}</table><div class="note">Il modello è utile solo se il suo Brier è stabilmente inferiore a quello di base_rate.</div>` : `<div class="empty">Nessuna previsione ancora risolta. Le prime (orizzonte 1-3 giorni) si risolvono dopo alcuni giorni di funzionamento.</div>`}</div>`;
}

// ---------------------------------------------------------------- impostazioni
async function viewSettings() {
  const st = await api("settings"); UNIV = await api("universe");
  const chk = (name, val, on, label) => `<label style="display:inline-flex;gap:5px;margin:3px 12px 3px 0"><input type="checkbox" data-n="${name}" value="${esc(val)}" ${on ? "checked" : ""}>${esc(label)}</label>`;
  const H = ["1-3g", "1 settimana", "1 mese", "3 mesi", "6-12 mesi", "intraday (4h)", "intraday (24h)"];
  const cn = await api("connect").catch(() => ({ lan_urls: [] }));
  const installCard = `<div class="card" style="margin-bottom:14px"><h2>App sul telefono Android</h2>
   ${isStandalone() ? `<p>Stai già usando l'app installata.</p>` : window.isSecureContext
      ? `<p>Questo indirizzo è sicuro (HTTPS): puoi installare l'app. ${_installEvt ? `<button class="btn small" onclick="document.getElementById('install').click()">Installa ora</button>` : `In Chrome: menu ⋮ → <b>Installa app</b> (o <b>Aggiungi a schermata Home</b>).`}</p>`
      : `<p>Apri dal telefono, collegato allo stesso Wi-Fi del PC, uno di questi indirizzi: ${cn.lan_urls.map(u => `<b class="mono">${esc(u)}</b>`).join(" oppure ") || "(indirizzo del PC non trovato)"}. Poi in Chrome: menu ⋮ → <b>Aggiungi a schermata Home</b>.</p>
         <p class="note">Su un indirizzo http:// di casa Android crea solo un collegamento. Per l'app completa (finestra propria, apertura anche offline con gli ultimi dati) serve un indirizzo HTTPS: il modo più semplice e gratuito è Tailscale, spiegato nel file LEGGIMI-ANDROID.md.</p>`}
   <p class="note">Il PC con la piattaforma deve restare acceso: il telefono mostra i dati calcolati dal PC. Senza collegamento l'app installata mostra gli ultimi dati ricevuti (indicatore "offline" in alto).</p></div>`;
  view.innerHTML = `<h1>Impostazioni</h1>${installCard}<div class="grid g2">
  <div class="card"><h2>Mercati</h2>${Object.entries(UNIV.markets).map(([k, v]) => chk("enabled_markets", k, st.enabled_markets.includes(k), v)).join("")}
   <h3>Strumenti</h3><div style="max-height:260px;overflow:auto">${UNIV.instruments.map(i => chk("instr", i.id, !st.disabled_instruments.includes(i.id), i.name + (i.custom ? " (personalizzato)" : ""))).join("<br>")}</div>
   <h3>Aggiungi strumento</h3><div class="filters"><select id="csrc"><option value="yahoo">Yahoo Finance</option><option value="binance">Binance</option><option value="fred">FRED</option></select><input type="text" id="csym" placeholder="simbolo, es. ENI.MI, AAPL, LINKUSDT"><input type="text" id="cname" placeholder="nome"><select id="cmkt">${Object.entries(UNIV.markets).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select><button class="btn small" id="cadd">Aggiungi</button></div>
   <div class="note">Il simbolo deve esistere nella fonte scelta; i dati compaiono al prossimo aggiornamento.</div></div>
  <div class="card"><h2>Pesi dei modelli di analisi</h2>${Object.entries(st.weights).map(([k, v]) => `<div class="row"><span style="width:170px">${famName[k] || k}</span><input type="range" min="0" max="2" step="0.1" value="${v}" data-w="${k}"><span id="wv-${k}">${v}</span></div>`).join("")}
   <h3>Orizzonti</h3>${H.map(h => chk("horizons", h, st.horizons.includes(h), h)).join("")}
   <h3>Rischio e filtri dello scanner</h3><div class="row">ATR massimo % <input type="number" id="risk" step="0.5" value="${st.risk_max_atr_pct}" style="width:80px"> affidabilità dati minima <input type="number" id="minrel" step="0.1" min="0" max="1" value="${st.min_reliability}" style="width:80px"></div>
   <h3>Costi per il backtest (pb per lato)</h3><div class="row">commissioni <input type="number" id="cost" value="${st.cost_bps}" style="width:70px"> slippage <input type="number" id="slip" value="${st.slippage_bps}" style="width:70px"></div>
   <h3>Aggiornamento</h3><div class="row">ogni <input type="number" id="refm" value="${st.refresh_minutes}" style="width:70px"> minuti · chiave FRED <input type="password" id="fred" value="${esc(st.fred_api_key)}" placeholder="facoltativa, per il calendario"></div></div>
  <div class="card"><h2>Segnali e tipi di opportunità</h2>${UNIV.rules.map(r => chk("enabled_rules", r.id, st.enabled_rules.includes(r.id), `${r.name} (${famName[r.family]})`)).join("<br>")}</div>
  <div class="card"><h2>Notifiche</h2>${[["anomalia", "Condizioni anomale"], ["volume", "Volumi anomali"], ["regime", "Cambio di regime di volatilità"], ["divergenza", "Divergenze"], ["flusso", "Inversioni di flusso"], ["evento_macro", "Eventi macro in arrivo"], ["convergenza", "Più modelli concordano"], ["watchlist", "Soglie della watchlist"]].map(([k, l]) => chk("alert_kinds", k, st.alert_kinds.includes(k), l)).join("<br>")}
   <div class="note">Le notifiche compaiono nella pagina Watchlist e alert. L'invio su telefono o email non è incluso nella fase 1.</div></div>
  </div><div style="margin-top:14px"><button class="btn" id="save">Salva impostazioni</button> <span class="note">I nuovi pesi e orizzonti si applicano al prossimo aggiornamento.</span></div>`;
  view.querySelectorAll("[data-w]").forEach(r => r.oninput = () => $("#wv-" + r.dataset.w).textContent = r.value);
  $("#cadd").onclick = async () => { try { await post("instruments/custom", { source: $("#csrc").value, symbol: $("#csym").value, name: $("#cname").value, market: $("#cmkt").value }); toast("Strumento aggiunto"); viewSettings(); } catch (e) { toast(e.message); } };
  $("#save").onclick = async () => {
    const vals = n => [...view.querySelectorAll(`[data-n="${n}"]`)].filter(x => x.checked).map(x => x.value);
    const body = { enabled_markets: vals("enabled_markets"), disabled_instruments: [...view.querySelectorAll('[data-n="instr"]')].filter(x => !x.checked).map(x => x.value),
      horizons: vals("horizons"), enabled_rules: vals("enabled_rules"), alert_kinds: vals("alert_kinds"),
      weights: Object.fromEntries([...view.querySelectorAll("[data-w]")].map(r => [r.dataset.w, +r.value])),
      risk_max_atr_pct: +$("#risk").value, min_reliability: +$("#minrel").value, cost_bps: +$("#cost").value, slippage_bps: +$("#slip").value, refresh_minutes: +$("#refm").value, fred_api_key: $("#fred").value };
    await post("settings", body); UNIV = null; toast("Impostazioni salvate");
  };
}

route();
