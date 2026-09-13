(() => {
  const form = document.getElementById("material-request-form");
  if (!form) return;

  const body = document.getElementById("request-lines-body");
  const totalForms = document.getElementById("id_lines-TOTAL_FORMS");
  const emptyTemplate = document.getElementById("empty-request-line");
  const search = document.getElementById("inventory-search");
  const resultCount = document.getElementById("inventory-result-count");
  const noResults = document.getElementById("inventory-no-results");
  const status = document.getElementById("request-draft-status");
  const pickerList = document.getElementById("inventory-picker-list");
  const browserDialog = document.getElementById("inventory-browser-dialog");
  const modalList = document.getElementById("inventory-modal-list");
  const modalSearch = document.getElementById("inventory-modal-search");
  const modalCount = document.getElementById("inventory-modal-result-count");
  const modalNoResults = document.getElementById("inventory-modal-no-results");
  let activeCategory = "";
  const isCreate = form.dataset.materialRequestDraft === "create";
  const draftKey = `bbx-material-request-draft-v1:${location.pathname}`;
  let saveTimer;

  function activeRows() {
    return [...body.querySelectorAll(".request-line")].filter((row) => {
      const deleted = row.querySelector('input[name$="-DELETE"]');
      return !deleted?.checked && !row.hidden;
    });
  }

  function addRow() {
    const index = Number.parseInt(totalForms.value, 10);
    body.insertAdjacentHTML("beforeend", emptyTemplate.innerHTML.replaceAll("__prefix__", String(index)));
    totalForms.value = String(index + 1);
    return body.lastElementChild;
  }

  function rowForItem(itemId) {
    return activeRows().find((row) => row.querySelector('select[name$="-item"]')?.value === String(itemId));
  }

  function availableRow() {
    return activeRows().find((row) => !row.querySelector('select[name$="-item"]')?.value) || addRow();
  }

  function addInventoryItem(itemId, fromDialog = false) {
    const duplicate = rowForItem(itemId);
    if (duplicate) {
      duplicate.classList.remove("line-highlight");
      void duplicate.offsetWidth;
      duplicate.classList.add("line-highlight");
      if (!fromDialog) duplicate.querySelector('input[name$="-quantity"]')?.focus();
      status.textContent = "That item is already on the request.";
      return;
    }
    const row = availableRow();
    const select = row.querySelector('select[name$="-item"]');
    const quantity = row.querySelector('input[name$="-quantity"]');
    const deleted = row.querySelector('input[name$="-DELETE"]');
    if (deleted) deleted.checked = false;
    row.hidden = false;
    select.value = String(itemId);
    select.dispatchEvent(new Event("change", { bubbles: true }));
    if (!quantity.value) quantity.value = "1";
    if (!fromDialog) {
      quantity.focus();
      row.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    status.textContent = "Item added. Enter the quantity needed.";
    scheduleSave();
  }

  function serializeDraft() {
    return [...form.elements]
      .filter((field) => field.name && field.name !== "csrfmiddlewaretoken" && !field.disabled)
      .map((field) => ({
        name: field.name,
        value: field.value,
        checked: field.type === "checkbox" || field.type === "radio" ? field.checked : null,
      }));
  }

  function saveDraft() {
    if (!isCreate) return;
    try {
      sessionStorage.setItem(draftKey, JSON.stringify(serializeDraft()));
      status.textContent = "Progress saved in this browser.";
    } catch (_) {
      status.textContent = "Progress is kept on this page.";
    }
  }

  function scheduleSave() {
    if (!isCreate) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDraft, 250);
  }

  function ensureFormCount(count) {
    while (Number.parseInt(totalForms.value, 10) < count) addRow();
  }

  function restoreDraft() {
    if (!isCreate || form.dataset.hasErrors === "true") return;
    try {
      const draft = JSON.parse(sessionStorage.getItem(draftKey) || "null");
      if (!Array.isArray(draft) || !draft.length) return;
      const savedTotal = draft.find((entry) => entry.name === "lines-TOTAL_FORMS");
      if (savedTotal) ensureFormCount(Number.parseInt(savedTotal.value, 10) || 0);
      draft.forEach((entry) => {
        const field = [...form.elements].find((candidate) => candidate.name === entry.name);
        if (!field || field.name === "lines-TOTAL_FORMS") return;
        if (entry.checked !== null) field.checked = entry.checked;
        else field.value = entry.value;
      });
      status.textContent = "Your saved request progress was restored.";
    } catch (_) {
      sessionStorage.removeItem(draftKey);
    }
  }

  document.addEventListener("click", (event) => {
    const addButton = event.target.closest(".add-inventory-item");
    if (addButton) addInventoryItem(addButton.dataset.itemId, Boolean(addButton.closest("dialog")));

    const removeButton = event.target.closest(".remove-request-line");
    if (removeButton) {
      const row = removeButton.closest(".request-line");
      const deleted = row.querySelector('input[name$="-DELETE"]');
      if (deleted) deleted.checked = true;
      row.hidden = true;
      if (!activeRows().length) addRow();
      status.textContent = "Line removed.";
      scheduleSave();
    }
  });

  document.getElementById("add-request-line")?.addEventListener("click", () => {
    const row = addRow();
    row.querySelector("select")?.focus();
    scheduleSave();
  });

  function filterModalInventory() {
    if (!modalList) return;
    const words = (modalSearch?.value || "").toLowerCase().trim().split(/\s+/).filter(Boolean);
    let visible = 0;
    modalList.querySelectorAll(".inventory-picker-item").forEach((item) => {
      const matchesText = words.every((word) => item.dataset.search.includes(word));
      const matchesCategory = !activeCategory || item.dataset.category === activeCategory;
      item.hidden = !(matchesText && matchesCategory);
      if (!item.hidden) visible += 1;
    });
    if (modalCount) modalCount.textContent = `${visible} item${visible === 1 ? "" : "s"}`;
    if (modalNoResults) modalNoResults.hidden = visible !== 0;
  }

  function openInventoryBrowser() {
    if (!browserDialog || !modalList || !pickerList) return;
    if (!modalList.children.length) {
      modalList.innerHTML = [...pickerList.querySelectorAll(".inventory-picker-item")]
        .map((item) => item.outerHTML).join("");
    }
    filterModalInventory();
    browserDialog.showModal();
    modalSearch?.focus();
  }

  document.getElementById("open-inventory-browser")?.addEventListener("click", openInventoryBrowser);
  browserDialog?.querySelector(".inventory-browser-close")?.addEventListener("click", () => browserDialog.close());
  browserDialog?.addEventListener("click", (event) => {
    if (event.target === browserDialog) browserDialog.close();
  });
  modalSearch?.addEventListener("input", filterModalInventory);
  document.querySelectorAll("[data-category-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      activeCategory = button.dataset.categoryFilter;
      document.querySelectorAll("[data-category-filter]").forEach((candidate) => {
        candidate.classList.toggle("active", candidate === button);
      });
      filterModalInventory();
    });
  });

  search?.addEventListener("input", () => {
    const words = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    let visible = 0;
    document.querySelectorAll(".inventory-picker-item").forEach((item) => {
      const matches = words.every((word) => item.dataset.search.includes(word));
      item.hidden = !matches;
      if (matches) visible += 1;
    });
    resultCount.textContent = `${visible} item${visible === 1 ? "" : "s"}`;
    noResults.hidden = visible !== 0;
  });

  form.addEventListener("input", scheduleSave);
  form.addEventListener("change", scheduleSave);
  form.addEventListener("submit", () => {
    // Keep the recovery draft until the server confirms creation. The detail-page
    // redirect removes it after a successful save.
  });
  document.getElementById("clear-request-draft")?.addEventListener("click", () => {
    sessionStorage.removeItem(draftKey);
    location.reload();
  });

  restoreDraft();
})();
