(() => {
  const box = document.querySelector("[data-weekly-static]");
  if (!box) return;
  const refresh = () => {
    const now = Date.now();
    const active =
      now >= Date.parse(box.dataset.validFrom) &&
      now < Date.parse(box.dataset.validUntil);
    box.querySelector("[data-weekly-title]").textContent = active
      ? "Most Interesting Last Week"
      : "Weekly spotlight";
    box.querySelector("[data-weekly-current]").hidden = !active;
    box.querySelector("[data-weekly-expired]").hidden = active;
  };
  refresh();
  window.addEventListener("pageshow", refresh);
  document.addEventListener("visibilitychange", refresh);
  window.setInterval(refresh, 60000);
})();
