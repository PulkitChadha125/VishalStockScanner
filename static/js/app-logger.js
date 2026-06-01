/**
 * Records clicks and user activity to App Logs.
 */
(function () {
  const API = "/api/logs/app";
  const queue = [];
  let flushing = false;

  function pagePath() {
    return window.location.pathname;
  }

  function postLog(payload) {
    return fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    });
  }

  async function flushQueue() {
    if (flushing || !queue.length) return;
    flushing = true;
    while (queue.length) {
      const item = queue.shift();
      try {
        await postLog(item);
      } catch {
        queue.unshift(item);
        break;
      }
    }
    flushing = false;
  }

  window.AppLogger = {
    log(activityType, description, options = {}) {
      const payload = {
        activity_type: activityType,
        description,
        page_path: options.page_path ?? pagePath(),
        element: options.element ?? null,
        details: options.details ?? null,
      };
      queue.push(payload);
      flushQueue();
    },

    logClick(label, element) {
      this.log("click", label, { element });
    },

    logPageView(pageName) {
      this.log("page_view", `Viewed page: ${pageName}`, {
        page_path: pagePath(),
      });
    },
  };

  document.addEventListener("click", (e) => {
    const target = e.target.closest("[data-log-label], .drawer__link, button, a");
    if (!target) return;

    const label =
      target.getAttribute("data-log-label") ||
      target.textContent?.trim().slice(0, 80) ||
      target.tagName;

    const tag = target.tagName.toLowerCase();
    const id = target.id ? `#${target.id}` : "";
    const element = `${tag}${id}`;

    if (target.matches(".drawer__link")) {
      AppLogger.logClick(label, element);
      return;
    }

    if (target.matches("button, a, [data-log-label]")) {
      AppLogger.logClick(`Click: ${label}`, element);
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    const page = document.body.dataset.page;
    const titles = {
      symbols: "Symbol Settings",
      orders: "Order Logs",
      app_logs: "App Logs",
    };
    if (page && titles[page]) {
      AppLogger.logPageView(titles[page]);
    }

    const toggle = document.getElementById("drawer-toggle");
    const drawer = document.getElementById("app-drawer");
    if (toggle && drawer) {
      toggle.addEventListener("click", () => {
        drawer.classList.toggle("is-open");
        AppLogger.logClick("Toggle navigation drawer", "#drawer-toggle");
      });
    }
  });
})();
