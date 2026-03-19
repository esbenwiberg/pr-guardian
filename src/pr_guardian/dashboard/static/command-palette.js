/**
 * PR Guardian — Command Palette (Cmd+K / Ctrl+K)
 * Self-contained: creates its own DOM, handles keyboard events.
 */
(function () {
  'use strict';

  const PAGES = [
    { name: 'Dashboard',     url: '/dashboard',       section: 'pages' },
    { name: 'Reviews',       url: '/reviews',         section: 'pages' },
    { name: 'Scans',         url: '/scans',           section: 'pages' },
    { name: 'Prompts',       url: '/prompts',         section: 'pages' },
    { name: 'How It Works',  url: '/how-it-works',    section: 'pages' },
    { name: 'CLI Reference', url: '/cli-reference',   section: 'pages' },
    { name: 'API Reference', url: '/api-reference',   section: 'pages' },
    { name: 'Settings',      url: '/settings',        section: 'pages' },
  ];

  let overlay = null;
  let input = null;
  let resultsEl = null;
  let items = [];
  let highlighted = 0;
  let recentReviews = [];

  // ---- DOM ----

  function create() {
    overlay = document.createElement('div');
    overlay.id = 'cmd-palette';
    overlay.className = 'hidden';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:200';
    overlay.innerHTML = `
      <div style="position:fixed;inset:0;background:rgba(0,0,0,0.55);backdrop-filter:blur(4px);z-index:200" data-backdrop></div>
      <div style="position:fixed;inset:0;display:flex;align-items:flex-start;justify-content:center;padding-top:min(18vh,160px);z-index:201;pointer-events:none">
        <div class="command-palette-box" style="pointer-events:auto">
          <div class="flex items-center border-b border-slate-700/50">
            <svg class="w-4 h-4 text-slate-500 ml-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"/></svg>
            <input class="command-palette-input" placeholder="Search pages, reviews..." autocomplete="off" spellcheck="false">
          </div>
          <div class="command-palette-results" data-results></div>
          <div class="flex items-center gap-4 px-4 py-2 border-t border-slate-700/50 text-2xs text-slate-500">
            <span><kbd class="kbd" style="font-size:9px">↑↓</kbd> navigate</span>
            <span><kbd class="kbd" style="font-size:9px">↵</kbd> open</span>
            <span><kbd class="kbd" style="font-size:9px">esc</kbd> close</span>
          </div>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    input = overlay.querySelector('.command-palette-input');
    resultsEl = overlay.querySelector('[data-results]');

    overlay.querySelector('[data-backdrop]').addEventListener('click', close);
    input.addEventListener('input', () => { highlighted = 0; render(input.value.trim().toLowerCase()); });
    input.addEventListener('keydown', onKeydown);

    fetchReviews();
  }

  async function fetchReviews() {
    try {
      const r = await fetch('/api/dashboard/reviews?limit=25');
      if (r.ok) recentReviews = await r.json();
    } catch {}
  }

  // ---- Open / Close ----

  function open() {
    if (!overlay) create();
    overlay.classList.remove('hidden');
    input.value = '';
    highlighted = 0;
    render('');
    requestAnimationFrame(() => input.focus());
  }

  function close() {
    if (overlay) overlay.classList.add('hidden');
  }

  function isOpen() {
    return overlay && !overlay.classList.contains('hidden');
  }

  // ---- Render ----

  function render(q) {
    items = [];
    let html = '';

    // Pages (always show, filter by query)
    const pages = PAGES.filter(p => !q || p.name.toLowerCase().includes(q));
    if (pages.length) {
      html += sectionLabel('Pages');
      pages.forEach(p => {
        const isCurrent = window.location.pathname === p.url;
        items.push({ url: p.url });
        html += renderItem(items.length - 1, p.name, isCurrent ? 'current' : '', '');
      });
    }

    // Reviews (show when query matches, or show recent 5 when no query)
    const reviews = q
      ? recentReviews.filter(r => {
          const s = `#${r.pr_id} ${r.title || ''} ${r.repo} ${r.author}`.toLowerCase();
          return s.includes(q);
        }).slice(0, 8)
      : recentReviews.slice(0, 5);

    if (reviews.length) {
      html += sectionLabel(q ? 'Reviews' : 'Recent reviews');
      reviews.forEach(r => {
        items.push({ url: `/reviews/${r.id}` });
        const dc = { auto_approve: 'text-emerald-400', human_review: 'text-orange-400', reject: 'text-red-400', hard_block: 'text-red-400' };
        const dl = { auto_approve: 'Approved', human_review: 'Review', reject: 'Rejected', hard_block: 'Blocked' };
        const badge = `<span class="${dc[r.decision] || 'text-slate-500'} text-2xs ml-auto shrink-0">${dl[r.decision] || ''}</span>`;
        html += renderItem(
          items.length - 1,
          `<span class="text-slate-500 font-mono text-2xs w-12 shrink-0">#${esc(r.pr_id)}</span>
           <span class="truncate">${esc(r.title || r.repo)}</span>
           ${badge}`,
          '', ''
        );
      });
    }

    if (!items.length) {
      html = '<div class="px-4 py-8 text-center text-sm text-slate-500">No results</div>';
    }

    resultsEl.innerHTML = html;
  }

  function sectionLabel(text) {
    return `<div class="dropdown-label">${text}</div>`;
  }

  function renderItem(idx, content, extra, kbd) {
    return `<div class="command-palette-item ${idx === highlighted ? 'highlighted' : ''} ${extra}" data-idx="${idx}"
      onmouseenter="this.parentNode.querySelectorAll('.highlighted').forEach(e=>e.classList.remove('highlighted'));this.classList.add('highlighted');window.__cmdP_h=${idx}"
      onclick="window.__cmdP_go(${idx})">${content}${kbd ? `<span class="command-palette-item-kbd">${kbd}</span>` : ''}</div>`;
  }

  // ---- Keyboard ----

  function onKeydown(e) {
    if (e.key === 'Escape') { close(); e.preventDefault(); return; }
    if (e.key === 'ArrowDown') { highlighted = Math.min(highlighted + 1, items.length - 1); updateHighlight(); e.preventDefault(); return; }
    if (e.key === 'ArrowUp') { highlighted = Math.max(highlighted - 1, 0); updateHighlight(); e.preventDefault(); return; }
    if (e.key === 'Enter' && items[highlighted]) { window.location = items[highlighted].url; close(); e.preventDefault(); }
  }

  function updateHighlight() {
    resultsEl.querySelectorAll('.command-palette-item').forEach(el => {
      el.classList.toggle('highlighted', parseInt(el.dataset.idx) === highlighted);
    });
    const active = resultsEl.querySelector('.highlighted');
    if (active) active.scrollIntoView({ block: 'nearest' });
  }

  // ---- Helpers ----

  function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  // ---- Global shortcut ----

  document.addEventListener('keydown', (e) => {
    // Cmd+K / Ctrl+K
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      isOpen() ? close() : open();
    }
  });

  // Expose minimal API for onclick handlers
  window.__cmdP_go = function (idx) { if (items[idx]) { window.location = items[idx].url; close(); } };
  window.__cmdP_h = 0; // track highlight from mouseenter
  window.__cmdPalette = { open, close };
})();
