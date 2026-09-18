(() => {
  "use strict";
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem("eqreq-theme"); } catch (_error) { /* Storage can be disabled. */ }
  const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = saved === "light" || saved === "dark" ? saved : (dark ? "dark" : "light");
  root.dataset.theme = theme;
  const meta = document.querySelector("meta[data-theme-color]");
  if (meta) meta.content = theme === "dark" ? "#0f1319" : "#f4f7fb";
})();
