(() => {
  "use strict";
  const root = document.documentElement;
  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/service-worker.js").catch(() => {});
    });
  }

  let installPrompt = null;
  const installButtons = Array.from(document.querySelectorAll("[data-pwa-install]"));
  const isiOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  const hideInstall = () => installButtons.forEach((button) => { button.hidden = true; });
  const showInstall = () => installButtons.forEach((button) => { button.hidden = false; });
  const showInstallMessage = (message) => {
    const region = document.getElementById("toast-region");
    if (!region) return;
    const item = document.createElement("div");
    item.className = "toast";
    item.textContent = message;
    region.appendChild(item);
    window.setTimeout(() => item.remove(), 5500);
  };
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    showInstall();
  });
  if (isiOS && !standalone) showInstall();
  installButtons.forEach((button) => button.addEventListener("click", async () => {
    if (installPrompt) {
      installPrompt.prompt();
      await installPrompt.userChoice;
      installPrompt = null;
      hideInstall();
    } else if (isiOS) {
      showInstallMessage("To install RPL Equipment: tap Share, then Add to Home Screen.");
    }
  }));
  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    hideInstall();
  });

  const themeButtons = Array.from(document.querySelectorAll("[data-equipment-theme-toggle]"));
  const savedTheme = localStorage.getItem("equipment-theme");
  const preferredLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  root.dataset.theme = savedTheme || (preferredLight ? "light" : "dark");
  const updateThemeLabels = () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    themeButtons.forEach((button) => {
      button.setAttribute("aria-label", `Switch to ${next} theme`);
      button.title = `Switch to ${next} theme`;
    });
  };
  updateThemeLabels();
  themeButtons.forEach((button) => button.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem("equipment-theme", root.dataset.theme);
    updateThemeLabels();
  }));

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

  const headerMoreToggle = document.querySelector("[data-header-more-toggle]");
  const headerMoreMenu = document.querySelector("[data-header-more-menu]");
  const accountToggle = document.querySelector("[data-account-toggle]");
  const accountMenu = document.querySelector("[data-account-menu]");
  const setHeaderMenu = (button, panel, open) => {
    if (!button || !panel) return;
    panel.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
  };
  const closeHeaderMenus = (except = null) => {
    if (except !== headerMoreMenu) setHeaderMenu(headerMoreToggle, headerMoreMenu, false);
    if (except !== accountMenu) setHeaderMenu(accountToggle, accountMenu, false);
  };
  headerMoreToggle?.addEventListener("click", () => {
    const opening = headerMoreMenu.hidden;
    closeHeaderMenus(headerMoreMenu);
    setHeaderMenu(headerMoreToggle, headerMoreMenu, opening);
  });
  accountToggle?.addEventListener("click", () => {
    const opening = accountMenu.hidden;
    closeHeaderMenus(accountMenu);
    setHeaderMenu(accountToggle, accountMenu, opening);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".header-more") && !event.target.closest(".account-menu")) closeHeaderMenus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    setMenu(false);
    closeHeaderMenus();
  });

  const printButton = document.getElementById("print-label");
  if (printButton) printButton.addEventListener("click", () => window.print());

  const scanInput = document.getElementById("scan-input");
  if (scanInput) scanInput.focus({ preventScroll: true });
})();
