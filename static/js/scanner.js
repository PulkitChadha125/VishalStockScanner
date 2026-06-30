const API_BASE = "/api/scanner";
const STATUS_URL = `${API_BASE}/status`;

const tbody = document.getElementById("scanner-tbody");
const emptyRow = document.getElementById("empty-row");
const modal = document.getElementById("scanner-modal");
const form = document.getElementById("scanner-form");
const modalTitle = document.getElementById("modal-title");
const formError = document.getElementById("form-error");
const toast = document.getElementById("toast");

let editingId = null;
let statusTimer = null;

function showToast(message) {
  if (typeof window.showAppToast === "function") {
    window.showAppToast(message);
    return;
  }
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    toast.hidden = true;
  }, 2800);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function formatPrice(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function biasLabel(bias) {
  if (bias === "buy") return '<span class="tag tag--buy">Above</span>';
  if (bias === "sell") return '<span class="tag tag--sell">Below</span>';
  if (bias === "flat") return '<span class="tag">Equal</span>';
  return '<span class="tag tag--muted">—</span>';
}

function allowedLabel(signal, isNeutral) {
  if (signal === "BUY") return '<span class="tag tag--buy">BUY only</span>';
  if (signal === "SELL") return '<span class="tag tag--sell">SELL only</span>';
  if (isNeutral) return '<span class="tag tag--muted">NEUTRAL</span>';
  return "—";
}

function renderSummary(summary) {
  document.getElementById("summary-total").textContent = summary.total ?? 0;
  document.getElementById("summary-buy").textContent = summary.buy_count ?? 0;
  document.getElementById("summary-sell").textContent = summary.sell_count ?? 0;
  document.getElementById("summary-other").textContent =
    (summary.flat_count ?? 0) + (summary.missing ?? 0);
  document.getElementById("summary-allowed").innerHTML = allowedLabel(
    summary.allowed_signal,
    summary.is_neutral
  );
}

function renderTable(symbols) {
  tbody.querySelectorAll("tr:not(#empty-row)").forEach((row) => row.remove());

  if (!symbols.length) {
    emptyRow.hidden = false;
    return;
  }

  emptyRow.hidden = true;

  symbols.forEach((s) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${escapeHtml(s.symbol_name)}</strong></td>
      <td>${escapeHtml(s.time_frame)}</td>
      <td>${formatPrice(s.prev_close)}</td>
      <td>${formatPrice(s.ltp)}</td>
      <td>${biasLabel(s.bias)}</td>
      <td>${escapeHtml(s.prev_close_fetched_at || "—")}</td>
      <td>${escapeHtml(s.ltp_updated_at || "—")}</td>
      <td class="col-actions">
        <div class="action-group">
          <button type="button" class="btn btn--sm btn--edit" data-edit="${s.id}">Edit</button>
          <button type="button" class="btn btn--sm btn--delete" data-delete="${s.id}">Delete</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function openModal(title, data = null) {
  modalTitle.textContent = title;
  formError.hidden = true;
  formError.textContent = "";

  document.getElementById("scanner-id").value = data?.id ?? "";
  document.getElementById("scanner-name").value = data?.symbol_name ?? "";
  document.getElementById("scanner-timeframe").value = data?.time_frame ?? "1d";

  editingId = data?.id ?? null;
  modal.classList.add("is-open");
  modal.setAttribute("aria-hidden", "false");
  document.getElementById("scanner-name").focus();
}

function closeModal() {
  modal.classList.remove("is-open");
  modal.setAttribute("aria-hidden", "true");
  editingId = null;
  form.reset();
}

function getFormPayload() {
  return {
    symbol_name: document.getElementById("scanner-name").value.trim(),
    time_frame: document.getElementById("scanner-timeframe").value,
  };
}

async function loadStatus() {
  try {
    const res = await fetch(STATUS_URL);
    if (!res.ok) throw new Error("status failed");
    const data = await res.json();
    renderSummary(data.summary || {});
    renderTable(data.symbols || []);
    document.getElementById("scanner-updated").textContent = `Updated: ${data.updated_at || "—"}`;
  } catch {
    showToast("Could not load scanner status.");
  }
}

document.getElementById("btn-add").addEventListener("click", () => {
  openModal("Add Scanner Symbol", { time_frame: "1d" });
});

document.getElementById("btn-download-csv").addEventListener("click", () => {
  window.location.href = `${API_BASE}/export.csv`;
});

document.getElementById("btn-load-csv").addEventListener("click", () => {
  document.getElementById("csv-file-input").click();
});

document.getElementById("csv-file-input").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  e.target.value = "";
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch(`${API_BASE}/import.csv`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Import failed");
    showToast(data.message || "CSV loaded.");
    await loadStatus();
  } catch (err) {
    showToast(err.message || "CSV import failed.");
  }
});

document.getElementById("btn-refresh-prev-close").addEventListener("click", async () => {
  const btn = document.getElementById("btn-refresh-prev-close");
  btn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/refresh-prev-close`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Refresh failed");
    showToast(data.message || "Previous closes refreshed.");
    await loadStatus();
  } catch (err) {
    showToast(err.message || "Could not refresh prev close.");
  } finally {
    btn.disabled = false;
  }
});

tbody.addEventListener("click", async (e) => {
  const editBtn = e.target.closest("[data-edit]");
  const deleteBtn = e.target.closest("[data-delete]");

  if (editBtn) {
    const id = Number(editBtn.dataset.edit);
    const res = await fetch(`${API_BASE}/${id}`);
    if (!res.ok) {
      showToast("Could not load symbol.");
      return;
    }
    const data = await res.json();
    openModal("Edit Scanner Symbol", data);
    return;
  }

  if (deleteBtn) {
    const id = Number(deleteBtn.dataset.delete);
    if (!window.confirm("Delete this scanner symbol?")) return;
    const res = await fetch(`${API_BASE}/${id}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.error || "Delete failed.");
      return;
    }
    showToast("Deleted.");
    await loadStatus();
  }
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  formError.hidden = true;

  const payload = getFormPayload();
  const url = editingId ? `${API_BASE}/${editingId}` : API_BASE;
  const method = editingId ? "PUT" : "POST";

  try {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Save failed");
    closeModal();
    showToast(editingId ? "Updated." : "Added.");
    await loadStatus();
  } catch (err) {
    formError.textContent = err.message;
    formError.hidden = false;
  }
});

modal.querySelectorAll("[data-close-modal]").forEach((el) => {
  el.addEventListener("click", closeModal);
});

loadStatus();
statusTimer = setInterval(loadStatus, 3000);

window.addEventListener("beforeunload", () => {
  if (statusTimer) clearInterval(statusTimer);
});
