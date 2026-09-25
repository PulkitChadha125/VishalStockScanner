/**
 * Shell chrome: live IST clock in the sidebar.
 */
(function () {
  const timeEl = document.getElementById("ui-clock");
  const dateEl = document.getElementById("ui-date");
  if (!timeEl) return;

  const timeFmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const dateFmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });

  function tick() {
    const now = new Date();
    timeEl.textContent = timeFmt.format(now);
    if (dateEl) dateEl.textContent = dateFmt.format(now);
  }

  tick();
  setInterval(tick, 1000);
})();
