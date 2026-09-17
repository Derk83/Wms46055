(() => {
  'use strict';

  const button = document.querySelector('[data-push-toggle]');
  if (!button || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return;

  const configUrl = document.body.dataset.pushConfigUrl;
  const subscribeUrl = document.body.dataset.pushSubscribeUrl;
  const unsubscribeUrl = document.body.dataset.pushUnsubscribeUrl;
  let publicKey = '';
  let browserSubscription = null;

  const csrfToken = () => {
    const field = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (field) return field.value;
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : '';
  };

  const toast = (message, tone = 'info') => {
    const region = document.getElementById('toast-region');
    if (!region) return;
    const item = document.createElement('div');
    item.className = `toast ${tone}`;
    item.textContent = message;
    region.appendChild(item);
    window.setTimeout(() => item.remove(), 5000);
  };

  const decodeKey = (value) => {
    const padding = '='.repeat((4 - (value.length % 4)) % 4);
    const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = window.atob(base64);
    return Uint8Array.from([...raw].map((character) => character.charCodeAt(0)));
  };

  const serialize = (subscription) => {
    const data = subscription.toJSON();
    return {endpoint: data.endpoint, keys: data.keys};
  };

  const post = async (url, payload) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken()},
      body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error(`Notification preference update failed (${response.status}).`);
    return response.json();
  };

  const updateButton = () => {
    const enabled = Boolean(browserSubscription);
    button.hidden = false;
    button.classList.toggle('is-active', enabled);
    button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    button.setAttribute('aria-label', enabled ? 'Disable material request notifications' : 'Enable material request notifications');
    button.title = enabled ? 'Material request alerts are on' : 'Turn on material request alerts';
    button.textContent = enabled ? '🔔' : '🔕';
  };

  const initialize = async () => {
    const response = await fetch(configUrl, {credentials: 'same-origin', cache: 'no-store'});
    if (!response.ok) return;
    const config = await response.json();
    if (!config.enabled || !config.publicKey) return;
    publicKey = config.publicKey;
    const registration = await navigator.serviceWorker.ready;
    browserSubscription = await registration.pushManager.getSubscription();
    if (browserSubscription && Notification.permission === 'granted') {
      await post(subscribeUrl, serialize(browserSubscription));
    }
    updateButton();
  };

  button.addEventListener('click', async () => {
    if (!navigator.onLine) {
      toast('You must be online to change notification settings.', 'error');
      return;
    }
    button.disabled = true;
    try {
      if (browserSubscription) {
        if (!window.confirm('Turn off material request notifications on this device?')) return;
        await post(unsubscribeUrl, {endpoint: browserSubscription.endpoint});
        await browserSubscription.unsubscribe();
        browserSubscription = null;
        updateButton();
        toast('Material request notifications are off.');
        return;
      }
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        toast('Notifications were not enabled. You can change this in browser settings.', 'error');
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      browserSubscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: decodeKey(publicKey)
      });
      await post(subscribeUrl, serialize(browserSubscription));
      updateButton();
      toast('Material request notifications are on.', 'success');
    } catch (error) {
      console.warn(error);
      toast('Notification settings could not be updated. Check your connection and browser permissions.', 'error');
    } finally {
      button.disabled = false;
    }
  });

  document.querySelectorAll('form[data-push-logout]').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      if (form.dataset.pushLogoutReady === 'true' || !browserSubscription || !navigator.onLine) return;
      event.preventDefault();
      try {
        await post(unsubscribeUrl, {endpoint: browserSubscription.endpoint});
        await browserSubscription.unsubscribe();
      } catch (error) {
        console.warn('Could not remove the browser push endpoint during logout.', error);
      } finally {
        form.dataset.pushLogoutReady = 'true';
        form.requestSubmit();
      }
    });
  });

  navigator.serviceWorker.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'push-notification') {
      const payload = event.data.payload || {};
      if (window.BBXNotifications?.add) {
        window.BBXNotifications.add({
          id: payload.eventId,
          title: payload.title || 'Material request update',
          body: payload.body || '',
          url: payload.url || '',
          created_at: new Date().toISOString(),
          urgent: Boolean(payload.urgent),
          require_interaction: Boolean(payload.requireInteraction),
          request_id: payload.requestId || null,
          claim_url: payload.claimUrl || '',
          claimed_by: payload.claimedBy || ''
        });
      }
    }
  });

  initialize().catch((error) => console.warn('Push notification initialization failed.', error));
})();
