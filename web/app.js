const number = (value) => String(value).padStart(2, "0");
const percent = (value) => `${(value * 100).toFixed(2)}%`;
const units = (value) => `${Number(value).toFixed(2)} u`;
const isBlank = (value) => value === null || value === undefined;

// El intervalo real llega en la respuesta de la API (config_sync.py).
// Este valor sólo se usa hasta la primera respuesta.
let refreshIntervalMs = 45000;
let refreshTimer = null;

function render(data) {
  const results = data.results || [];
  const latest = results[0];
  document.querySelector("#result-count").textContent = `${results.length} resultados`;
  document.querySelector("#last-sync").textContent = data.last_sync
    ? `sync: ${new Date(data.last_sync).toLocaleTimeString()}`
    : "sin sincronización";

  const intervaloSeconds = Math.round(refreshIntervalMs / 1000);
  const cerrado = Boolean(data.horario) && data.horario.abierto === false;
  document.querySelector("#sync-dot").classList.toggle("error", Boolean(data.sync_error));
  document.querySelector("#sync-status").textContent = data.sync_error
    ? `Error: ${data.sync_error}`
    : cerrado
    ? "Scraping en pausa · fuera del horario de Loteka"
    : `Sincronización activa · cada ${intervaloSeconds} s`;

  renderHorario(data.horario);

  document.querySelector("#latest-result").textContent = latest ? number(latest.Numero) : "-";
  document.querySelector("#latest-meta").textContent = latest
    ? `${latest.No_Sorteo} · ${latest.Fecha} ${latest.Hora}`
    : "No hay resultados guardados.";

  renderRecovery(data.recovery, data.recovery_error);
  renderResults(results);
  renderAlert(data);
  renderPredictions(data);
  renderPauses(data.pauses);
}

function renderHorario(horario) {
  const target = document.querySelector("#horario-status");
  if (!horario) {
    target.textContent = "";
    return;
  }
  const ventana = `${horario.apertura}-${horario.cierre} DR`;
  if (horario.abierto) {
    const cierra = Math.max(0, Math.round((horario.segundos_hasta_cierre || 0) / 60));
    target.textContent = `Loteka abierto · cierra en ${cierra} min`;
    return;
  }
  const abre = Math.max(0, Math.round((horario.segundos_hasta_apertura || 0) / 60));
  target.textContent = `Loteka cerrado (${ventana}) · abre en ${abre} min`;
}

function renderRecovery(recovery, recoveryError) {
  const errorBox = document.querySelector("#recovery-error");
  errorBox.textContent = recoveryError || "";
  errorBox.classList.toggle("hidden", !recoveryError);
  if (!recovery) return;
  const model = recovery.selected_model;
  document.querySelector("#sample-size").textContent = `${recovery.sample_size} observaciones`;
  document.querySelector("#top1").textContent = percent(model.top1);
  document.querySelector("#top3").textContent = percent(model.top3);
  document.querySelector("#log-loss").textContent = model.log_loss.toFixed(4);
  document.querySelector("#markov-forecast").innerHTML = recovery.markov_forecast
    .map((item, index) => `<li><span>${index + 1}. ${number(item.number)}</span><strong>${percent(item.probability)}</strong></li>`)
    .join("");
  document.querySelector("#heuristic-forecast").innerHTML = recovery.heuristic_forecast
    .map((item, index) => `<li><span>${index + 1}. ${number(item.number)}</span><strong>${percent(item.probability)}</strong></li>`)
    .join("");
}

function renderResults(results) {
  document.querySelector("#results-body").innerHTML = results
    .map((row) => `<tr><td>${row.Fecha}</td><td>${row.Hora}</td><td>${row.No_Sorteo}</td><td><b>${number(row.Numero)}</b></td></tr>`)
    .join("");
}

function renderAlert(data) {
  const badge = document.querySelector("#alert-state");
  const errorBox = document.querySelector("#alert-error");
  const alert = data.alert;
  errorBox.textContent = data.predictions_error || "";
  errorBox.classList.toggle("hidden", !data.predictions_error);

  if (!alert || !alert.available) {
    badge.textContent = "SIN DATOS";
    badge.className = "alert-badge idle";
    document.querySelector("#alert-number").textContent = "-";
    document.querySelector("#alert-detail").textContent = "Sin ronda abierta todavía.";
    document.querySelector("#alert-failures").innerHTML = "";
    document.querySelector("#alert-stake").textContent = "-";
    document.querySelector("#alert-stake-note").textContent = "";
    document.querySelector("#alert-counters").textContent = "-";
    document.querySelector("#alert-rate").textContent = "-";
    document.querySelector("#alert-baseline").textContent = "-";
    document.querySelector("#alert-history-body").innerHTML = "";
    return;
  }

  const stats = data.alert_stats || {};
  const fired = Boolean(alert.fired);
  badge.textContent = fired ? "SEÑAL ACTIVA" : "SIN SEÑAL";
  badge.className = `alert-badge ${fired ? "active" : "idle"}`;

  document.querySelector("#alert-number").textContent = isBlank(alert.number) ? "00" : number(alert.number);

  const details = [];
  if (!isBlank(alert.probability)) details.push(`P(M4)=${percent(alert.probability)}`);
  if (!isBlank(alert.spread)) details.push(`Δ=${percent(alert.spread)}`);
  details.push(`confianza ${alert.confidence}`);
  details.push(alert.anchor ? "anclaje activo" : "sin anclaje");
  if (alert.source_draw) details.push(`base ${alert.source_draw}`);
  document.querySelector("#alert-detail").textContent = details.join(" · ");

  document.querySelector("#alert-failures").innerHTML = (alert.failures || [])
    .map((item) => `<li>${item}</li>`)
    .join("");

  const applied = Boolean(alert.stake_applied);
  const notes = alert.stake_notes || [];
  document.querySelector("#alert-stake").textContent = applied
    ? `${percent(alert.stake_fraction)} · ${units(alert.stake_amount)}`
    : `${units(0)} · sin stake`;
  document.querySelector("#alert-stake-note").textContent = applied
    ? `Capital de referencia ${units(alert.bankroll)}${notes.length ? ` · ${notes.join("; ")}` : ""}`
    : notes.length
    ? notes.join("; ")
    : "Sin stake: la evidencia acumulada no respalda la apuesta.";

  document.querySelector("#alert-counters").textContent = `${stats.alerts || 0} de ${stats.rounds || 0} rondas`;
  document.querySelector("#alert-rate").textContent = stats.alerts
    ? `${percent(stats.alert_rate)} (${stats.alert_hits}/${stats.alerts})`
    : "sin alertas";
  document.querySelector("#alert-baseline").textContent = percent(stats.baseline || 0);

  document.querySelector("#alert-history-body").innerHTML = (data.alert_history || [])
    .map((item) => {
      const result = isBlank(item.actual_number) ? "-" : number(item.actual_number);
      const signal = isBlank(item.hit) ? "-" : item.hit ? "Acierto" : "Fallo";
      const stake = item.stake_applied ? percent(item.stake_fraction) : "0";
      const num = isBlank(item.number) ? "00" : number(item.number);
      return `<tr>
        <td>${item.source_draw || "-"}</td>
        <td>${num}</td>
        <td>${result}</td>
        <td class="${item.hit ? "hit" : "miss"}">${signal}</td>
        <td>${isBlank(item.probability) ? "-" : percent(item.probability)}</td>
        <td>${item.confidence || "-"}</td>
        <td>${stake}</td>
      </tr>`;
    })
    .join("");
}

function renderPredictions(data) {
  const errorBox = document.querySelector("#predictions-error");
  errorBox.textContent = data.predictions_error || "";
  errorBox.classList.toggle("hidden", !data.predictions_error);

  document.querySelector("#predictions-body").innerHTML = (data.predictions || [])
    .map((prediction) => {
      const result = prediction.status === "pendiente"
        ? "Pendiente"
        : prediction.status === "omitida_pausa"
        ? "No evaluada · pausa"
        : `${number(prediction.actual_number)} · ${prediction.observed_draw}`;
      const markov = prediction.markov_rank ? `Sí · #${prediction.markov_rank}` : "No";
      const heuristic = prediction.heuristic_rank ? `Sí · #${prediction.heuristic_rank}` : "No";
      const matched = prediction.status === "pendiente"
        ? "Pendiente"
        : prediction.matched === "ninguno"
        ? "Ninguno"
        : prediction.matched === "ambos"
        ? "Ambos"
        : prediction.matched === "markov"
        ? "Markov"
        : "Heurístico";
      const alert = !prediction.alert_fired
        ? "—"
        : isBlank(prediction.alert_hit)
        ? "Activa"
        : prediction.alert_hit
        ? "Acierto"
        : "Fallo";
      return `<tr>
        <td>${prediction.source_draw}</td>
        <td>${result}</td>
        <td>${markov}</td>
        <td>${heuristic}</td>
        <td>${matched}</td>
        <td>${alert}</td>
      </tr>`;
    })
    .join("");

  const det = document.querySelector("#predictions-body-detailed");
  if (det) {
      det.innerHTML = (data.predictions || [])
      .map((prediction) => {
        const result = prediction.status === "pendiente"
          ? "Pendiente"
          : prediction.status === "omitida_pausa"
          ? "Pausa"
          : `${number(prediction.actual_number)} · ${prediction.observed_draw}`;
        const mf = prediction.markov_forecast || [];
        const hf = prediction.heuristic_forecast || [];
        const get = (arr, i) => arr[i] || { number: prediction[`m${i+1}`], probability: prediction[`p${i+1}`] };
        const m = [get(mf,0), get(mf,1), get(mf,2), get(mf,3), get(mf,4)];
        const h = [get(hf,0), get(hf,1), get(hf,2), get(hf,3), get(hf,4)];
        if (m[0].number === undefined && prediction.m1) { m[0]={number:prediction.m1,probability:prediction.p1}; m[1]={number:prediction.m2,probability:prediction.p2}; m[2]={number:prediction.m3,probability:prediction.p3}; m[3]={number:prediction.m4,probability:prediction.p4}; m[4]={number:prediction.m5,probability:prediction.p5}; }
        if (h[0].number === undefined && prediction.h1) { h[0]={number:prediction.h1,probability:prediction.ph1}; h[1]={number:prediction.h2,probability:prediction.ph2}; h[2]={number:prediction.h3,probability:prediction.ph3}; h[3]={number:prediction.h4,probability:prediction.ph4}; h[4]={number:prediction.h5,probability:prediction.ph5}; }
        const p = (x) => (x.probability == null ? "-" : percent(x.probability));
        const n = (x) => (x.number == null || x.number === "-" ? "-" : (typeof x.number === 'number' ? number(x.number) : x.number));
        return `<tr>
          <td>${prediction.source_draw}</td>
          <td>${result}</td>
          <td>${n(m[0])}</td><td>${p(m[0])}</td>
          <td>${n(m[1])}</td><td>${p(m[1])}</td>
          <td>${n(m[2])}</td><td>${p(m[2])}</td>
          <td>${n(m[3])}</td><td>${p(m[3])}</td>
          <td>${n(m[4])}</td><td>${p(m[4])}</td>
          <td>${n(h[0])}</td><td>${p(h[0])}</td>
          <td>${n(h[1])}</td><td>${p(h[1])}</td>
          <td>${n(h[2])}</td><td>${p(h[2])}</td>
          <td>${n(h[3])}</td><td>${p(h[3])}</td>
          <td>${n(h[4])}</td><td>${p(h[4])}</td>
        </tr>`;
      })
      .join("");
  }

  const stats = data.prediction_stats || {};
  document.querySelector("#predictions-summary").textContent = stats.rounds
    ? `${stats.rounds} rondas evaluadas · Markov top-1 ${percent(stats.markov_top1_rate)} (base ${percent(stats.baseline)}) · Markov top-5 ${percent(stats.markov_rate)} (base ${percent(stats.baseline_top5)}) · Heurístico top-1 ${percent(stats.heuristic_top1_rate)}`
    : "Aún no hay rondas evaluadas.";

}

function renderPauses(pauses) {
  const list = pauses || [];
  document.querySelector("#pauses-summary").textContent = list.length
    ? `${list.length} pausa(s) detectada(s); esos sorteos no cuentan como predicciones evaluadas.`
    : "No se han detectado pausas.";
  document.querySelector("#pauses-body").innerHTML = list
    .map((pause) => `<tr><td>${pause.from_draw}</td><td>${pause.to_draw}</td><td>${pause.skipped_count}</td><td>${new Date(pause.detected_at).toLocaleString()}</td></tr>`)
    .join("");
}

async function refresh() {
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data.refresh_interval_seconds) {
      const configured = Math.max(1, Number(data.refresh_interval_seconds)) * 1000;
      if (configured !== refreshIntervalMs) {
        refreshIntervalMs = configured;
        scheduleRefresh();
      }
    }
    render(data);
  } catch (error) {
    document.querySelector("#sync-dot").classList.add("error");
    document.querySelector("#sync-status").textContent = `Sin conexión: ${error.message}`;
  }
}

function scheduleRefresh() {
  if (refreshTimer !== null) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(runRefresh, refreshIntervalMs);
}

async function runRefresh() {
  await refresh();
  scheduleRefresh();
}

document.querySelector("#logout").addEventListener("click", async () => {
  await fetch("/api/auth", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action: "logout"}),
  });
  window.location.href = "/";
});

refresh();
scheduleRefresh();
