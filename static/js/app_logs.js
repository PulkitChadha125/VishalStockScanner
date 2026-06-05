const API = "/api/logs/app";
const tbody = document.getElementById("app-logs-tbody");
const emptyRow = document.getElementById("app-empty-row");
const btnDeleteAll = document.getElementById("btn-delete-all-app");

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
  if (text == null || text === "") return "—";
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

function renderTable(logs) {
  tbody.querySelectorAll("tr:not(#app-empty-row)").forEach((r) => r.remove());

  if (!logs.length) {
    emptyRow.hidden = false;
    return;
  }

  emptyRow.hidden = true;

  logs.forEach((log) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(log.created_at)}</td>
      <td><span class="badge badge--activity">${escapeHtml(log.activity_type)}</span></td>
      <td>${escapeHtml(log.description)}</td>
      <td>${escapeHtml(log.page_path)}</td>
      <td>${escapeHtml(log.element)}</td>
      <td class="cell-details">${escapeHtml(log.details)}</td>
      <td>
        <button type="button" class="btn btn--sm btn--delete" data-delete-app="${log.id}">
          Delete
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadLogs() {
  try {
    const res = await fetch(API);
    if (!res.ok) throw new Error();
    renderTable(await res.json());
  } catch {
    emptyRow.hidden = false;
    const cell = emptyRow.querySelector("td");
    if (cell) cell.colSpan = 7;
    if (cell) cell.textContent = "Could not load app logs.";
  }
}

async function deleteLog(id) {
  if (!confirm("Delete this app log entry?")) return;
  try {
    const res = await fetch(`${API}/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error();
    showToast("App log deleted.");
    await loadLogs();
  } catch {
    showToast("Could not delete app log.");
  }
}

async function deleteAllLogs() {
  if (!confirm("Delete ALL app logs? This cannot be undone.")) return;
  try {
    const res = await fetch(API, { method: "DELETE" });
    if (!res.ok) throw new Error();
    const data = await res.json();
    showToast(`Deleted ${data.deleted || 0} app log(s).`);
    await loadLogs();
  } catch {
    showToast("Could not delete app logs.");
  }
}

btnDeleteAll.addEventListener("click", deleteAllLogs);

tbody.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-delete-app]");
  if (btn) deleteLog(btn.dataset.deleteApp);
});

loadLogs();
