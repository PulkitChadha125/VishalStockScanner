const API = "/api/logs/app";
const tbody = document.getElementById("app-logs-tbody");
const emptyRow = document.getElementById("app-empty-row");

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
    if (cell) cell.colSpan = 6;
    if (cell) cell.textContent = "Could not load app logs.";
  }
}

loadLogs();
