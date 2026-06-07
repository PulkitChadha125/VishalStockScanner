/**
 * Polls cached WebSocket book totals — non-blocking, pauses when tab is hidden.
 */
(function () {
  const API = "/api/symbols/market-book";
  const POLL_MS = 2500;

  const tbody = document.getElementById("market-book-tbody");
  const emptyRow = document.getElementById("market-book-empty");
  const updatedEl = document.getElementById("market-book-updated");
  const bannerEl = document.getElementById("market-book-banner");
  const hoursEl = document.getElementById("market-book-hours");

  if (!tbody) return;

  let timerId = null;
  let inFlight = false;
  let abortCtrl = null;
  let lastJson = "";

  function formatQty(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }

  function formatPrice(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return Number(n).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function statusLabel(row, marketMessage) {
    switch (row.status) {
      case "live":
        return null;
      case "market_closed":
        return marketMessage || "Market not open";
      case "login_required":
        return "Log in required";
      case "waiting_totals":
        return "Waiting for book totals…";
      case "waiting":
        return "Waiting for feed…";
      default:
        return "—";
    }
  }

  function renderMixBar(bidPct) {
    const buy = Math.min(100, Math.max(0, bidPct || 0));
    const sell = 100 - buy;
    return `
      <div class="book-mix" title="Buy side ${buy.toFixed(1)}% / Sell side ${sell.toFixed(1)}%">
        <div class="book-mix__bar">
          <span class="book-mix__buy" style="width:${buy}%"></span>
          <span class="book-mix__sell" style="width:${sell}%"></span>
        </div>
        <span class="book-mix__label">${buy.toFixed(1)}% / ${sell.toFixed(1)}%</span>
      </div>
    `;
  }

  function signalBadge(signal) {
    if (!signal) return '<span class="book-signal book-signal--none">—</span>';
    const cls = signal === "BUY" ? "book-signal--buy" : "book-signal--sell";
    return `<span class="book-signal ${cls}">${escapeHtml(signal)}</span>`;
  }

  function renderBanner(data) {
    if (!bannerEl) return;
    if (data.market_open === false && data.market_message) {
      bannerEl.hidden = false;
      bannerEl.textContent = data.market_message;
      bannerEl.classList.add("market-book__banner--closed");
    } else {
      bannerEl.hidden = true;
      bannerEl.textContent = "";
      bannerEl.classList.remove("market-book__banner--closed");
    }

    if (hoursEl && data.start_time && data.stop_time) {
      const tz = data.timezone ? ` ${data.timezone}` : "";
      hoursEl.textContent = `${data.start_time}–${data.stop_time}${tz}`;
    }
  }

  function renderRows(data) {
    const symbols = data.symbols || [];
    const connected = data.connected;
    const marketOpen = data.market_open !== false;
    const marketMessage = data.market_message || "Market not open";

    tbody.querySelectorAll("tr:not(#market-book-empty)").forEach((r) => r.remove());

    if (!symbols.length) {
      emptyRow.hidden = false;
      if (!marketOpen) {
        emptyRow.querySelector("td").textContent = marketMessage;
      } else {
        emptyRow.querySelector("td").textContent = connected
          ? "Add symbols above to see book totals."
          : "Log in to API to see live book totals.";
      }
      return;
    }

    if (!marketOpen) {
      emptyRow.hidden = true;
      symbols.forEach((row) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${escapeHtml(row.symbol_name)}</strong></td>
          <td colspan="7" class="market-book__status">${escapeHtml(
            statusLabel(row, marketMessage)
          )}</td>
        `;
        tbody.appendChild(tr);
      });
      return;
    }

    emptyRow.hidden = true;

    symbols.forEach((row) => {
      const tr = document.createElement("tr");
      if (row.status !== "live") {
        tr.innerHTML = `
          <td><strong>${escapeHtml(row.symbol_name)}</strong></td>
          <td colspan="7" class="market-book__status">${escapeHtml(
            statusLabel(row, marketMessage)
          )}</td>
        `;
        tbody.appendChild(tr);
        return;
      }

      tr.innerHTML = `
        <td><strong>${escapeHtml(row.symbol_name)}</strong></td>
        <td class="qty-buy">${formatQty(row.book_buy_qty)}</td>
        <td class="qty-sell">${formatQty(row.book_sell_qty)}</td>
        <td>${renderMixBar(row.bid_pct)}</td>
        <td>${formatPrice(row.bid_price)}</td>
        <td>${formatPrice(row.ask_price)}</td>
        <td>${formatPrice(row.ltp)}</td>
        <td>${signalBadge(row.signal)}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  async function refresh() {
    if (inFlight || document.hidden) return;

    inFlight = true;
    abortCtrl?.abort();
    abortCtrl = new AbortController();

    try {
      const res = await fetch(API, {
        signal: abortCtrl.signal,
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error("fetch failed");
      const data = await res.json();
      renderBanner(data);
      if (updatedEl) {
        if (data.market_open === false) {
          updatedEl.textContent = data.now
            ? `Now ${data.now} (${data.timezone || "—"})`
            : "—";
        } else {
          const ws = data.ws_active ? "WS" : "REST";
          updatedEl.textContent = data.updated_at
            ? `Updated ${data.updated_at} IST (${ws})`
            : "—";
        }
      }
      const snapshot = JSON.stringify({
        market_open: data.market_open,
        market_message: data.market_message,
        symbols: data.symbols,
      });
      if (snapshot !== lastJson) {
        lastJson = snapshot;
        renderRows(data);
      }
    } catch (err) {
      if (err.name === "AbortError") return;
      if (updatedEl) updatedEl.textContent = "Update failed — retrying…";
    } finally {
      inFlight = false;
    }
  }

  function schedulePoll() {
    clearInterval(timerId);
    timerId = setInterval(refresh, POLL_MS);
  }

  function start() {
    refresh();
    schedulePoll();
  }

  function stop() {
    clearInterval(timerId);
    abortCtrl?.abort();
    inFlight = false;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      abortCtrl?.abort();
      inFlight = false;
    } else {
      refresh();
    }
  });

  window.addEventListener("pagehide", stop);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
