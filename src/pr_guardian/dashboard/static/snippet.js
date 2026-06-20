'use strict';

// fetchSnippet(reviewId, path, line, context=3) → Promise<hunkData|null>
// renderSnippet(container, hunkData) — DOM-only, reuses .hunk CSS, never throws.
async function fetchSnippet(reviewId, path, line, context = 3) {
  try {
    const url = `/api/dashboard/reviews/${encodeURIComponent(reviewId)}/diff`
      + `?path=${encodeURIComponent(path)}&line=${encodeURIComponent(line)}&context=${encodeURIComponent(context)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      let detail = `Couldn't load the code snippet (HTTP ${resp.status}).`;
      try {
        const body = await resp.json();
        if (body && body.detail) detail = body.detail;
      } catch { /* non-JSON error body */ }
      return { error: detail };
    }
    return await resp.json();
  } catch {
    return { error: 'Network error while loading the code snippet.' };
  }
}

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
    msg.textContent = (hunkData && hunkData.error)
      ? hunkData.error
      : 'No code is available for this line in the PR diff.';
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
    marker.textContent = ln.marker;

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
