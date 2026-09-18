(() => {
  "use strict";
  const root = document.documentElement;
  const themeButton = document.querySelector("[data-eqreq-theme-toggle]");
  const syncTheme = () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    if (themeButton) {
      themeButton.setAttribute("aria-label", `Switch to ${next} theme`);
      themeButton.title = `Switch to ${next} theme`;
    }
    const meta = document.querySelector("meta[data-theme-color]");
    if (meta) meta.content = root.dataset.theme === "dark" ? "#0f1319" : "#f4f7fb";
  };
  syncTheme();
  themeButton?.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("eqreq-theme", root.dataset.theme); } catch (_error) { /* Storage can be disabled. */ }
    syncTheme();
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  const formset = document.querySelector("[data-eqreq-formset]");
  if (formset) {
    const list = formset.querySelector("[data-line-list]");
    const template = formset.querySelector("template[data-empty-line]");
    const total = formset.querySelector("[name='lines-TOTAL_FORMS']");
    const notice = formset.querySelector("[data-line-limit]");
    const add = formset.querySelector("[data-add-line]");
    const setupEquipmentChoices = (row) => {
      const category = row.querySelector("[data-equipment-category]");
      const equipment = row.querySelector("[data-equipment-choice]");
      const availability = row.querySelector("[data-equipment-availability]");
      if (!category || !equipment) return;
      let requestVersion = 0;
      const setMessage = (message) => { if (availability) availability.textContent = message; };
      const loadChoices = async (preserveSelection = true) => {
        const version = ++requestVersion;
        const categoryId = category.value;
        const previous = preserveSelection ? equipment.value : "";
        const placeholder = new Option(categoryId ? "No preferred item" : "Select a category first", "", true, !previous);
        if (!categoryId) {
          equipment.replaceChildren(placeholder);
          equipment.disabled = true;
          setMessage("Choose a category to view its equipment.");
          return;
        }
        equipment.disabled = true;
        if (!preserveSelection) equipment.replaceChildren(placeholder);
        equipment.setAttribute("aria-busy", "true");
        setMessage("Loading equipment…");
        try {
          const url = new URL(formset.dataset.equipmentOptionsUrl, window.location.origin);
          url.searchParams.set("category", categoryId);
          const response = await fetch(url, {headers: {Accept: "application/json"}, credentials: "same-origin"});
          if (!response.ok) throw new Error("Equipment lookup failed");
          const data = await response.json();
          if (version !== requestVersion) return;
          const options = data.assets.map((asset) => {
            const option = new Option(asset.label, asset.id, false, asset.id === previous);
            option.dataset.assetCategory = categoryId;
            option.dataset.assetAvailable = asset.available ? "true" : "false";
            option.disabled = !asset.available;
            if (!asset.available) option.className = "eqreq-unavailable-option";
            return option;
          });
          equipment.replaceChildren(placeholder, ...options);
          if (options.some((option) => option.value === previous)) equipment.value = previous;
          equipment.disabled = false;
          const available = data.assets.filter((asset) => asset.available).length;
          const unavailable = data.assets.length - available;
          if (!data.assets.length) {
            setMessage("No listed equipment in this category. You can still submit a category-only request.");
          } else {
            setMessage(`${available} available${unavailable ? ` · ${unavailable} unavailable (shown in grey)` : ""}.`);
          }
        } catch (error) {
          if (version !== requestVersion) return;
          equipment.disabled = false;
          setMessage("Equipment could not be loaded. You can still submit a category-only request.");
        } finally {
          if (version === requestVersion) equipment.removeAttribute("aria-busy");
        }
      };
      category.addEventListener("change", () => loadChoices(false));
      loadChoices(true);
    };
    const activeRows = () => Array.from(list.querySelectorAll("[data-line-row]")).filter((row) => !row.hidden);
    const renumber = () => {
      activeRows().forEach((row, index) => {
        const number = row.querySelector("[data-line-number]");
        if (number) number.textContent = String(index + 1);
      });
      const count = activeRows().length;
      add.disabled = count >= 20;
      notice.textContent = count >= 20 ? "Maximum of 20 items reached." : `${count} of 20 items`;
      list.querySelectorAll("[data-remove-line]").forEach((button) => { button.disabled = count <= 1; });
    };
    const remove = (button) => {
      const row = button.closest("[data-line-row]");
      if (!row || activeRows().length <= 1) return;
      const deletion = row.querySelector("input[name$='-DELETE']");
      if (deletion) deletion.checked = true;
      row.hidden = true;
      renumber();
      add.focus();
    };
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-line]");
      if (button) remove(button);
    });
    add.addEventListener("click", () => {
      if (activeRows().length >= 20) return;
      const index = Number.parseInt(total.value, 10);
      const wrapper = document.createElement("div");
      wrapper.innerHTML = template.innerHTML.replaceAll("__prefix__", String(index)).replaceAll("__number__", String(activeRows().length + 1)).trim();
      const row = wrapper.firstElementChild;
      list.appendChild(row);
      setupEquipmentChoices(row);
      total.value = String(index + 1);
      renumber();
      row.querySelector("select, input:not([type='hidden']), textarea")?.focus();
    });
    list.querySelectorAll("[data-line-row]").forEach(setupEquipmentChoices);
    renumber();
  }

  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/service-worker.js").catch(() => {}));
  }
})();
