const number = (value) => String(value).padStart(2, "0");
const percent = (value) => `${(value * 100).toFixed(2)}%`;

function render(data) {
  const results = data.results || [];
  const latest = results[0];
  document.querySelector("#result-count").textContent = `${results.length} resultados`;
  document.querySelector("#last-sync").textContent = data.last_sync
    ? `sync: ${new Date(data.last_sync).toLocaleTimeString()}`
    : "sin sincronización";

  document.querySelector("#sync-dot").classList.toggle("error", Boolean(data.sync_error));
  document.querySelector("#sync-status").textContent = data.sync_error
    ? `Error: ${data.sync_error}`
    : "Sincronización activa · cada 15 s";

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
}

async function refresh() {
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    document.querySelector("#sync-dot").classList.add("error");
    document.querySelector("#sync-status").textContent = `Sin conexión: ${error.message}`;
  }
}

refresh();
setInterval(refresh, 15000);
