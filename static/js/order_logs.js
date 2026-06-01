const API = "/api/logs/orders";
const tbody = document.getElementById("order-logs-tbody");
const emptyRow = document.getElementById("order-empty-row");

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

function renderTable(orders) {
  tbody.querySelectorAll("tr:not(#order-empty-row)").forEach((r) => r.remove());

  if (!orders.length) {
    emptyRow.hidden = false;
    return;
  }

  emptyRow.hidden = true;

  orders.forEach((o) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(o.placed_at)}</td>
      <td><strong>${escapeHtml(o.symbol_name)}</strong></td>
      <td><span class="badge badge--${o.side.toLowerCase()}">${escapeHtml(o.side)}</span></td>
      <td>${escapeHtml(o.order_type)}</td>
      <td>${formatNum(o.quantity)}</td>
      <td>${formatNum(o.price)}</td>
      <td>${escapeHtml(o.status)}</td>
      <td>${formatNum(o.stop_loss)}</td>
      <td>${formatNum(o.target)}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadOrders() {
  try {
    const res = await fetch(API);
    if (!res.ok) throw new Error();
    renderTable(await res.json());
  } catch {
    emptyRow.hidden = false;
    emptyRow.querySelector("td").textContent = "Could not load order logs.";
  }
}

loadOrders();
