from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

import pytest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _playwright_available() -> bool:
    check = subprocess.run(
        ["node", "-e", "require('playwright');"],
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return check.returncode == 0


def test_review_detail_renders_quote_strip_and_skipped_architecture(tmp_path):
    if not _playwright_available():
        pytest.skip("node playwright dependency is not installed")

    repo = Path(__file__).resolve().parents[2]
    review_id = str(uuid.uuid4())
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    screenshot_path = repo / "test-results" / "review-detail-quote-status.png"
    screenshot_path.parent.mkdir(exist_ok=True)
    chromium_executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if not chromium_executable:
        candidates = sorted(Path("/opt/pw-browsers").glob("chromium_headless_shell-*/chrome-linux/headless_shell"))
        chromium_executable = str(candidates[-1]) if candidates else ""

    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src")
    env.pop("DATABASE_URL", None)
    env.pop("GUARDIAN_DB_ENABLED", None)
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "pr_guardian.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while True:
            try:
                with urlopen(f"{base_url}/reviews/{review_id}", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(0.2)

        review_payload = {
            "id": review_id,
            "pr_id": "42",
            "repo": "org/repo",
            "platform": "github",
            "author": "dev",
            "title": "Add auth fast path",
            "source_branch": "feature",
            "target_branch": "main",
            "head_commit_sha": "abc123",
            "pr_url": "https://github.com/org/repo/pull/42",
            "risk_tier": "medium",
            "repo_risk_class": "standard",
            "combined_score": 42,
            "decision": "human_review",
            "summary": "Review summary.",
            "mechanical_results": [],
            "agent_results": [
                {
                    "agent_name": "architecture",
                    "verdict": "pass",
                    "status": "skipped",
                    "status_reason": "no architecture context found",
                    "findings": [],
                },
                {
                    "agent_name": "intent",
                    "verdict": "flag_human",
                    "status": "ran",
                    "status_reason": None,
                    "findings": [
                        {
                            "id": str(uuid.uuid4()),
                            "severity": "medium",
                            "certainty": "suspected",
                            "category": "scope-opacity",
                            "language": "text",
                            "file": "",
                            "line": None,
                            "description": "PR intent lacks a specific auth anchor.",
                            "quote": "PR description: fixes auth stuff",
                        }
                    ],
                },
            ],
            "sticky_triggers": [],
            "finding_reasons": [],
            "triage_counts": {"noise": 0, "fyi": 1, "decision": 0},
            "dismissal_count": 0,
            "prior_dismissals": [],
        }
        script = textwrap.dedent(
            f"""
            const {{ chromium }} = require('playwright');
            (async () => {{
              const launchOptions = {{ headless: true }};
              if ({json.dumps(chromium_executable)}) launchOptions.executablePath = {json.dumps(chromium_executable)};
              const browser = await chromium.launch(launchOptions);
              const page = await browser.newPage({{ viewport: {{ width: 1280, height: 900 }} }});
              await page.route('**/api/dashboard/reviews/{review_id}', route => route.fulfill({{
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({json.dumps(review_payload)}),
              }}));
              await page.goto('{base_url}/reviews/{review_id}', {{ waitUntil: 'networkidle' }});
              await page.getByText('Architecture skipped - no architecture context found').waitFor();
              await page.locator('[data-quote-strip]').filter({{ hasText: 'PR description: fixes auth stuff' }}).waitFor();
              await page.screenshot({{ path: {json.dumps(str(screenshot_path))}, fullPage: true }});
              await browser.close();
            }})().catch(err => {{
              console.error(err);
              process.exit(1);
            }});
            """
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=repo,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert screenshot_path.exists()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
