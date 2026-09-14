(() => {
  'use strict';
  // Shared scanner feedback only. Camera lifecycle and form-specific lookup
  // behavior intentionally remain owned by each screen.
  const successBeep = () => {
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      const context = new AudioContext();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.setValueAtTime(880, context.currentTime);
      gain.gain.setValueAtTime(0.001, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.18, context.currentTime + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.14);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + 0.16);
      window.setTimeout(() => context.close(), 260);
    } catch (_) { /* audio feedback is optional */ }
  };
  window.WMSScanner = Object.freeze({successBeep});

  const params = new URLSearchParams(window.location.search);
  if (params.get('created') === '1') {
    try { sessionStorage.removeItem('bbx-material-request-draft-v1:/material-requests/new/'); } catch (_) { /* storage may be unavailable */ }
  }
  const root = document.documentElement;
  const themeButtons = Array.from(document.querySelectorAll('[data-theme-toggle]'));
  const applyTheme = (theme) => {
    root.dataset.theme = theme;
    const isLight = theme === 'light';
    themeButtons.forEach((button) => {
      button.setAttribute('aria-pressed', String(isLight));
      button.setAttribute('aria-label', `Switch to ${isLight ? 'dark' : 'light'} theme`);
      button.title = `Switch to ${isLight ? 'dark' : 'light'} theme`;
      const label = button.querySelector('[data-theme-label]');
      if (label) label.textContent = `${isLight ? 'Dark' : 'Light'} theme`;
    });
  };
  applyTheme(root.dataset.theme || 'dark');
  themeButtons.forEach((button) => button.addEventListener('click', () => {
    const next = root.dataset.theme === 'light' ? 'dark' : 'light';
    localStorage.setItem('bbx-theme', next);
    applyTheme(next);
  }));

  // Every standard WMS action uses one predictable semantic color. Explicit
  // data-action values win; otherwise legacy markup is classified by intent.
  const SEMANTIC_BUTTON_CLASSES = [
    'btn-primary', 'btn-secondary', 'btn-success',
    'btn-warning', 'btn-danger', 'btn-info'
  ];
  const buttonRole = (button) => {
    const explicit = button.dataset.action;
    if (SEMANTIC_BUTTON_CLASSES.includes(`btn-${explicit}`)) return `btn-${explicit}`;
    const serverRenderedRole = SEMANTIC_BUTTON_CLASSES.find(className => button.classList.contains(className));
    if (serverRenderedRole) return serverRenderedRole;
    const form = button.closest('form');
    const intent = `${button.textContent} ${button.getAttribute('aria-label') || ''} ${button.getAttribute('title') || ''} ${button.getAttribute('href') || ''} ${form?.action || ''}`.toLowerCase();
    if (/delete|remove|reject|discard|clear all|start over/.test(intent)) return 'btn-danger';
    if (/finalize|complete|mark picked|ready for delivery|closed.?delivered|approve|receive stock|new receiving|upload.+receive/.test(intent)) return 'btn-success';
    if (/print|export|download|scan|camera|barcode|label|app qr/.test(intent)) return 'btn-info';
    if (/edit|adjust|rename|commit|change password/.test(intent)) return 'btn-warning';
    if (/back|cancel|view|clear|reset|close|history|settings|dashboard|inventory|done|archive|preview|previous|next|active board|all locations|\bopen\b/.test(intent)) return 'btn-secondary';
    return 'btn-primary';
  };
  const assignSemanticButton = (button) => {
    if (!(button instanceof HTMLElement) || !button.matches('.btn, .action-chip, .quick-action-btn')) return;
    const role = buttonRole(button);
    button.classList.remove(...SEMANTIC_BUTTON_CLASSES, 'primary', 'secondary', 'light', 'accent', 'danger', 'success', 'warning', 'warn', 'info');
    button.classList.add(role);
    if (button.classList.contains('sm')) button.classList.add('btn-sm');
    if (button.classList.contains('lg')) button.classList.add('btn-lg');
  };
  document.querySelectorAll('.btn').forEach(assignSemanticButton);
  document.querySelectorAll('.action-chip, .quick-action-btn').forEach(assignSemanticButton);
  new MutationObserver(mutations => mutations.forEach(({addedNodes}) => addedNodes.forEach(node => {
    if (!(node instanceof HTMLElement)) return;
    assignSemanticButton(node);
    node.querySelectorAll?.('.btn, .action-chip, .quick-action-btn').forEach(assignSemanticButton);
  }))).observe(document.body, {childList: true, subtree: true});

  const navButton = document.querySelector('[data-mobile-nav-toggle]');
  const nav = document.querySelector('[data-mobile-nav-panel]');
  const navLabel = document.querySelector('[data-mobile-nav-label]');
  const navCloseButton = document.querySelector('[data-mobile-nav-close]');
  const navBackdrop = document.querySelector('[data-mobile-nav-backdrop]');
  const pageRegions = [document.querySelector('main'), document.getElementById('toast-region')].filter(Boolean);
  const pageRegionAria = new Map();
  const focusableNavItems = () => [...nav.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter(element => !element.hidden && element.getClientRects().length);
  const setPageInert = inert => {
    pageRegions.forEach(region => {
      if (inert) {
        pageRegionAria.set(region, region.getAttribute('aria-hidden'));
        region.inert = true;
        region.setAttribute('aria-hidden', 'true');
      } else {
        region.inert = false;
        const previous = pageRegionAria.get(region);
        if (previous === null || previous === undefined) region.removeAttribute('aria-hidden');
        else region.setAttribute('aria-hidden', previous);
      }
    });
    if (!inert) pageRegionAria.clear();
  };
  const closeNav = () => {
    if (!nav || !navButton) return;
    nav.classList.remove('is-open');
    navBackdrop?.classList.remove('is-open');
    document.body.classList.remove('nav-open');
    navButton.classList.remove('is-open');
    navButton.setAttribute('aria-expanded', 'false');
    navButton.setAttribute('aria-label', 'Open navigation menu');
    nav.removeAttribute('role');
    nav.removeAttribute('aria-modal');
    setPageInert(false);
    if (navLabel) navLabel.textContent = 'Menu';
  };
  const openNav = () => {
    if (!nav || !navButton) return;
    nav.classList.add('is-open');
    navBackdrop?.classList.add('is-open');
    document.body.classList.add('nav-open');
    navButton.classList.add('is-open');
    navButton.setAttribute('aria-expanded', 'true');
    navButton.setAttribute('aria-label', 'Close navigation menu');
    nav.setAttribute('role', 'dialog');
    nav.setAttribute('aria-modal', 'true');
    setPageInert(true);
    if (navLabel) navLabel.textContent = 'Close';
    window.setTimeout(() => navCloseButton?.focus(), 0);
  };
  navButton?.addEventListener('click', () => {
    nav?.classList.contains('is-open') ? closeNav() : openNav();
  });
  navCloseButton?.addEventListener('click', () => {
    closeNav();
    navButton?.focus();
  });
  navBackdrop?.addEventListener('click', () => {
    closeNav();
    navButton?.focus();
  });
  nav?.querySelectorAll('a').forEach(link => link.addEventListener('click', closeNav));
  document.addEventListener('keydown', event => {
    if (!document.body.classList.contains('nav-open')) return;
    if (event.key === 'Escape') {
      closeNav();
      navButton?.focus();
      return;
    }
    if (event.key === 'Tab') {
      const items = focusableNavItems();
      if (!items.length) {
        event.preventDefault();
        nav?.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || !nav.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
  document.addEventListener('click', event => {
    if (nav?.classList.contains('is-open') && !event.target.closest('.site-header')) closeNav();
  });
  window.matchMedia('(min-width: 1121px)').addEventListener?.('change', event => {
    if (event.matches) closeNav();
    closeDesktopMenus();
  });

  const headerMoreToggle = document.querySelector('[data-header-more-toggle]');
  const headerMoreMenu = document.querySelector('[data-header-more-menu]');
  const accountToggle = document.querySelector('[data-account-toggle]');
  const accountMenu = document.querySelector('[data-account-menu]');
  const setHeaderMenu = (toggle, menu, open) => {
    if (!toggle || !menu) return;
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  };
  const closeDesktopMenus = (except = null) => {
    if (except !== headerMoreMenu) setHeaderMenu(headerMoreToggle, headerMoreMenu, false);
    if (except !== accountMenu) setHeaderMenu(accountToggle, accountMenu, false);
  };
  headerMoreToggle?.addEventListener('click', () => {
    const opening = headerMoreMenu.hidden;
    closeDesktopMenus(headerMoreMenu);
    setHeaderMenu(headerMoreToggle, headerMoreMenu, opening);
  });
  accountToggle?.addEventListener('click', () => {
    const opening = accountMenu.hidden;
    closeDesktopMenus(accountMenu);
    setHeaderMenu(accountToggle, accountMenu, opening);
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.header-more') && !event.target.closest('.account-menu')) closeDesktopMenus();
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const returnFocus = headerMoreMenu && !headerMoreMenu.hidden ? headerMoreToggle : accountMenu && !accountMenu.hidden ? accountToggle : null;
    closeDesktopMenus();
    returnFocus?.focus();
  });

  const notificationCenter = document.querySelector('[data-notification-center]');
  const notificationToggle = document.querySelector('[data-notification-toggle]');
  const notificationPanel = document.querySelector('[data-notification-panel]');
  const notificationList = document.querySelector('[data-notification-list]');
  const notificationCount = document.querySelector('[data-notification-count]');
  const notificationKey = `bbx-notifications-v1:${location.host}:${document.body.dataset.notificationUser || 'guest'}`;
  let notificationState = {items: [], unread: 0};
  try { notificationState = {...notificationState, ...JSON.parse(localStorage.getItem(notificationKey) || '{}')}; } catch (_) { /* ignore invalid storage */ }
  document.querySelectorAll('[data-initial-notification]').forEach((item, index) => {
    const text = item.querySelector('span')?.textContent?.trim();
    if (text && !notificationState.items.some(entry => entry.title === text)) {
      notificationState.items.unshift({id: `message-${Date.now()}-${index}`, title: text, body: '', url: '', at: new Date().toISOString()});
      notificationState.unread += 1;
    }
  });
  const saveNotifications = () => {
    notificationState.items = notificationState.items.slice(0, 30);
    try { localStorage.setItem(notificationKey, JSON.stringify(notificationState)); } catch (_) { /* storage may be unavailable */ }
  };
  const dismissNotification = id => {
    notificationState.items = notificationState.items.filter(item => String(item.id) !== String(id));
    saveNotifications();
    renderNotifications();
  };
  const renderUrgentAlerts = () => {
    let container = document.querySelector('[data-urgent-alerts]');
    const urgentItems = notificationState.items.filter(item => item.urgent);
    if (!urgentItems.length) { container?.remove(); return; }
    if (!container) {
      container = document.createElement('aside'); container.className = 'urgent-alert-stack'; container.dataset.urgentAlerts = '';
      container.setAttribute('aria-live', 'assertive'); document.body.appendChild(container);
    }
    container.replaceChildren();
    urgentItems.forEach(item => {
      const alert = document.createElement('div'); alert.className = 'urgent-alert'; alert.setAttribute('role', 'alert');
      const content = item.url ? document.createElement('a') : document.createElement('span');
      if (item.url) content.href = item.url;
      const strong = document.createElement('strong'); strong.textContent = item.title; content.appendChild(strong);
      if (item.body) { const body = document.createElement('small'); body.textContent = item.body; content.appendChild(body); }
      const dismiss = document.createElement('button'); dismiss.type = 'button'; dismiss.className = 'urgent-alert-dismiss'; dismiss.textContent = 'Dismiss';
      dismiss.setAttribute('aria-label', `Dismiss ${item.title}`); dismiss.addEventListener('click', () => dismissNotification(item.id));
      alert.append(content, dismiss); container.appendChild(alert);
    });
  };
  const renderNotifications = () => {
    if (!notificationList || !notificationCount) return;
    notificationList.replaceChildren();
    if (!notificationState.items.length) {
      const empty = document.createElement('li'); empty.className = 'notification-empty'; empty.textContent = 'No notifications yet.'; notificationList.appendChild(empty);
    }
    notificationState.items.forEach(item => {
      const li = document.createElement('li'); li.className = `notification-item${item.urgent ? ' is-urgent' : ''}`;
      const content = item.url ? document.createElement('a') : document.createElement('span');
      if (item.url) content.href = item.url;
      const strong = document.createElement('strong'); strong.textContent = item.title; content.appendChild(strong);
      if (item.body) { const body = document.createElement('small'); body.textContent = item.body; content.appendChild(body); }
      li.appendChild(content);
      const time = document.createElement('time'); time.textContent = new Date(item.at).toLocaleString([], {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}); li.appendChild(time);
      notificationList.appendChild(li);
    });
    notificationCount.textContent = notificationState.unread > 99 ? '99+' : String(notificationState.unread);
    notificationCount.hidden = notificationState.unread < 1;
    notificationToggle?.setAttribute('aria-label', notificationState.unread ? `Notifications, ${notificationState.unread} unread` : 'Notifications');
    renderUrgentAlerts();
  };
  const addNotification = item => {
    const id = String(item.id || `${Date.now()}-${Math.random()}`);
    if (notificationState.items.some(entry => String(entry.id) === id)) return;
    notificationState.items.unshift({id, title: item.title || 'Notification', body: item.body || '', url: item.url || '', at: item.created_at || new Date().toISOString(), urgent: Boolean(item.urgent || item.require_interaction || item.requireInteraction)});
    notificationState.unread += 1; saveNotifications(); renderNotifications();
  };
  window.BBXNotifications = {add: addNotification};
  notificationToggle?.addEventListener('click', () => {
    const opening = notificationPanel.hidden;
    notificationPanel.hidden = !opening;
    notificationToggle.setAttribute('aria-expanded', String(opening));
    if (opening) { notificationState.unread = 0; saveNotifications(); renderNotifications(); }
  });
  document.querySelector('[data-notification-clear]')?.addEventListener('click', () => { notificationState = {items: [], unread: 0}; saveNotifications(); renderNotifications(); });
  document.addEventListener('click', event => {
    if (notificationPanel && !notificationPanel.hidden && !event.target.closest('[data-notification-center]')) { notificationPanel.hidden = true; notificationToggle?.setAttribute('aria-expanded', 'false'); }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && notificationPanel && !notificationPanel.hidden) {
      notificationPanel.hidden = true;
      notificationToggle?.setAttribute('aria-expanded', 'false');
      notificationToggle?.focus();
    }
  });
  document.querySelector('[data-not-ready-toggle]')?.addEventListener('click', event => {
    const options = document.getElementById('not-ready-options'); if (!options) return;
    options.hidden = !options.hidden; event.currentTarget.setAttribute('aria-expanded', String(!options.hidden));
  });
  saveNotifications(); renderNotifications();

  const endpoint = document.body.dataset.eventsUrl;
  if (!endpoint) return; // Request portal pages deliberately never poll.

  const cursorKey = 'bbx-material-request-cursor-v1';
  const seen = new Set();
  const channel = 'BroadcastChannel' in window ? new BroadcastChannel('bbx-material-requests') : null;
  let cursor = localStorage.getItem(cursorKey);
  let timer;

  const saveCursor = (value) => {
    cursor = String(value);
    localStorage.setItem(cursorKey, cursor);
    channel?.postMessage({type: 'cursor', cursor});
  };
  const markSeen = (id) => {
    seen.add(String(id));
    channel?.postMessage({type: 'seen', id: String(id)});
  };
  channel?.addEventListener('message', ({data}) => {
    if (data?.type === 'seen') seen.add(String(data.id));
    if (data?.type === 'cursor' && Number(data.cursor) > Number(cursor || 0)) cursor = String(data.cursor);
  });

  const showToast = (event) => {
    if (seen.has(String(event.id))) return;
    markSeen(event.id);
    addNotification(event);
  };

  const poll = async () => {
    if (document.hidden) return;
    try {
      const url = cursor === null ? endpoint : `${endpoint}?cursor=${encodeURIComponent(cursor)}`;
      const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
      if (!response.ok) return;
      const payload = await response.json();
      payload.events.forEach(showToast);
      saveCursor(payload.cursor);
    } catch (_) { /* transient network failures are retried */ }
  };
  const schedule = () => {
    clearInterval(timer);
    if (!document.hidden) {
      poll();
      timer = window.setInterval(poll, 10000);
    }
  };
  document.addEventListener('visibilitychange', schedule);
  schedule();
})();
