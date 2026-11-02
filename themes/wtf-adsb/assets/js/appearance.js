// Loaded before the stylesheet so a saved appearance is applied before first paint.
(() => {
  "use strict";
  try {
    const saved = localStorage.getItem("wtf-appearance");
    if (saved === "night" || saved === "paper")
      document.documentElement.dataset.appearance = saved;
  } catch {
    /* Without storage, the appearance chosen in Ghost stays in place. */
  }
})();
