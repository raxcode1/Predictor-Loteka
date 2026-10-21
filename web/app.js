const number = (value) => String(value).padStart(2, "0");
const percent = (value) => `${(value * 100).toFixed(2)}%`;

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

  document.querySelector("#sync-dot").classList.toggle("error", Boolean(data.sync_error));
  const intervalSeconds = Math.round(refreshIntervalMs / 1000);
  document.querySelector("#sync-status").textContent = data.sync_error
    ? `Error: ${data.sync_error}`
    : "Sincronización activa · cada ${intervalSeconds} s";

  document.querySelector("#latest-result").textContent = latest ? number(latest.Numero) : "-";
  document.querySelector("#latest-meta").textContent = latest
    ? `${latest.No_Sorteo} · ${latest.Fecha} ${latest.Hora}`
    : "No hay resultados guardados.";

  const recovery = data.recovery;
  const recoveryError = document.querySelector("#recovery-error");
  recoveryError.textContent = data.recovery_error || "";
  recoveryError.classList.toggle("hidden", !data.recovery_error);
  if (recovery) {
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

  document.querySelector("#results-body").innerHTML = results
    .map((row) => `<tr><td>${row.Fecha}</td><td>${row.Hora}</td><td>${row.No_Sorteo}</td><td><b>${number(row.Numero)}</b></td></tr>`)
    .join("");

  const predictionsError = document.querySelector("#predictions-error");
  predictionsError.textContent = data.predictions_error || "";
  predictionsError.classList.toggle("hidden", !data.predictions_error);
  document.querySelector("#predictions-body").innerHTML = (data.predictions || [])
    .map((prediction) => {
      const result = prediction.status === "pendiente"
        ? "Pendiente"
        : prediction.status === "omitida_pausa"
        ? "No evaluada · pausa"
        : `${number(prediction.actual_number)} · ${prediction.observed_draw}`;
      const markov = prediction.markov_rank
        ? `Sí · #${prediction.markov_rank}`
        : "No";
      const heuristic = prediction.heuristic_rank
        ? `Sí · #${prediction.heuristic_rank}`
        : "No";
      const matched = prediction.status === "pendiente"
        ? "Pendiente"
        : prediction.matched === "ninguno"
        ? "Ninguno"
        : prediction.matched === "ambos"
        ? "Ambos"
        : prediction.matched === "markov"
        ? "Markov"
        : "Heurístico";
      return `<tr><td>${prediction.source_draw}</td><td>${result}</td><td>${markov}</td><td>${heuristic}</td><td>${matched}</td></tr>`;
    })
    .join("");
  const pauses = data.pauses || [];
  document.querySelector("#pauses-summary").textContent = pauses.length
    ? `${pauses.length} pausa(s) detectada(s); esos sorteos no cuentan como predicciones evaluadas.`
    : "No se han detectado pausas.";
  document.querySelector("#pauses-body").innerHTML = pauses
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
