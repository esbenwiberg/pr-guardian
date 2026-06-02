/**
 * PR Guardian — Shared Sidebar
 *
 * Renders into <aside id="sidebar"> on every page. Primary slots:
 *   Reviews · Pull Requests · Insights · Profiles · Settings · Help
 *
 * Capabilities come from /api/me; restricted items are hidden until that
 * resolves so ordinary users never see them flash on first paint.
 */
(function () {
  'use strict';

  // Embed mode (Brief 06): when ?embed=1 is present we're inside an iframe
  // on /settings — suppress the sidebar entirely and drop the body's
  // ml-16/ml-64 offset so the legacy page fills its frame.
  const EMBED = /[?&]embed=1\b/.test(window.location.search);
  if (EMBED) {
    const style = document.createElement('style');
    style.textContent = `
      #sidebar { display: none !important; }
      .ml-16, .lg\\:ml-64 { margin-left: 0 !important; }
      header.sticky { display: none !important; }
      body { background: transparent !important; }
    `;
    document.head.appendChild(style);
    return;  // skip sidebar render entirely
  }

  const SHIELD = '<svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 2.18l7 3.12v4.7c0 4.83-3.13 9.37-7 10.82-3.87-1.45-7-5.99-7-10.82V6.3l7-3.12z"/><path d="M10 15.5l-3.5-3.5 1.41-1.41L10 12.67l5.59-5.59L17 8.5l-7 7z"/></svg>';

  const ICON_REVIEWS  = '<path stroke-linecap="round" stroke-linejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 0 0 2.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 0 0-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75 2.25 2.25 0 0 0-.1-.664m-5.8 0A2.251 2.251 0 0 1 13.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25ZM6.75 12h.008v.008H6.75V12Zm0 3h.008v.008H6.75V15Zm0 3h.008v.008H6.75V18Z"/>';
  const ICON_PULLS    = '<path stroke-linecap="round" stroke-linejoin="round" d="M7.5 3.75a2.25 2.25 0 1 0 0 4.5 2.25 2.25 0 0 0 0-4.5ZM7.5 8.25v7.5m0 0a2.25 2.25 0 1 0 0 4.5 2.25 2.25 0 0 0 0-4.5Zm9-12v3.75a3 3 0 0 1-3 3H12m4.5-6.75a2.25 2.25 0 1 0 0 4.5 2.25 2.25 0 0 0 0-4.5Zm0 12a2.25 2.25 0 1 0 0 4.5 2.25 2.25 0 0 0 0-4.5Zm0 0v-2.25a3 3 0 0 0-3-3H12"/>';
  const ICON_INSIGHTS = '<path stroke-linecap="round" stroke-linejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"/>';
  const ICON_PROFILES = '<path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"/><path stroke-linecap="round" stroke-linejoin="round" d="M18.75 9.75v4.5m2.25-2.25h-4.5"/>';
  const ICON_SETTINGS = '<path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.248a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"/>';

  const NAV_PRIMARY = [
    { key: 'reviews',  name: 'Reviews',  url: '/reviews',  icon: ICON_REVIEWS },
    { key: 'pulls',    name: 'Pull Requests', url: '/pull-requests', icon: ICON_PULLS },
    { key: 'insights', name: 'Insights', url: '/insights', icon: ICON_INSIGHTS },
  ];
  const NAV_PROFILES = { key: 'profiles', name: 'Profiles', url: '/profiles', icon: ICON_PROFILES };
  const NAV_ADMIN = { key: 'settings', name: 'Settings', url: '/settings', icon: ICON_SETTINGS };

  const HELP_LINKS = [
    { name: 'How it works',  url: '/help/how-it-works' },
    { name: 'CLI reference', url: '/help/cli' },
    { name: 'API reference', url: '/help/api' },
  ];

  function isActive(url) {
    const p = window.location.pathname;
    if (url === '/reviews') return p === '/' || p === '/reviews' || p.startsWith('/reviews/');
    if (url === '/pull-requests') return p === '/pull-requests';
    if (url === '/insights') return p === '/insights';
    if (url === '/profiles') return p === '/profiles';
    if (url === '/settings') return p === '/settings';
    return p === url || p.startsWith(url + '/');
  }

  function icon(pathD) {
    return `<svg class="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">${pathD}</svg>`;
  }

  function navItem(n) {
    return `
      <a href="${n.url}" class="sidebar-item${isActive(n.url) ? ' active' : ''}" data-nav="${n.key}">
        ${icon(n.icon)}
        <span class="hidden lg:block">${n.name}</span>
      </a>`;
  }

  function helpPopover() {
    const items = HELP_LINKS.map(l => `<a href="${l.url}" class="block px-3 py-1.5 text-xs text-slate-300 hover:text-slate-50 hover:bg-slate-800/60 rounded">${l.name}</a>`).join('');
    return `
      <div class="relative" id="help-menu">
        <button id="help-toggle" class="flex items-center gap-2 w-full px-3 py-1.5 text-2xs text-slate-500 hover:text-slate-300 transition-colors rounded-lg hover:bg-slate-800/60">
          <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 5.25h.008v.008H12v-.008Z"/></svg>
          <span>Help</span>
          <span class="ml-auto text-slate-600">&#9662;</span>
        </button>
        <div id="help-popover" class="hidden absolute left-0 right-0 bottom-full mb-1 p-1 bg-slate-900 border border-slate-700 rounded-lg shadow-xl z-30">
          ${items}
        </div>
      </div>`;
  }

  const el = document.getElementById('sidebar');
  if (!el) return;

  // Read admin status from a synchronously-injected hint, if present, to avoid flash.
  const seedAdmin = (window.__currentUser && window.__currentUser.is_admin) ? true : false;
  const seedManageProfiles = seedAdmin || (
    (window.__currentUser && window.__currentUser.can_manage_profiles) ? true : false
  );

  function render(user) {
    const isAdmin = Boolean(user && user.is_admin);
    const canManageProfiles = isAdmin || Boolean(user && user.can_manage_profiles);
    const items = NAV_PRIMARY.map(navItem).join('')
      + (canManageProfiles ? navItem(NAV_PROFILES) : '')
      + (isAdmin ? navItem(NAV_ADMIN) : '');
    el.className = 'sidebar';
    el.innerHTML = `
      <div class="sidebar-header">
        <div class="sidebar-logo">${SHIELD}</div>
        <span class="text-sm font-semibold text-slate-50 hidden lg:block">PR Guardian</span>
        <span class="ml-auto text-2xs font-mono text-slate-500 hidden lg:block">v0.1</span>
      </div>
      <nav class="sidebar-nav">
        ${items}
      </nav>
      <div class="sidebar-footer hidden lg:block">
        <button onclick="window.__cmdPalette?.open()" class="flex items-center gap-2 w-full px-3 py-1.5 text-2xs text-slate-500 hover:text-slate-300 transition-colors rounded-lg hover:bg-slate-800/60 mb-1">
          <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"/></svg>
          <span>Search</span>
          <kbd class="kbd ml-auto" style="font-size:9px">⌘K</kbd>
        </button>
        ${helpPopover()}
        <div id="sidebar-footer-extra"></div>
      </div>`;
    wireHelpPopover();
  }

  function wireHelpPopover() {
    const toggle = document.getElementById('help-toggle');
    const pop = document.getElementById('help-popover');
    if (!toggle || !pop) return;
    toggle.addEventListener('click', (e) => {
      e.preventDefault();
      pop.classList.toggle('hidden');
    });
    document.addEventListener('click', (e) => {
      if (!document.getElementById('help-menu')?.contains(e.target)) {
        pop.classList.add('hidden');
      }
    });
  }

  // Initial render with the synchronous hint, then refine after /api/me resolves.
  render({ is_admin: seedAdmin, can_manage_profiles: seedManageProfiles });

  fetch('/api/me', { credentials: 'same-origin' })
    .then(r => r.ok ? r.json() : null)
    .then(user => {
      if (!user) return;
      window.__currentUser = user;
      if (
        Boolean(user.is_admin) !== seedAdmin
        || Boolean(user.can_manage_profiles) !== seedManageProfiles
      ) {
        render(user);
      }
    })
    .catch(() => {});

  // One-shot admin-required toast (set by /settings redirect for non-admins).
  if (new URLSearchParams(window.location.search).get('error') === 'admin_required') {
    showToast('Settings is admin-only.');
    const url = new URL(window.location.href);
    url.searchParams.delete('error');
    window.history.replaceState({}, '', url.toString());
  }
  if (new URLSearchParams(window.location.search).get('error') === 'profile_manager_required') {
    showToast('Profiles is available to admins and Profile Managers.');
    const url = new URL(window.location.href);
    url.searchParams.delete('error');
    window.history.replaceState({}, '', url.toString());
  }

  function showToast(msg) {
    const t = document.createElement('div');
    t.textContent = msg;
    t.className = 'fixed bottom-4 right-4 z-50 px-4 py-2 bg-slate-800 border border-slate-700 text-slate-100 text-xs rounded-lg shadow-xl';
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3500);
  }
})();
