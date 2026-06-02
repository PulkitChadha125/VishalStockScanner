const STRATEGY_API = "/api/strategy";

const els = {
  apiStatus: document.getElementById("api-status"),
  strategyStatus: document.getElementById("strategy-status"),
  balanceStatus: document.getElementById("balance-status"),
  btnLogin: document.getElementById("btn-api-login"),
  btnStart: document.getElementById("btn-strategy-start"),
  btnStop: document.getElementById("btn-strategy-stop"),
  btnSaveSettings: document.getElementById("btn-save-settings"),
  startTime: document.getElementById("strategy-start-time"),
  stopTime: document.getElementById("strategy-stop-time"),
  maxTrades: document.getElementById("strategy-max-trades"),
  hint: document.getElementById("strategy-hint"),
};

let state = {
  api_connected: false,
  is_running: false,
  start_time: "09:30",
  stop_time: "15:00",
  max_trades: 2,
  trades_taken_today: 0,
  available_balance: null,
};

let pollTimer = null;

window.showAppToast = function showAppToast(message) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showAppToast._timer);
  showAppToast._timer = setTimeout(() => {
    toast.hidden = true;
  }, 3200);
};

function toInputTime(hhmm) {
  return hhmm.length === 5 ? hhmm : "09:30";
}

function fromInputTime(value) {
  return value || "09:30";
}

function renderState() {
  const apiOn = state.api_connected;
  const running = state.is_running;

  els.apiStatus.dataset.state = apiOn ? "connected" : "disconnected";
  els.apiStatus.querySelector(".status-pill__text").textContent = apiOn
    ? "API on"
    : "API off";

  els.strategyStatus.dataset.state = running ? "running" : "stopped";
  els.strategyStatus.querySelector(".status-pill__text").textContent = running
    ? "Running"
    : "Stopped";
  const balText =
    typeof state.available_balance === "number"
      ? `Balance: ₹${state.available_balance.toLocaleString(undefined, {
          maximumFractionDigits: 2,
        })}`
      : "Balance: --";
  els.balanceStatus.querySelector(".status-pill__text").textContent = balText;

  els.btnLogin.textContent = apiOn ? "Logout" : "Login";
  els.btnLogin.classList.toggle("btn--outline", !apiOn);
  els.btnLogin.classList.toggle("btn--ghost", apiOn);

  // Start can auto-login on click, so keep it enabled whenever strategy is not running.
  els.btnStart.disabled = running;
  els.btnStop.disabled = !running;

  els.startTime.disabled = running;
  els.stopTime.disabled = running;
  els.maxTrades.disabled = running;
  els.btnSaveSettings.disabled = running;

  const max = state.max_trades ?? 2;
  const taken = state.trades_taken_today ?? 0;
  const tradesLeft = Math.max(0, max - taken);

  if (running) {
    els.hint.textContent = `Running · ${state.start_time}–${state.stop_time} · ${taken}/${max} trades today (${tradesLeft} left, all symbols).`;
  } else if (!apiOn) {
    els.hint.textContent = "Log in, set window & max trades, then Save and Start.";
  } else {
    els.hint.textContent = `${state.start_time}–${state.stop_time} · max ${max}/day · ${taken} used, ${tradesLeft} left (counts across all symbols).`;
  }
}

function updatePolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (state.is_running) {
    pollTimer = setInterval(async () => {
      try {
        state = await apiRequest(STRATEGY_API);
        renderState();
      } catch {
        // ignore background poll errors
      }
    }, 1000);
  }
}

async function apiRequest(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.error || body.message || "Request failed");
  }
  return body;
}

async function loadStrategy() {
  try {
    state = await apiRequest(STRATEGY_API);
    els.startTime.value = toInputTime(state.start_time);
    els.stopTime.value = toInputTime(state.stop_time);
    els.maxTrades.value = state.max_trades ?? 2;
    renderState();
    updatePolling();
  } catch {
    showAppToast("Could not load strategy settings.");
  }
}

async function saveSettings() {
  const maxTrades = parseInt(els.maxTrades.value, 10);
  if (!Number.isFinite(maxTrades) || maxTrades < 1) {
    showAppToast("Max trades must be at least 1.");
    return;
  }

  const payload = {
    start_time: fromInputTime(els.startTime.value),
    stop_time: fromInputTime(els.stopTime.value),
    max_trades: maxTrades,
  };
  try {
    state = await apiRequest(`${STRATEGY_API}/times`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    renderState();
    updatePolling();
    showAppToast(
      `Settings saved: ${state.start_time}–${state.stop_time}, max ${state.max_trades} trades/day`
    );
    if (window.AppLogger) {
      AppLogger.log(
        "strategy",
        `Saved settings: ${state.start_time}–${state.stop_time}, max trades ${state.max_trades}`
      );
    }
  } catch (err) {
    showAppToast(err.message);
  }
}

els.btnSaveSettings.addEventListener("click", saveSettings);

els.btnLogin.addEventListener("click", async () => {
  try {
    if (state.api_connected) {
      const body = await apiRequest(`${STRATEGY_API}/logout`, { method: "POST" });
      state = body;
      showAppToast(body.message || "Logged out.");
    } else {
      const body = await apiRequest(`${STRATEGY_API}/login`, { method: "POST" });
      state = body;
      showAppToast(body.message || "Login successful (stub).");
    }
    renderState();
    updatePolling();
  } catch (err) {
    showAppToast(err.message);
  }
});

els.btnStart.addEventListener("click", async () => {
  try {
    const body = await apiRequest(`${STRATEGY_API}/start`, { method: "POST" });
    state = body;
    renderState();
    updatePolling();
    showAppToast(body.message || "Strategy started.");
    if (window.AppLogger) AppLogger.log("strategy", "Strategy start button clicked");
  } catch (err) {
    showAppToast(err.message);
  }
});

els.btnStop.addEventListener("click", async () => {
  try {
    const body = await apiRequest(`${STRATEGY_API}/stop`, { method: "POST" });
    state = body;
    renderState();
    updatePolling();
    showAppToast(body.message || "Strategy stopped.");
    if (window.AppLogger) AppLogger.log("strategy", "Strategy stop button clicked");
  } catch (err) {
    showAppToast(err.message);
  }
});

loadStrategy();
