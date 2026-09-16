(() => {
  "use strict";
  const root = document.documentElement;
  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/service-worker.js").catch(() => {});
    });
  }
  const themeButton = document.getElementById("theme-toggle");
  const savedTheme = localStorage.getItem("equipment-theme");
  const preferredLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  root.dataset.theme = savedTheme || (preferredLight ? "light" : "dark");
  if (themeButton) {
    const updateLabel = () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      themeButton.setAttribute("aria-label", `Switch to ${next} theme`);
      themeButton.title = `Switch to ${next} theme`;
    };
    updateLabel();
    themeButton.addEventListener("click", () => {
      root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
      localStorage.setItem("equipment-theme", root.dataset.theme);
      updateLabel();
    });
  }

  const menu = document.getElementById("equipment-nav");
  const toggle = document.querySelector(".nav-toggle");
  const close = document.querySelector(".mobile-nav-close");
  const setMenu = (open) => {
    if (!menu || !toggle) return;
    menu.classList.toggle("is-open", open);
    toggle.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.classList.toggle("nav-open", open);
  };
  if (toggle) toggle.addEventListener("click", () => setMenu(!menu.classList.contains("is-open")));
  if (close) close.addEventListener("click", () => setMenu(false));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") setMenu(false); });

  const printButton = document.getElementById("print-label");
  if (printButton) printButton.addEventListener("click", () => window.print());

  const scanInput = document.getElementById("scan-input");
  if (scanInput) scanInput.focus({ preventScroll: true });
})();
