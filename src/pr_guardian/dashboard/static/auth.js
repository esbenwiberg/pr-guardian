/**
 * Dashboard authentication via MSAL.js (Microsoft Entra ID).
 *
 * When Entra ID is configured (the /api/v1/auth/config endpoint returns
 * enabled=true), all API calls use bearer tokens acquired via MSAL.js.
 * When auth is disabled (dev mode), everything works without login.
 *
 * Usage in page scripts:
 *   await initAuth();                    // Call once on page load
 *   const data = await authFetch(url);   // Drop-in replacement for fetch()
 */

/* global msal */

let msalInstance = null;
let authEnabled = false;
let authConfig = null;

/**
 * Initialise MSAL.js if auth is enabled. Call once on DOMContentLoaded.
 */
async function initAuth() {
  try {
    const resp = await fetch('/api/v1/auth/config');
    authConfig = await resp.json();
  } catch {
    // Auth config endpoint not available — dev mode
    authConfig = { enabled: false };
  }

  if (!authConfig.enabled) {
    authEnabled = false;
    _showAuthUI(false);
    return;
  }

  // Load MSAL.js from CDN if not already present
  if (typeof msal === 'undefined') {
    await _loadScript(
      'https://alcdn.msauth.net/browser/2.38.3/js/msal-browser.min.js'
    );
  }

  const msalConfig = {
    auth: {
      clientId: authConfig.client_id,
      authority: `https://login.microsoftonline.com/${authConfig.tenant_id}`,
      redirectUri: window.location.origin + '/dashboard',
    },
    cache: {
      cacheLocation: 'sessionStorage',
      storeAuthStateInCookie: false,
    },
  };

  msalInstance = new msal.PublicClientApplication(msalConfig);
  await msalInstance.initialize();

  // Handle redirect callback (returning from Microsoft login)
  const redirectResult = await msalInstance.handleRedirectPromise();
  if (redirectResult) {
    msalInstance.setActiveAccount(redirectResult.account);
  }

  authEnabled = true;

  // Check if already signed in
  const accounts = msalInstance.getAllAccounts();
  if (accounts.length > 0) {
    msalInstance.setActiveAccount(accounts[0]);
    _showAuthUI(true, accounts[0]);
  } else {
    // Not signed in — trigger login
    _showAuthUI(true, null);
    await signIn();
  }
}

/**
 * Sign in via redirect (auth code + PKCE).
 */
async function signIn() {
  if (!msalInstance) return;

  const loginRequest = {
    scopes: authConfig.scopes || [],
  };

  try {
    await msalInstance.loginRedirect(loginRequest);
  } catch (err) {
    console.error('Login failed:', err);
  }
}

/**
 * Sign out and clear session.
 */
async function signOut() {
  if (!msalInstance) return;
  await msalInstance.logoutRedirect({ postLogoutRedirectUri: '/' });
}

/**
 * Acquire an access token silently (or via popup fallback).
 */
async function getAccessToken() {
  if (!msalInstance || !authEnabled) return null;

  const account = msalInstance.getActiveAccount();
  if (!account) return null;

  const tokenRequest = {
    scopes: authConfig.scopes || [],
    account: account,
  };

  try {
    const result = await msalInstance.acquireTokenSilent(tokenRequest);
    return result.accessToken;
  } catch (err) {
    // Silent token acquisition failed — try interactive
    if (err instanceof msal.InteractionRequiredAuthError) {
      try {
        const result = await msalInstance.acquireTokenPopup(tokenRequest);
        return result.accessToken;
      } catch (popupErr) {
        console.error('Token popup failed:', popupErr);
        return null;
      }
    }
    console.error('Token acquisition failed:', err);
    return null;
  }
}

/**
 * Authenticated fetch — drop-in replacement for fetch().
 * Adds Authorization header when auth is enabled.
 */
async function authFetch(url, options = {}) {
  if (authEnabled) {
    const token = await getAccessToken();
    if (token) {
      options.headers = {
        ...options.headers,
        Authorization: `Bearer ${token}`,
      };
    }
  }
  return fetch(url, options);
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function _loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

function _showAuthUI(enabled, account) {
  // Inject a small auth status indicator into the sidebar if present
  const sidebar = document.querySelector('.sidebar-header');
  if (!sidebar) return;

  // Remove existing auth indicator
  const existing = document.getElementById('auth-indicator');
  if (existing) existing.remove();

  if (!enabled) return;

  const indicator = document.createElement('div');
  indicator.id = 'auth-indicator';
  indicator.style.cssText =
    'position:fixed;bottom:12px;left:12px;font-size:11px;color:#94a3b8;z-index:50;';

  if (account) {
    const name = account.name || account.username || '';
    indicator.innerHTML = `
      <span title="${name}" style="cursor:pointer" onclick="signOut()">
        ${name.split(' ')[0]} · sign out
      </span>`;
  } else {
    indicator.innerHTML = `
      <span style="cursor:pointer" onclick="signIn()">
        sign in
      </span>`;
  }

  document.body.appendChild(indicator);
}
