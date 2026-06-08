const API = "/api/logs/orders";
const SYMBOLS_API = "/api/symbols";

const tbody = document.getElementById("order-logs-tbody");
const emptyRow = document.getElementById("order-empty-row");
const filterSymbol = document.getElementById("filter-symbol");
const filterFrom = document.getElementById("filter-from");
const filterTo = document.getElementById("filter-to");
const btnToday = document.getElementById("filter-today");
const btnApply = document.getElementById("filter-apply");
const btnClear = document.getElementById("filter-clear");
const btnDeleteShown = document.getElementById("btn-delete-shown");
const elTotalPnl = document.getElementById("summary-total-pnl");
const elClosed = document.getElementById("summary-closed");
const elOpen = document.getElementById("summary-open");
const elWl = document.getElementById("summary-wl");
const tradeModal = document.getElementById("trade-detail-modal");
const detailTitle = document.getElementById("trade-detail-title");
const detailPrevPrevClose = document.getElementById("detail-prev-prev-close");
const detailPrevClose = document.getElementById("detail-prev-close");
const detailEntryPrice = document.getElementById("detail-entry-price");
const detailVwap = document.getElementById("detail-vwap");
const detailVwapTf = document.getElementById("detail-vwap-tf");
const detailTarget = document.getElementById("detail-target");
const detailStopLoss = document.getElementById("detail-stop-loss");
const detailStopLossPct = document.getElementById("detail-stop-loss-pct");
const detailTargetPct = document.getElementById("detail-target-pct");
const detailVolumeThreshold = document.getElementById("detail-volume-threshold");
const detailVolumeTrigger = document.getElementById("detail-volume-trigger");
const detailBookBuy = document.getElementById("detail-book-buy");
const detailBookSell = document.getElementById("detail-book-sell");
const detailEntryRequest = document.getElementById("detail-entry-request");
const detailEntryResponse = document.getElementById("detail-entry-response");
const detailExitRequestWrap = document.getElementById("detail-exit-request-wrap");
const detailExitResponseWrap = document.getElementById("detail-exit-response-wrap");
const detailExitRequest = document.getElementById("detail-exit-request");
const detailExitResponse = document.getElementById("detail-exit-response");

let todayMode = true;
let todayIst = todayIso();

function showToast(message) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    toast.hidden = true;
  }, 2800);
}

function escapeHtml(text) {
  if (text == null) return "—";
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

function formatNum(value) {
  if (value == null) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function formatPct(value) {
  if (value == null) return "—";
  return `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
}

function formatJson(value) {
  if (value == null) return "—";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatPnl(value) {
  if (value == null) return "—";
  const n = Number(value);
  const sign = n > 0 ? "+" : "";
  return sign + n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function pnlClass(value) {
  if (value == null) return "";
  const n = Number(value);
  if (n > 0) return "pnl--profit";
  if (n < 0) return "pnl--loss";
  return "";
}

function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function setTodayMode(on) {
  todayMode = on;
  if (on) {
    filterFrom.value = todayIst;
    filterTo.value = todayIst;
  }
  btnToday.classList.toggle("is-active", on);
  btnToday.setAttribute("aria-pressed", on ? "true" : "false");
  updateEmptyMessage();
  updateDeleteButtonLabel();
}

function updateEmptyMessage() {
  const cell = emptyRow.querySelector("td");
  if (!cell) return;
  if (todayMode && !filterSymbol.value) {
    cell.textContent = "No trades today.";
  } else if (hasActiveFilters()) {
    cell.textContent = "No trades match these filters.";
  } else {
    cell.textContent = "No trades in this period.";
  }
}

function buildQuery() {
  const params = new URLSearchParams();
  if (filterSymbol.value) params.set("symbol", filterSymbol.value);
  if (todayMode) {
    params.set("today", "1");
  } else {
    if (filterFrom.value) params.set("from", filterFrom.value);
    if (filterTo.value) params.set("to", filterTo.value);
  }
  const q = params.toString();
  return q ? `${API}?${q}` : API;
}

function hasActiveFilters() {
  return (
    Boolean(filterSymbol.value) ||
    todayMode ||
    Boolean(filterFrom.value) ||
    Boolean(filterTo.value)
  );
}

function deleteConfirmMessage() {
  if (todayMode && !filterSymbol.value) {
    return "Delete all of today's order logs? This cannot be undone.";
  }
  if (!hasActiveFilters()) {
    return "Delete ALL order logs? This cannot be undone.";
  }
  return "Delete all order logs matching the current filters? This cannot be undone.";
}

function renderSummary(summary) {
  const pnl = summary?.total_pnl ?? 0;
  elTotalPnl.textContent = formatPnl(pnl);
  elTotalPnl.className = `pnl-summary__value ${pnlClass(pnl)}`;
  elClosed.textContent = String(summary?.closed_trades ?? 0);
  elOpen.textContent = String(summary?.open_trades ?? 0);
  elWl.textContent = `${summary?.wins ?? 0} / ${summary?.losses ?? 0}`;
}

function renderTable(trades) {
  tbody.querySelectorAll("tr:not(#order-empty-row)").forEach((r) => r.remove());

  if (!trades.length) {
    emptyRow.hidden = false;
    updateEmptyMessage();
    return;
  }

  emptyRow.hidden = true;

  trades.forEach((t) => {
    const tr = document.createElement("tr");
    tr.classList.add("trade-row");
    tr.dataset.tradeId = String(t.id);
    if (t.is_open) tr.classList.add("row--open");
    tr.innerHTML = `
      <td>${escapeHtml(t.entry_time)}</td>
      <td>${escapeHtml(t.exit_time)}</td>
      <td><strong>${escapeHtml(t.symbol_name)}</strong></td>
      <td><span class="badge badge--${t.side.toLowerCase()}">${escapeHtml(t.side)}</span></td>
      <td>${formatNum(t.quantity)}</td>
      <td>${formatNum(t.entry_price)}</td>
      <td>${formatNum(t.exit_price)}</td>
      <td>${escapeHtml(t.exit_reason)}</td>
      <td><span class="status-pill status-pill--${(t.entry_status || "").toLowerCase()}">${escapeHtml(t.entry_status)}</span></td>
      <td><span class="status-pill status-pill--${(t.exit_status || "open").toLowerCase()}">${escapeHtml(t.exit_status || (t.is_open ? "OPEN" : "—"))}</span></td>
      <td>${formatNum(t.stop_loss)}</td>
      <td>${formatNum(t.target)}</td>
      <td class="${pnlClass(t.pnl)}">${formatPnl(t.pnl)}</td>
      <td>
        <button type="button" class="btn btn--sm btn--delete" data-delete-trade="${t.id}">
          Delete
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function openTradeModal() {
  if (!tradeModal) return;
  tradeModal.classList.add("is-open");
  tradeModal.setAttribute("aria-hidden", "false");
}

function closeTradeModal() {
  if (!tradeModal) return;
  tradeModal.classList.remove("is-open");
  tradeModal.setAttribute("aria-hidden", "true");
}

function fillTradeModal(trade) {
  detailTitle.textContent = `${trade.symbol_name} — ${trade.side}`;
  if (detailPrevPrevClose) {
    detailPrevPrevClose.textContent = formatNum(trade.prev_prev_close);
  }
  if (detailPrevClose) {
    detailPrevClose.textContent = formatNum(trade.prev_close);
  }
  detailEntryPrice.textContent = formatNum(trade.entry_price);
  detailVwap.textContent = formatNum(trade.vwap);
  if (detailVwapTf) {
    detailVwapTf.textContent = trade.time_frame ? `(${trade.time_frame})` : "";
  }
  detailTarget.textContent = formatNum(trade.target);
  detailStopLoss.textContent = formatNum(trade.stop_loss);
  if (detailStopLossPct) {
    detailStopLossPct.textContent = formatPct(trade.stop_loss_pct);
  }
  if (detailTargetPct) {
    detailTargetPct.textContent = formatPct(trade.target_pct);
  }
  if (detailVolumeThreshold) {
    detailVolumeThreshold.textContent = formatNum(trade.volume_difference);
  }
  if (detailVolumeTrigger) {
    detailVolumeTrigger.textContent = formatNum(trade.volume_trigger);
  }
  if (detailBookBuy) {
    detailBookBuy.textContent = formatNum(trade.book_buy_qty);
  }
  if (detailBookSell) {
    detailBookSell.textContent = formatNum(trade.book_sell_qty);
  }
  detailEntryRequest.textContent = formatJson(trade.entry_api_request);
  detailEntryResponse.textContent = formatJson(trade.entry_api_response);

  const hasExitRequest = trade.exit_api_request != null;
  const hasExitResponse = trade.exit_api_response != null;
  detailExitRequestWrap.hidden = !hasExitRequest;
  detailExitResponseWrap.hidden = !hasExitResponse;
  if (hasExitRequest) {
    detailExitRequest.textContent = formatJson(trade.exit_api_request);
  }
  if (hasExitResponse) {
    detailExitResponse.textContent = formatJson(trade.exit_api_response);
  }
}

async function showTradeDetail(tradeId) {
  try {
    const res = await fetch(`${API}/${tradeId}`);
    if (!res.ok) throw new Error();
    const trade = await res.json();
    fillTradeModal(trade);
    openTradeModal();
  } catch {
    showToast("Could not load trade details.");
  }
}

async function deleteTrade(id) {
  if (!confirm("Delete this trade log?")) return;
  try {
    const res = await fetch(`${API}/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error();
    showToast("Trade log deleted.");
    await loadOrders();
  } catch {
    showToast("Could not delete trade log.");
  }
}

async function deleteShownLogs() {
  if (!confirm(deleteConfirmMessage())) return;
  try {
    const res = await fetch(buildQuery(), { method: "DELETE" });
    if (!res.ok) throw new Error();
    const data = await res.json();
    const n = (data.trades_deleted || 0) + (data.order_logs_deleted || 0);
    showToast(`Deleted ${n} log record(s).`);
    await loadOrders();
  } catch {
    showToast("Could not delete logs.");
  }
}

async function loadSymbols() {
  try {
    const res = await fetch(SYMBOLS_API);
    if (!res.ok) return;
    const symbols = await res.json();
    symbols.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.symbol_name;
      opt.textContent = s.symbol_name;
      filterSymbol.appendChild(opt);
    });
  } catch {
    /* ignore */
  }
}

async function loadOrders() {
  try {
    const res = await fetch(buildQuery());
    if (!res.ok) throw new Error();
    const data = await res.json();
    if (data.today_ist) todayIst = data.today_ist;
    renderSummary(data.summary);
    renderTable(data.trades || []);
    if (todayMode) {
      filterFrom.value = todayIst;
      filterTo.value = todayIst;
    }
  } catch {
    renderSummary({ total_pnl: 0, closed_trades: 0, open_trades: 0, wins: 0, losses: 0 });
    emptyRow.hidden = false;
    emptyRow.querySelector("td").textContent = "Could not load order logs.";
  }
}

btnApply.addEventListener("click", () => {
  setTodayMode(false);
  loadOrders();
});

btnToday.addEventListener("click", () => {
  if (todayMode) {
    setTodayMode(false);
    filterFrom.value = "";
    filterTo.value = "";
  } else {
    setTodayMode(true);
  }
  loadOrders();
});

btnClear.addEventListener("click", () => {
  setTodayMode(false);
  filterSymbol.value = "";
  filterFrom.value = "";
  filterTo.value = "";
  loadOrders();
});

btnDeleteShown.addEventListener("click", deleteShownLogs);

tbody.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-delete-trade]");
  if (btn) {
    e.stopPropagation();
    deleteTrade(btn.dataset.deleteTrade);
    return;
  }
  const row = e.target.closest("tr.trade-row");
  if (row?.dataset.tradeId) showTradeDetail(row.dataset.tradeId);
});

if (tradeModal) {
  tradeModal.querySelectorAll("[data-close-trade-modal]").forEach((el) => {
    el.addEventListener("click", closeTradeModal);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && tradeModal.classList.contains("is-open")) {
      closeTradeModal();
    }
  });
}

function updateDeleteButtonLabel() {
  if (todayMode && !filterSymbol.value) {
    btnDeleteShown.textContent = "Delete today's logs";
  } else if (hasActiveFilters()) {
    btnDeleteShown.textContent = "Delete shown logs";
  } else {
    btnDeleteShown.textContent = "Delete all order logs";
  }
}

filterSymbol.addEventListener("change", () => {
  updateDeleteButtonLabel();
  updateEmptyMessage();
  loadOrders();
});

filterFrom.addEventListener("change", () => {
  if (todayMode) setTodayMode(false);
});

filterTo.addEventListener("change", () => {
  if (todayMode) setTodayMode(false);
});

loadSymbols().then(() => {
  setTodayMode(true);
  loadOrders();
});
