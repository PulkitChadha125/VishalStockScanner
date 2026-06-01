const API_BASE = "/api/symbols";

const tbody = document.getElementById("symbols-tbody");
const emptyRow = document.getElementById("empty-row");
const modal = document.getElementById("symbol-modal");
const form = document.getElementById("symbol-form");
const modalTitle = document.getElementById("modal-title");
const formError = document.getElementById("form-error");
const toast = document.getElementById("toast");

let editingId = null;

async function fetchSymbols() {
  const res = await fetch(API_BASE);
  if (!res.ok) throw new Error("Failed to load symbols");
  return res.json();
}

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

function formatPct(value) {
  return `${Number(value).toFixed(2)}%`;
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
      <td>${formatPct(s.stop_loss_pct)}</td>
      <td>${formatPct(s.target_pct)}</td>
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

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function openModal(title, data = null) {
  modalTitle.textContent = title;
  formError.hidden = true;
  formError.textContent = "";

  document.getElementById("symbol-id").value = data?.id ?? "";
  document.getElementById("symbol-name").value = data?.symbol_name ?? "";
  document.getElementById("time-frame").value = data?.time_frame ?? "";
  document.getElementById("stop-loss").value = data?.stop_loss_pct ?? "";
  document.getElementById("target").value = data?.target_pct ?? "";

  editingId = data?.id ?? null;
  modal.classList.add("is-open");
  modal.setAttribute("aria-hidden", "false");
  document.getElementById("symbol-name").focus();
}

function closeModal() {
  modal.classList.remove("is-open");
  modal.setAttribute("aria-hidden", "true");
  editingId = null;
  form.reset();
}

function getFormPayload() {
  return {
    symbol_name: document.getElementById("symbol-name").value.trim(),
    time_frame: document.getElementById("time-frame").value,
    stop_loss_pct: parseFloat(document.getElementById("stop-loss").value),
    target_pct: parseFloat(document.getElementById("target").value),
  };
}

async function loadAndRender() {
  try {
    const symbols = await fetchSymbols();
    renderTable(symbols);
  } catch {
    showToast("Could not load symbols.");
  }
}

document.getElementById("btn-add").addEventListener("click", () => {
  openModal("Add Symbol");
});

document.querySelectorAll("[data-close-modal]").forEach((el) => {
  el.addEventListener("click", closeModal);
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

    const body = await res.json().catch(() => ({}));

    if (!res.ok) {
      formError.textContent = body.error || "Something went wrong.";
      formError.hidden = false;
      return;
    }

    const wasEdit = Boolean(editingId);
    closeModal();
    if (window.AppLogger) {
      AppLogger.log(
        "form",
        wasEdit ? `Symbol updated: ${payload.symbol_name}` : `Symbol added: ${payload.symbol_name}`,
        { element: "#symbol-form" }
      );
    }
    showToast(wasEdit ? "Symbol updated." : "Symbol added.");
    await loadAndRender();
  } catch {
    formError.textContent = "Network error. Please try again.";
    formError.hidden = false;
  }
});

tbody.addEventListener("click", async (e) => {
  const editBtn = e.target.closest("[data-edit]");
  const deleteBtn = e.target.closest("[data-delete]");

  if (editBtn) {
    const id = editBtn.dataset.edit;
    try {
      const res = await fetch(`${API_BASE}/${id}`);
      if (!res.ok) throw new Error();
      const symbol = await res.json();
      openModal("Edit Symbol", symbol);
    } catch {
      showToast("Could not load symbol details.");
    }
    return;
  }

  if (deleteBtn) {
    const id = deleteBtn.dataset.delete;
    if (!confirm("Delete this symbol setting?")) {
      if (window.AppLogger) AppLogger.logClick("Cancelled symbol delete", `[data-delete="${id}"]`);
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error();
      showToast("Symbol deleted.");
      await loadAndRender();
    } catch {
      showToast("Could not delete symbol.");
    }
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && modal.classList.contains("is-open")) {
    closeModal();
  }
});

loadAndRender();
