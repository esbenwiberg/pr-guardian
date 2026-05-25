from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest


def test_review_detail_renders_quote_strip_and_skipped_architecture(tmp_path):
    probe = subprocess.run(
        [
            "node",
            "-e",
            "const { chromium } = require('playwright');"
            "chromium.launch({headless:true}).then(b=>b.close()).catch(e=>{console.error(e.message); process.exit(1);})",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=10,
    )
    if probe.returncode != 0:
        pytest.skip("Playwright Chromium is not installed in this environment")

    evidence_dir = Path(os.environ.get("PR_GUARDIAN_BROWSER_EVIDENCE", "test-results/quote-status"))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = evidence_dir / "review-detail-quote-status.png"
    log_path = evidence_dir / "review-detail-quote-status.log"

    script = tmp_path / "review_detail_quote_status.mjs"
    script.write_text(
        textwrap.dedent(
            f"""
            import http from 'node:http';
            import fs from 'node:fs/promises';
            import path from 'node:path';
            import {{ chromium }} from 'playwright';

            const root = process.cwd();
            const reviewId = '00000000-0000-4000-8000-000000000042';
            const review = {{
              id: reviewId,
              pr_id: '42',
              repo: 'org/repo',
              platform: 'github',
              author: 'dev',
              title: 'Wire auth guard',
              decision: 'human_review',
              risk_tier: 'medium',
              combined_score: 0.72,
              summary: 'Review summary',
              started_at: new Date().toISOString(),
              finished_at: new Date().toISOString(),
              duration_ms: 1200,
              cost_usd: 0.01,
              mechanical_results: [],
              sticky_triggers: [],
              finding_reasons: [],
              dismissal_count: 0,
              prior_dismissals: [],
              agent_results: [
                {{
                  agent_name: 'security_privacy',
                  verdict: 'warn',
                  status: 'ran',
                  status_reason: null,
                  findings: [{{
                    id: 'finding-1',
                    severity: 'high',
                    certainty: 'detected',
                    category: 'authorization',
                    language: 'python',
                    file: 'src/auth.py',
                    line: 17,
                    description: 'The new guard can be bypassed.',
                    quote: 'return user.is_admin or allow_all',
                    suggestion: 'Remove the allow_all path.',
                    cwe: 'CWE-863',
                    triage: 'decision'
                  }}]
                }},
                {{
                  agent_name: 'architecture',
                  verdict: 'pass',
                  status: 'skipped',
                  status_reason: 'no architecture context found',
                  findings: []
                }}
              ]
            }};

            const server = http.createServer(async (req, res) => {{
              const url = new URL(req.url, 'http://127.0.0.1');
              if (url.pathname === `/api/dashboard/reviews/${{reviewId}}`) {{
                res.writeHead(200, {{ 'content-type': 'application/json' }});
                res.end(JSON.stringify(review));
                return;
              }}
              if (url.pathname === `/reviews/${{reviewId}}`) {{
                const html = await fs.readFile(path.join(root, 'src/pr_guardian/dashboard/review_detail.html'), 'utf8');
                res.writeHead(200, {{ 'content-type': 'text/html' }});
                res.end(html);
                return;
              }}
              if (url.pathname.startsWith('/static/')) {{
                const filePath = path.join(root, 'src/pr_guardian/dashboard/static', url.pathname.slice('/static/'.length));
                try {{
                  const body = await fs.readFile(filePath);
                  const type = filePath.endsWith('.css') ? 'text/css' : 'application/javascript';
                  res.writeHead(200, {{ 'content-type': type }});
                  res.end(body);
                }} catch {{
                  res.writeHead(404);
                  res.end('missing static asset');
                }}
                return;
              }}
              res.writeHead(404);
              res.end('not found');
            }});

            await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
            const port = server.address().port;
            let browser;
            try {{
              browser = await chromium.launch({{ headless: true }});
              const page = await browser.newPage({{ viewport: {{ width: 1280, height: 900 }} }});
              const messages = [];
              page.on('console', msg => messages.push(`${{msg.type()}}: ${{msg.text()}}`));
              await page.goto(`http://127.0.0.1:${{port}}/reviews/${{reviewId}}`, {{ waitUntil: 'networkidle' }});
              await page.getByText('return user.is_admin or allow_all').waitFor({{ timeout: 5000 }});
              await page.getByText('Architecture skipped - no architecture context found').waitFor({{ timeout: 5000 }});
              const quoteFont = await page.locator('[data-quote-strip]').first().evaluate(el => getComputedStyle(el).fontFamily);
              if (!/mono|Consolas|Menlo/i.test(quoteFont)) {{
                throw new Error(`quote strip is not monospaced: ${{quoteFont}}`);
              }}
              await page.screenshot({{ path: {str(screenshot_path)!r}, fullPage: true }});
              await fs.writeFile({str(log_path)!r}, messages.join('\\n') || 'no browser console messages\\n');
            }} finally {{
              if (browser) await browser.close();
              server.close();
            }}
            """
        )
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert screenshot_path.exists()
    assert log_path.exists()
