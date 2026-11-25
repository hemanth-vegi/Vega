/**
 * web/api_client.js
 * Secure fetch wrapper for Vega 2.0 with automatic X-Vega-Token CSRF injection.
 */

let vegaSessionToken = null;

async function initVegaAuth() {
  if (vegaSessionToken) return vegaSessionToken;
  try {
    const res = await fetch('/api/auth/token');
    if (res.ok) {
      const data = await res.json();
      vegaSessionToken = data.token;
      window.VEGA_SESSION_TOKEN = vegaSessionToken;
    }
  } catch (err) {
    console.warn('Vega auth handshake:', err);
  }
  return vegaSessionToken;
}

/**
 * Universal wrapper around fetch that injects the CSRF token on mutating requests
 * and cleanly intercepts HTTP 423 (Locked) to trigger the lock screen without raw error bubbles.
 */
async function apiFetch(url, options = {}) {
  options.headers = options.headers || {};
  if (!vegaSessionToken) {
    await initVegaAuth();
  }
  if (vegaSessionToken) {
    if (options.headers instanceof Headers) {
      options.headers.set('X-Vega-Token', vegaSessionToken);
    } else {
      options.headers['X-Vega-Token'] = vegaSessionToken;
    }
  }
  const response = await fetch(url, options);
  if (response.status === 423) {
    if (typeof window !== 'undefined') {
      const now = Date.now();
      const recentlyUnlocked = (now - (window.lastUnlockedTs || 0)) < 5000;
      if (recentlyUnlocked) {
        console.warn('HTTP 423 received during unlock grace period; suppressed re-lock.');
        return response;
      }
      window.appIsLocked = true;
      if (typeof window.handleVegaLocked === 'function') {
        try {
          window.handleVegaLocked();
        } catch (e) {
          console.warn('handleVegaLocked notification notice:', e);
        }
      } else if (typeof window.showLockScreen === 'function') {
        try {
          window.showLockScreen();
        } catch (e) {
          console.warn('showLockScreen notification notice:', e);
        }
      }
    }
  }
  return response;
}

// Auto-run handshake
if (typeof window !== 'undefined') {
  window.apiFetch = apiFetch;
  window.initVegaAuth = initVegaAuth;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initVegaAuth());
  } else {
    initVegaAuth();
  }
}

