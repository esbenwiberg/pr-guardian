/* viewer-shell.js
 *
 * Brief 04 — shared shell loaded by all three review viewers so they can be
 * flipped between modes (wizard | chapters | findings) without losing the
 * user's local state.
 *
 * Each viewer is its own page; this script:
 *   1. Detects the current mode from the page's <title> (or window.__viewerMode).
 *   2. Renders a small segmented switcher into the existing header.
 *   3. On click / on `1`/`2`/`3` keypress, navigates to /reviews/{id}?mode=…
 *   4. Persists the last-used mode in localStorage["prg:viewer_mode"] so the
 *      reviews-queue page can deep-link the user back into the same mode.
 *   5. Exposes window.ReviewState — a tiny per-review key-value store backed
 *      by localStorage["prg:review:{id}"] so decisions, drafts, and scroll
 *      position survive a mode flip.
 */
(function () {
  'use strict';

  const PATH = window.location.pathname;
  const m = PATH.match(/^\/reviews\/([^/]+)\b/);
  const REVIEW_ID = m ? decodeURIComponent(m[1]) : '';
  if (!REVIEW_ID) return;

  // -------------------------------------------------------------------------
  // Mode detection
  // -------------------------------------------------------------------------
  const TITLE = (document.title || '').toLowerCase();
  const MODE = (window.__viewerMode
    || (TITLE.includes('wizard') ? 'wizard'
        : (TITLE.includes('human review') || TITLE.includes('chapters')) ? 'chapters'
        : 'findings'));

  const MODES = [
    { key: 'wizard',   label: 'Wizard',   hint: 'Q&A walkthrough' },
    { key: 'chapters', label: 'Chapters', hint: 'Section-by-section' },
    { key: 'findings', label: 'Findings', hint: 'Flat list view' },
  ];

  function navTo(mode) {
    if (mode === MODE) return;
    try { localStorage.setItem('prg:viewer_mode', mode); } catch (e) {}
    const url = new URL(window.location.href);
    url.searchParams.set('mode', mode);
    window.location.href = url.toString();
  }

  // Remember the mode the user actually landed on.
  try { localStorage.setItem('prg:viewer_mode', MODE); } catch (e) {}

  // -------------------------------------------------------------------------
  // Render the switcher into the existing header
  // -------------------------------------------------------------------------
  function injectSwitcher() {
    const header = document.querySelector('header');
    if (!header) {
      // Headerless layout — fall back to a floating chip.
      mountFloating();
      return;
    }
    if (header.querySelector('[data-viewer-switcher]')) return; // idempotent

    // The pre-shell viewers each had their own ad-hoc cross-link
    // ("Try wizard →" / "Switch to Chapters →"). The shared switcher
    // supersedes both. Hide via stylesheet with !important so page JS
    // that flips `style.display = ''` after loading can't override us.
    if (!document.getElementById('prg-viewer-shell-style')) {
      const style = document.createElement('style');
      style.id = 'prg-viewer-shell-style';
      style.textContent = '#hdr-wizard-link, #hdr-chapter-link { display: none !important; }';
      document.head.appendChild(style);
    }

    const wrap = document.createElement('div');
    wrap.setAttribute('data-viewer-switcher', '');
    wrap.style.cssText = [
      'display:inline-flex',
      'align-items:center',
      'gap:2px',
      'padding:3px',
      'border-radius:8px',
      'background:rgba(15,23,42,0.6)',
      'border:1px solid rgba(71,85,105,0.4)',
      'font-family:inherit',
      'font-size:12px',
      'margin-left:auto',
      'margin-right:12px',
    ].join(';');

    for (const m of MODES) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.dataset.mode = m.key;
      btn.textContent = m.label;
      btn.title = `${m.hint}  (press ${MODES.indexOf(m) + 1})`;
      const active = m.key === MODE;
      btn.style.cssText = [
        'cursor:' + (active ? 'default' : 'pointer'),
        'padding:5px 10px',
        'border-radius:6px',
        'border:0',
        'background:' + (active ? 'rgba(99,102,241,0.22)' : 'transparent'),
        'color:' + (active ? 'rgb(199,210,254)' : 'rgb(148,163,184)'),
        'font-weight:' + (active ? '600' : '500'),
        'font-size:12px',
        'transition:background 80ms ease, color 80ms ease',
      ].join(';');
      btn.addEventListener('click', () => navTo(m.key));
      btn.addEventListener('mouseenter', () => {
        if (m.key !== MODE) btn.style.color = 'rgb(226,232,240)';
      });
      btn.addEventListener('mouseleave', () => {
        if (m.key !== MODE) btn.style.color = 'rgb(148,163,184)';
      });
      wrap.appendChild(btn);
    }

    // Append to the end of the header. With margin-left:auto the switcher
    // pushes itself to the right, regardless of how the header arranges its
    // existing children. For headers that already use flex justify-between
    // we still appear at the far right.
    header.appendChild(wrap);

    // Finish-review button — Brief 05's entry point into the wrap-up modal.
    const finishBtn = document.createElement('button');
    finishBtn.type = 'button';
    finishBtn.id = 'prg-finish-review';
    finishBtn.textContent = 'Finish review →';
    finishBtn.style.cssText = [
      'cursor:pointer', 'padding:6px 12px', 'border-radius:6px',
      'border:1px solid rgba(16,185,129,0.5)',
      'background:rgba(16,185,129,0.18)', 'color:rgb(110,231,183)',
      'font-family:inherit', 'font-size:12px', 'font-weight:600',
      'margin-right:4px',
    ].join(';');
    finishBtn.addEventListener('click', openWrapUp);
    header.appendChild(finishBtn);
  }

  // -------------------------------------------------------------------------
  // Wrap-up modal — Brief 05
  // -------------------------------------------------------------------------
  function collectDecisions() {
    // Decisions are written by each viewer into ReviewState as
    //   { decisions: { <finding_id>: "accept"|"fix"|"dismiss" } }
    return window.ReviewState.get('decisions', {}) || {};
  }

  function decisionFromDismissal(status) {
    if (status === 'acknowledged') return 'accept';
    if (status === 'will_fix') return 'fix';
    if (status === 'false_positive' || status === 'by_design') return 'dismiss';
    return '';
  }

  function isActionableFinding(f) {
    const severity = String(f && f.severity || '').toLowerCase();
    const certainty = String(f && f.certainty || '').toLowerCase();
    return severity === 'high' || severity === 'critical'
      || (severity === 'medium' && certainty === 'detected');
  }

  function pluralFix(n) { return n === 1 ? '1 fix' : `${n} fixes`; }

  function buildSummary(decisions, findings, verdict, intro, mode) {
    const counts = { accept: 0, fix: 0, dismiss: 0 };
    Object.values(decisions || {}).forEach(d => { if (d in counts) counts[d]++; });
    const lines = [];
    const fixCount = counts.fix || ((verdict === 'request_changes' || verdict === 'block') ? findings.length : 0);
    if (fixCount) {
      if (mode === 'inline') {
        lines.push(`${intro} ${pluralFix(fixCount)} to address before merge. See inline comments.`);
      } else {
        lines.push(`${intro} ${pluralFix(fixCount)} to address before merge:`);
        findings.forEach(f => {
          const where = (f.file ? (f.line ? `${f.file}:${f.line}` : f.file) : '');
          const title = (f.description || '(no description)').split('\n')[0].slice(0, 140);
          lines.push(`- ${title}` + (where ? ` (${where})` : ''));
        });
      }
    } else if (verdict === 'request_changes') {
      lines.push(`${intro} Changes requested before merge.`);
    } else if (verdict === 'block') {
      lines.push(`${intro} This is blocked until the merge risk is resolved.`);
    } else {
      lines.push(`${intro} No blocking concerns from this review.`);
    }
    return lines.join('\n');
  }

  let modalOpen = false;
  async function openWrapUp() {
    if (modalOpen) return;
    modalOpen = true;
    let decisions = collectDecisions();

    // Try to fetch review data for nicer summary; fall back gracefully.
    let review = null;
    try {
      const r = await fetch(`/api/dashboard/reviews/${encodeURIComponent(REVIEW_ID)}`);
      if (r.ok) review = await r.json();
    } catch (e) {}

    if (review && Array.isArray(review.agent_results)) {
      const persisted = {};
      for (const agent of review.agent_results) {
        for (const f of (agent.findings || [])) {
          const decision = decisionFromDismissal(f.dismissal && f.dismissal.status);
          if (f.id && decision) persisted[f.id] = decision;
        }
      }
      decisions = { ...persisted, ...decisions };
      if (Object.keys(persisted).length && window.ReviewState) {
        window.ReviewState.set('decisions', decisions);
      }
    }

    const explicitFixFindings = [];
    const fallbackFixFindings = [];
    if (review && Array.isArray(review.agent_results)) {
      for (const agent of review.agent_results) {
        for (const f of (agent.findings || [])) {
          if (decisions[f.id] === 'fix') explicitFixFindings.push(f);
          else if (decisions[f.id] !== 'accept' && decisions[f.id] !== 'dismiss' && isActionableFinding(f)) {
            fallbackFixFindings.push(f);
          }
        }
      }
    }

    const intros = [
      'Solid refactor overall.',
      'Reviewed — see notes below.',
      'Looks good, with the items below to resolve.',
    ];
    const intro = intros[Math.floor(Math.random() * intros.length)];
    let verdict = 'approve';
    function fixFindingsForVerdict(nextVerdict) {
      if (explicitFixFindings.length) return explicitFixFindings;
      if (nextVerdict === 'request_changes' || nextVerdict === 'block') return fallbackFixFindings;
      return explicitFixFindings;
    }
    let mode = 'inline';
    let summary = buildSummary(decisions, fixFindingsForVerdict(verdict), verdict, intro, mode);

    const overlay = document.createElement('div');
    overlay.id = 'prg-wrap-overlay';
    overlay.style.cssText = [
      'position:fixed', 'inset:0', 'z-index:200',
      'background:rgba(2,6,23,0.78)', 'backdrop-filter:blur(6px)',
      'display:flex', 'align-items:center', 'justify-content:center',
      'font-family:system-ui, sans-serif',
    ].join(';');

    const counts = { accept: 0, fix: 0, dismiss: 0 };
    Object.values(decisions).forEach(d => { if (d in counts) counts[d]++; });
    function countsForVerdict(nextVerdict) {
      return {
        accept: counts.accept,
        fix: counts.fix || ((nextVerdict === 'request_changes' || nextVerdict === 'block') ? fallbackFixFindings.length : 0),
        dismiss: counts.dismiss,
      };
    }

    overlay.innerHTML = `
      <div style="background:rgb(15,23,42); border:1px solid rgb(51,65,85); border-radius:12px; padding:22px; width:min(640px, 92vw); max-height:90vh; overflow:auto; color:rgb(226,232,240);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
          <h2 style="margin:0; font-size:16px; font-weight:700;">Wrap up · ${REVIEW_ID}</h2>
          <button id="prg-wrap-close" style="background:transparent; border:0; color:rgb(148,163,184); cursor:pointer; font-size:18px;">×</button>
        </div>

        <div style="font-size:12px; color:rgb(148,163,184); margin-bottom:12px;">
          ✓ <span id="prg-count-accept">${counts.accept}</span> accepted (silent) &nbsp;·&nbsp;
          ✎ <span id="prg-count-fix">${counts.fix}</span> fix requested &nbsp;·&nbsp;
          — <span id="prg-count-dismiss">${counts.dismiss}</span> dismissed
        </div>

        <label style="display:block; font-size:12px; color:rgb(148,163,184); margin-bottom:4px;">Comment to author</label>
        <textarea id="prg-wrap-comment" rows="6" style="width:100%; background:rgb(2,6,23); color:rgb(226,232,240); border:1px solid rgb(51,65,85); border-radius:6px; padding:10px; font-family:ui-monospace,monospace; font-size:12.5px; resize:vertical;">${summary.replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</textarea>

        <div style="margin-top:16px; font-size:12px; color:rgb(148,163,184);">Verdict</div>
        <div id="prg-wrap-verdict" style="display:flex; gap:8px; margin-top:6px;">
          <button data-v="approve"         class="prg-wv" style="flex:1; padding:9px; border-radius:6px; border:1px solid rgb(51,65,85); background:rgba(16,185,129,0.10); color:rgb(110,231,183); cursor:pointer; font-weight:600;">✓ Approve</button>
          <button data-v="request_changes" class="prg-wv" style="flex:1; padding:9px; border-radius:6px; border:1px solid rgb(51,65,85); background:rgba(251,191,36,0.10); color:rgb(253,224,71); cursor:pointer; font-weight:600;">⟳ Request changes</button>
          <button data-v="block"           class="prg-wv" style="flex:1; padding:9px; border-radius:6px; border:1px solid rgb(51,65,85); background:rgba(248,113,113,0.10); color:rgb(252,165,165); cursor:pointer; font-weight:600;">⊘ Block</button>
        </div>

        <div style="margin-top:14px; font-size:12px; color:rgb(148,163,184);">Inline-comment mode</div>
        <div id="prg-wrap-mode" style="display:flex; gap:14px; margin-top:6px; font-size:12px;">
          <label><input type="radio" name="prg-wrap-mode" value="inline" checked> Inline (default)</label>
          <label><input type="radio" name="prg-wrap-mode" value="summary"> Summary only</label>
          <label><input type="radio" name="prg-wrap-mode" value="none"> None</label>
        </div>

        <div id="prg-wrap-error" style="display:none; margin-top:12px; padding:10px; border-radius:6px; border:1px solid rgba(248,113,113,0.4); background:rgba(248,113,113,0.08); color:rgb(252,165,165); font-size:12px;"></div>

        <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:18px;">
          <button id="prg-wrap-cancel" style="padding:9px 14px; border-radius:6px; border:1px solid rgb(51,65,85); background:transparent; color:rgb(148,163,184); cursor:pointer;">Cancel</button>
          <button id="prg-wrap-post" style="padding:9px 18px; border-radius:6px; border:0; background:rgb(99,102,241); color:white; cursor:pointer; font-weight:600;">Post →</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const verdictBtns = overlay.querySelectorAll('.prg-wv');
    const commentEl = overlay.querySelector('#prg-wrap-comment');
    let generatedSummary = summary;
    let commentDirty = false;
    commentEl.addEventListener('input', () => {
      commentDirty = commentEl.value !== generatedSummary;
    });

    function paintVerdict() {
      verdictBtns.forEach(b => {
        const isOn = b.dataset.v === verdict;
        b.style.outline = isOn ? '2px solid rgb(99,102,241)' : '';
        b.style.outlineOffset = '1px';
      });
    }
    function paintCounts() {
      const nextCounts = countsForVerdict(verdict);
      const acceptEl = overlay.querySelector('#prg-count-accept');
      const fixEl = overlay.querySelector('#prg-count-fix');
      const dismissEl = overlay.querySelector('#prg-count-dismiss');
      if (acceptEl) acceptEl.textContent = String(nextCounts.accept);
      if (fixEl) fixEl.textContent = String(nextCounts.fix);
      if (dismissEl) dismissEl.textContent = String(nextCounts.dismiss);
    }
    function setVerdict(nextVerdict) {
      verdict = nextVerdict;
      const nextSummary = buildSummary(decisions, fixFindingsForVerdict(verdict), verdict, intro, mode);
      if (!commentDirty || commentEl.value === generatedSummary) {
        commentEl.value = nextSummary;
        generatedSummary = nextSummary;
        commentDirty = false;
      }
      paintVerdict();
      paintCounts();
    }
    verdictBtns.forEach(b => b.addEventListener('click', () => { setVerdict(b.dataset.v); }));
    paintVerdict();
    paintCounts();

    overlay.querySelectorAll('input[name="prg-wrap-mode"]').forEach(input => {
      input.addEventListener('change', () => {
        mode = input.value || 'inline';
        const nextSummary = buildSummary(decisions, fixFindingsForVerdict(verdict), verdict, intro, mode);
        if (!commentDirty || commentEl.value === generatedSummary) {
          commentEl.value = nextSummary;
          generatedSummary = nextSummary;
          commentDirty = false;
        }
      });
    });

    const close = () => { overlay.remove(); modalOpen = false; };
    overlay.querySelector('#prg-wrap-close').addEventListener('click', close);
    overlay.querySelector('#prg-wrap-cancel').addEventListener('click', close);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

    overlay.querySelector('#prg-wrap-post').addEventListener('click', async () => {
      const btn = overlay.querySelector('#prg-wrap-post');
      const errEl = overlay.querySelector('#prg-wrap-error');
      btn.disabled = true;
      btn.textContent = 'Posting…';
      errEl.style.display = 'none';
      mode = (overlay.querySelector('input[name="prg-wrap-mode"]:checked') || {}).value || 'inline';
      const comment = commentEl.value;
      try {
        const r = await fetch(`/api/reviews/${encodeURIComponent(REVIEW_ID)}/finalize`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            decisions, comment_to_author: comment, verdict, comment_mode: mode,
          }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok || data.posted === false) {
          throw new Error(data.error || data.detail || `HTTP ${r.status}`);
        }
        window.ReviewState.merge({ finalized: true, verdict, posted_at: new Date().toISOString() });
        if (data.next_id) {
          window.location.href = `/reviews/${encodeURIComponent(data.next_id)}`;
        } else {
          window.location.href = '/reviews';
        }
      } catch (exc) {
        errEl.textContent = String(exc.message || exc);
        errEl.style.display = '';
        btn.disabled = false;
        btn.textContent = 'Post →';
      }
    });
  }

  function mountFloating() {
    if (document.getElementById('prg-viewer-floater')) return;
    const wrap = document.createElement('div');
    wrap.id = 'prg-viewer-floater';
    wrap.style.cssText = [
      'position:fixed', 'top:12px', 'right:12px', 'z-index:50',
      'display:inline-flex', 'gap:2px', 'padding:3px',
      'border-radius:8px', 'background:rgba(15,23,42,0.85)',
      'border:1px solid rgba(71,85,105,0.5)', 'backdrop-filter:blur(6px)',
      'font-family:system-ui, sans-serif', 'font-size:12px',
    ].join(';');
    for (const m of MODES) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = m.label;
      const active = m.key === MODE;
      btn.style.cssText = [
        'cursor:' + (active ? 'default' : 'pointer'),
        'padding:5px 10px', 'border-radius:6px', 'border:0',
        'background:' + (active ? 'rgba(99,102,241,0.22)' : 'transparent'),
        'color:' + (active ? 'rgb(199,210,254)' : 'rgb(148,163,184)'),
        'font-weight:' + (active ? '600' : '500'),
      ].join(';');
      btn.addEventListener('click', () => navTo(m.key));
      wrap.appendChild(btn);
    }
    document.body.appendChild(wrap);
  }

  // -------------------------------------------------------------------------
  // Keyboard shortcuts (1/2/3)
  // -------------------------------------------------------------------------
  document.addEventListener('keydown', (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const tag = (ev.target && ev.target.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || (ev.target && ev.target.isContentEditable)) return;
    if (ev.key === '1') { ev.preventDefault(); navTo('wizard'); }
    else if (ev.key === '2') { ev.preventDefault(); navTo('chapters'); }
    else if (ev.key === '3') { ev.preventDefault(); navTo('findings'); }
  });

  // -------------------------------------------------------------------------
  // window.ReviewState — per-review key-value store, shared across modes
  // -------------------------------------------------------------------------
  const STORE_KEY = `prg:review:${REVIEW_ID}`;
  function load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); }
    catch (e) { return {}; }
  }
  function save(obj) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(obj)); } catch (e) {}
  }
  window.ReviewState = {
    reviewId: REVIEW_ID,
    mode: MODE,
    get(key, fallback) {
      const v = load()[key];
      return v === undefined ? fallback : v;
    },
    set(key, value) {
      const s = load();
      s[key] = value;
      save(s);
    },
    merge(partial) {
      const s = load();
      Object.assign(s, partial || {});
      save(s);
    },
    all() { return load(); },
    clear() { try { localStorage.removeItem(STORE_KEY); } catch (e) {} },
  };

  // -------------------------------------------------------------------------
  // Boot
  // -------------------------------------------------------------------------
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectSwitcher);
  } else {
    injectSwitcher();
  }
})();
