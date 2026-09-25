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
const detailEntryLtp = document.getElementById("detail-entry-ltp");
const detailEntryBuffer = document.getElementById("detail-entry-buffer");
const detailVwapBand = document.getElementById("detail-vwap-band");
const detailEntryPrice = document.getElementById("detail-entry-price");
const detailEntryOrderType = document.getElementById("detail-entry-order-type");
const detailEntryLimit = document.getElementById("detail-entry-limit");
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
const detailBalance = document.getElementById("detail-balance");
const detailLeverage = document.getElementById("detail-leverage");
const detailExposure = document.getElementById("detail-exposure");
const detailShareValue = document.getElementById("detail-share-value");
const detailOrderQty = document.getElementById("detail-order-qty");
const detailOrderValue = document.getElementById("detail-order-value");
const detailEntryRequest = document.getElementById("detail-entry-request");
const detailEntryResponse = document.getElementById("detail-entry-response");
const detailExitRequestWrap = document.getElementById("detail-exit-request-wrap");
const detailExitResponseWrap = document.getElementById("detail-exit-response-wrap");
const detailExitRequest = document.getElementById("detail-exit-request");
const detailExitResponse = document.getElementById("detail-exit-response");
const detailLegsSection = document.getElementById("detail-legs-section");
const detailExitMode = document.getElementById("detail-exit-mode");
const detailExitLeg = document.getElementById("detail-exit-leg");
const detailTargetOrderId = document.getElementById("detail-target-order-id");
const detailSlOrderId = document.getElementById("detail-sl-order-id");
const detailSlPrices = document.getElementById("detail-sl-prices");
const detailEntryFill = document.getElementById("detail-entry-fill");
const detailTargetLegRequest = document.getElementById("detail-target-leg-request");
const detailTargetLegResponse = document.getElementById("detail-target-leg-response");
const detailSlLegRequest = document.getElementById("detail-sl-leg-request");
const detailSlLegResponse = document.getElementById("detail-sl-leg-response");
const detailLegCancelsWrap = document.getElementById("detail-leg-cancels-wrap");
const detailLegCancels = document.getElementById("detail-leg-cancels");

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

function formatMoney(value) {
  if (value == null) return "—";
  return `₹${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
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

function exitReasonLabel(reason) {
  if (!reason) return "—";
  const labels = {
    UNFILLED: "Limit cancelled",
    TARGET: "Target",
    EOD: "Time exit",
    SESSION: "Session stopped",
    MANUAL: "Session stopped",
    STOP: "Session stopped",
  };
  return labels[reason] || reason;
}

function eventTypeBadge(eventType) {
  const cls = eventType === "ENTRY" ? "badge--entry" : "badge--exit";
  return `<span class="badge ${cls}">${escapeHtml(eventType)}</span>`;
}

function renderTable(events) {
  tbody.querySelectorAll("tr:not(#order-empty-row)").forEach((r) => r.remove());

  if (!events.length) {
    emptyRow.hidden = false;
    updateEmptyMessage();
    return;
  }

  emptyRow.hidden = true;

  events.forEach((ev) => {
    const tr = document.createElement("tr");
    tr.classList.add("trade-row");
    tr.dataset.tradeId = String(ev.trade_id);
    if (ev.event_type === "ENTRY" && ev.is_open) tr.classList.add("row--open");

    const isEntry = ev.event_type === "ENTRY";
    const reasonCell = isEntry
      ? (ev.is_open ? '<span class="status-pill status-pill--open">OPEN</span>' : "—")
      : escapeHtml(exitReasonLabel(ev.exit_reason));

    const shareValue = ev.share_value != null ? ev.share_value : (isEntry ? ev.price : null);
    const exposureCell = isEntry ? formatMoney(ev.exposure) : "—";
    const shareCell = shareValue != null ? formatMoney(shareValue) : "—";
    const orderValueCell = isEntry ? formatMoney(ev.order_value) : "—";

    tr.innerHTML = `
      <td>${escapeHtml(ev.time)}</td>
      <td>${eventTypeBadge(ev.event_type)}</td>
      <td><strong>${escapeHtml(ev.symbol_name)}</strong></td>
      <td><span class="badge badge--${ev.side.toLowerCase()}">${escapeHtml(ev.side)}</span></td>
      <td>${formatNum(ev.quantity)}</td>
      <td>${shareCell}</td>
      <td>${exposureCell}</td>
      <td>${orderValueCell}</td>
      <td>${reasonCell}</td>
      <td><span class="status-pill status-pill--${(ev.status || "").toLowerCase()}">${escapeHtml(ev.status)}</span></td>
      <td>${isEntry ? formatNum(ev.stop_loss) : "—"}</td>
      <td>${isEntry ? formatNum(ev.target) : "—"}</td>
      <td class="${pnlClass(ev.pnl)}">${isEntry ? "—" : formatPnl(ev.pnl)}</td>
      <td>
        <button type="button" class="btn btn--sm btn--delete" data-delete-trade="${ev.trade_id}">
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

function fillExitLegs(trade) {
  if (!detailLegsSection) return;

  const hasLegs =
    trade.target_order_id != null ||
    trade.sl_order_id != null ||
    trade.target_leg_request != null ||
    trade.sl_leg_request != null;
  detailLegsSection.hidden = !hasLegs;
  if (!hasLegs) return;

  detailExitMode.textContent = trade.exit_mode || "—";
  detailExitLeg.textContent = trade.exit_leg
    ? `${trade.exit_leg} (${trade.exit_via || "broker_leg"})`
    : "—";
  detailTargetOrderId.textContent = trade.target_order_id || "—";
  detailSlOrderId.textContent = trade.sl_order_id || "—";
  detailSlPrices.textContent =
    trade.sl_trigger_price != null
      ? `${formatNum(trade.sl_trigger_price)} / ${formatNum(trade.sl_limit_price)}`
      : "—";
  detailEntryFill.textContent = formatNum(trade.entry_fill_price);

  detailTargetLegRequest.textContent = formatJson(trade.target_leg_request);
  detailTargetLegResponse.textContent = formatJson(trade.target_leg_response);
  detailSlLegRequest.textContent = formatJson(trade.sl_leg_request);
  detailSlLegResponse.textContent = formatJson(trade.sl_leg_response);

  const cancels = trade.exit_leg_cancels;
  detailLegCancelsWrap.hidden = cancels == null;
  if (cancels != null) {
    detailLegCancels.textContent = formatJson(cancels);
  }
}

function fillTradeModal(trade) {
  detailTitle.textContent = `${trade.symbol_name} — ${trade.side}`;
  if (detailEntryLtp) {
    detailEntryLtp.textContent = formatNum(trade.entry_ltp);
  }
  if (detailEntryBuffer) {
    const down = trade.entry_range_down_pct ?? trade.entry_buffer_pct;
    const up = trade.entry_range_up_pct;
    if (down != null && up != null) {
      detailEntryBuffer.textContent = `${formatNum(down)}% / ${formatNum(up)}%`;
    } else if (down != null) {
      detailEntryBuffer.textContent = `${formatNum(down)}%`;
    } else {
      detailEntryBuffer.textContent = "—";
    }
  }
  if (detailVwapBand) {
    if (trade.vwap_band_low != null && trade.vwap_band_high != null) {
      const low = formatNum(trade.vwap_band_low);
      const high = formatNum(trade.vwap_band_high);
      detailVwapBand.textContent = `${low} – ${high}`;
    } else {
      detailVwapBand.textContent = "—";
    }
  }
  detailEntryPrice.textContent = formatNum(trade.entry_price);
  if (detailEntryOrderType) {
    detailEntryOrderType.textContent = trade.entry_order_type || "MARKET";
  }
  if (detailEntryLimit) {
    detailEntryLimit.textContent = formatNum(trade.entry_limit_price);
  }
  if (detailBalance) {
    detailBalance.textContent = formatMoney(trade.available_balance);
  }
  if (detailLeverage) {
    detailLeverage.textContent =
      trade.leverage_multiplier != null ? `${formatNum(trade.leverage_multiplier)}×` : "—";
  }
  if (detailExposure) {
    detailExposure.textContent = formatMoney(trade.exposure);
  }
  if (detailShareValue) {
    detailShareValue.textContent = formatMoney(
      trade.share_value != null ? trade.share_value : trade.entry_price
    );
  }
  if (detailOrderQty) {
    detailOrderQty.textContent = formatNum(trade.quantity);
  }
  if (detailOrderValue) {
    detailOrderValue.textContent = formatMoney(trade.order_value);
  }
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
  fillExitLegs(trade);

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
    renderTable(data.events || []);
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
