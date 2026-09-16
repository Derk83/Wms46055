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
  const close = document.querySelector(".equipment-sidebar-close");
  const backdrop = document.querySelector("[data-sidebar-backdrop]");
  const mobileMenu = window.matchMedia("(max-width: 1120px)");
  const syncMenuAccessibility = (open) => {
    if (!menu) return;
    if (mobileMenu.matches) {
      menu.inert = !open;
      menu.setAttribute("aria-hidden", open ? "false" : "true");
      menu.setAttribute("role", "dialog");
      if (open) menu.setAttribute("aria-modal", "true");
      else menu.removeAttribute("aria-modal");
    } else {
      menu.inert = false;
      menu.removeAttribute("aria-hidden");
      menu.removeAttribute("aria-modal");
      menu.removeAttribute("role");
    }
  };
  const setMenu = (open, restoreFocus = false) => {
    if (!menu || !toggle) return;
    const mobileOpen = open && mobileMenu.matches;
    menu.classList.toggle("is-open", mobileOpen);
    toggle.classList.toggle("is-open", mobileOpen);
    toggle.setAttribute("aria-expanded", mobileOpen ? "true" : "false");
    document.body.classList.toggle("nav-open", mobileOpen);
    if (backdrop) backdrop.hidden = !mobileOpen;
    syncMenuAccessibility(mobileOpen);
    if (mobileOpen) close?.focus();
    else if (restoreFocus) toggle.focus();
  };
  syncMenuAccessibility(false);
  if (toggle) toggle.addEventListener("click", () => setMenu(!menu.classList.contains("is-open")));
  if (close) close.addEventListener("click", () => setMenu(false, true));
  if (backdrop) backdrop.addEventListener("click", () => setMenu(false, true));
  menu?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => {
    if (mobileMenu.matches) setMenu(false);
  }));
  mobileMenu.addEventListener("change", () => setMenu(false));

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
    if (event.key === "Tab" && mobileMenu.matches && menu?.classList.contains("is-open")) {
      const focusable = Array.from(menu.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
    if (event.key !== "Escape") return;
    const menuWasOpen = menu?.classList.contains("is-open");
    setMenu(false, menuWasOpen);
    closeHeaderMenus();
  });

  const liveSearch = document.querySelector("[data-equipment-live-search]");
  if (liveSearch) {
    const searchInput = liveSearch.querySelector("[data-equipment-search-input]");
    const filters = Array.from(liveSearch.querySelectorAll("[data-equipment-search-filter]"));
    const resultBody = document.querySelector("[data-equipment-search-body]");
    const resultRegion = document.querySelector("[data-equipment-search-results]");
    const feedback = document.querySelector("[data-equipment-search-feedback]");
    let searchTimer = null;
    let controller = null;
    const initialRows = resultBody ? Array.from(resultBody.children).map((row) => row.cloneNode(true)) : [];
    const initialFeedback = feedback?.textContent || "Recently changed equipment";
    const resetResults = () => {
      if (resultBody) resultBody.replaceChildren(...initialRows.map((row) => row.cloneNode(true)));
      if (feedback) feedback.textContent = initialFeedback;
      resultRegion?.setAttribute("aria-busy", "false");
    };

    const addCell = (row, label, text) => {
      const cell = document.createElement("td");
      cell.dataset.label = label;
      cell.textContent = text;
      row.appendChild(cell);
      return cell;
    };
    const renderResults = (payload) => {
      if (!resultBody) return;
      resultBody.replaceChildren();
      payload.results.forEach((result) => {
        const row = document.createElement("tr");
        row.className = "responsive-record-card";
        const assetCell = document.createElement("td");
        assetCell.dataset.label = "Equipment";
        const link = document.createElement("a");
        link.className = "asset-link";
        link.href = result.url;
        const name = document.createElement("strong");
        name.textContent = result.name;
        const tag = document.createElement("small");
        tag.textContent = `${result.asset_tag} · ${result.category}`;
        link.append(name, tag);
        assetCell.appendChild(link);
        row.appendChild(assetCell);

        const statusCell = document.createElement("td");
        statusCell.dataset.label = "Status";
        const status = document.createElement("span");
        status.className = `equipment-status status-${result.status_code.toLowerCase()}`;
        status.textContent = result.status;
        statusCell.appendChild(status);
        row.appendChild(statusCell);
        addCell(row, "Assigned to", result.custodian);
        addCell(row, "Location", result.location);
        addCell(row, "Condition", result.condition);
        addCell(row, "Last activity", result.updated);
        resultBody.appendChild(row);
      });
      if (!payload.results.length) {
        const row = document.createElement("tr");
        const cell = addCell(row, "Results", "No equipment matches those filters.");
        cell.colSpan = 6;
        cell.className = "empty-state";
        resultBody.appendChild(row);
      }
      if (feedback) feedback.textContent = payload.truncated ? "Showing the first 20 matches. Open the full register for all results." : `${payload.results.length} matching record${payload.results.length === 1 ? "" : "s"}`;
    };
    const runSearch = async () => {
      const params = new URLSearchParams(new FormData(liveSearch));
      const query = (params.get("q") || "").trim();
      const hasFilter = filters.some((filter) => filter.value);
      controller?.abort();
      if (query.length < 2 && !hasFilter) {
        controller = null;
        resetResults();
        return;
      }
      const requestController = new AbortController();
      controller = requestController;
      resultRegion?.setAttribute("aria-busy", "true");
      if (feedback) feedback.textContent = "Searching the live register…";
      try {
        const response = await fetch(`${liveSearch.dataset.searchUrl}?${params}`, {
          headers: { Accept: "application/json" },
          signal: requestController.signal,
        });
        if (!response.ok) throw new Error("Search request failed");
        const payload = await response.json();
        if (controller === requestController) renderResults(payload);
      } catch (error) {
        if (error.name !== "AbortError" && controller === requestController && feedback) feedback.textContent = "Live search is unavailable. Use Search to open the full register.";
      } finally {
        if (controller === requestController) {
          controller = null;
          resultRegion?.setAttribute("aria-busy", "false");
        }
      }
    };
    const queueSearch = () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(runSearch, 250);
    };
    searchInput?.addEventListener("input", queueSearch);
    filters.forEach((filter) => filter.addEventListener("change", runSearch));
    liveSearch.addEventListener("submit", (event) => {
      const query = (searchInput?.value || "").trim();
      const hasFilter = filters.some((filter) => filter.value);
      if (query.length >= 2 || hasFilter) {
        event.preventDefault();
        runSearch();
      }
    });
  }

  const printButton = document.getElementById("print-label");
  if (printButton) printButton.addEventListener("click", () => window.print());

  const scanInput = document.getElementById("scan-input");
  if (scanInput) scanInput.focus({ preventScroll: true });
})();
