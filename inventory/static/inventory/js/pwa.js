(() => {
  'use strict';

  if (!('serviceWorker' in navigator)) return;

  let installPrompt = null;
  const installButtons = Array.from(document.querySelectorAll('[data-pwa-install]'));
  const isiOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;

  const hideInstall = () => installButtons.forEach((button) => { button.hidden = true; });
  const showInstall = () => installButtons.forEach((button) => { button.hidden = false; });
  const showMessage = (message, tone = 'info') => {
    const region = document.getElementById('toast-region');
    if (!region) {
      window.alert(message);
      return;
    }
    const item = document.createElement('div');
    item.className = `toast ${tone}`;
    item.textContent = message;
    region.appendChild(item);
    window.setTimeout(() => item.remove(), 5500);
  };

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    installPrompt = event;
    showInstall();
  });

  if (isiOS && !standalone) showInstall();

  installButtons.forEach((button) => button.addEventListener('click', async () => {
    if (installPrompt) {
      installPrompt.prompt();
      await installPrompt.userChoice;
      installPrompt = null;
      hideInstall();
      return;
    }
    if (isiOS) {
      showMessage('To install: tap Share, then Add to Home Screen.');
    }
  }));

  window.addEventListener('appinstalled', () => {
    installPrompt = null;
    hideInstall();
  });

  // Every non-GET form remains network-only. Block it before submission when the
  // browser already knows it is offline; no write is stored or replayed later.
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if ((form.method || 'get').toLowerCase() === 'get' || navigator.onLine) return;
    event.preventDefault();
    showMessage('You are offline. This change was not saved. Reconnect and try again.', 'error');
  }, true);

  window.addEventListener('offline', () => {
    showMessage('You are offline. Inventory changes are disabled until you reconnect.', 'error');
  });
  window.addEventListener('online', () => {
    showMessage('Connection restored. You can safely continue.', 'success');
  });

  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', {scope: '/'}).catch((error) => {
      console.warn('App service worker registration failed.', error);
    });
  });
})();
