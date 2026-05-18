/**
 * snippet.js — shared hunk renderer for review_detail and human_wizard.
 *
 * Exports:
 *   fetchSnippet(reviewId, path, line, context=3) → Promise<hunkData|null>
 *   renderSnippet(container, hunkData)            → void
 *
 * renderSnippet uses the .hunk CSS class defined in human_wizard.html.
 * It is DOM-only — no framework dependency.
 * On failure (404, empty hunk, network error) it renders a muted fallback
 * line rather than throwing.
 */

'use strict';

/**
 * Fetch a diff hunk from the dashboard API.
 * Returns the parsed JSON body on success, null on any failure.
 */
async function fetchSnippet(reviewId, path, line, context = 3) {
  try {
    const url = `/api/dashboard/reviews/${encodeURIComponent(reviewId)}/diff`
      + `?path=${encodeURIComponent(path)}&line=${encodeURIComponent(line)}&context=${encodeURIComponent(context)}`;
    const resp = await fetch(url);
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}

/**
 * Render a hunk into container using .hunk / .hunk-header / .row CSS primitives.
 * If hunkData is null or has no lines, appends a muted "snippet unavailable" line.
 * Calling again on a container that already has a .hunk removes it (toggle).
 */
function renderSnippet(container, hunkData) {
  const existing = container.querySelector('.hunk');
  if (existing) {
    existing.remove();
    return;
  }

  const lines = hunkData && Array.isArray(hunkData.lines) ? hunkData.lines : [];

  if (!lines.length) {
    const msg = document.createElement('div');
    msg.className = 'text-xs text-slate-500 mt-2 ml-1';
    msg.textContent = 'snippet unavailable';
    msg.dataset.snippetFallback = '1';
    container.appendChild(msg);
    return;
  }

  const hunk = document.createElement('div');
  hunk.className = 'hunk';

  if (hunkData.file) {
    const header = document.createElement('div');
    header.className = 'hunk-header';
    header.textContent = hunkData.file + (hunkData.line ? `:${hunkData.line}` : '');
    hunk.appendChild(header);
  }

  const pre = document.createElement('pre');

  for (const ln of lines) {
    const row = document.createElement('div');
    row.className = `row ${ln.type}`;

    const lineNum = document.createElement('span');
    lineNum.className = 'ln';
    lineNum.textContent = ln.ln != null ? String(ln.ln) : '';

    const marker = document.createElement('span');
    marker.className = 'marker';
    marker.textContent = ln.marker === '+' ? '+' : ln.marker === '-' ? '-' : ' ';

    const content = document.createElement('span');
    content.className = 'content';
    content.textContent = ln.content;

    row.appendChild(lineNum);
    row.appendChild(marker);
    row.appendChild(content);
    pre.appendChild(row);
  }

  hunk.appendChild(pre);
  container.appendChild(hunk);
}
