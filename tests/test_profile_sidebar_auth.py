from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from fastapi.testclient import TestClient

from pr_guardian.auth.identity import Identity
from pr_guardian.main import app


SIDEBAR_JS = Path("src/pr_guardian/dashboard/static/sidebar.js")
COMMAND_PALETTE_JS = Path("src/pr_guardian/dashboard/static/command-palette.js")


def _identity(*, admin: bool = False, manager: bool = False) -> Identity:
    return Identity(
        kind="user",
        email="masked@example.com",
        is_admin=admin,
        can_manage_profiles=manager,
    )


def test_sidebar_shows_profiles_for_managers_and_settings_for_admins():
    sidebar = SIDEBAR_JS.read_text()
    palette = COMMAND_PALETTE_JS.read_text()

    assert "NAV_PROFILES" in sidebar
    assert "canManageProfiles ? navItem(NAV_PROFILES)" in sidebar
    assert "isAdmin ? navItem(NAV_ADMIN)" in sidebar
    assert "requiresProfiles: true" in palette
    assert "requiresAdmin: true" in palette
    assert "currentUser.is_admin || currentUser.can_manage_profiles" in palette

    with (
        patch("pr_guardian.auth.identity._db_available", return_value=True),
        patch(
            "pr_guardian.auth.identity.IdentityMiddleware._resolve",
            return_value=_identity(admin=True),
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        me = client.get("/api/me").json()
        assert me["is_admin"] is True
        assert me["can_manage_profiles"] is True
        assert client.get("/profiles").status_code == 200
        assert client.get("/settings").status_code == 200

    with (
        patch("pr_guardian.auth.identity._db_available", return_value=True),
        patch(
            "pr_guardian.auth.identity.IdentityMiddleware._resolve",
            return_value=_identity(manager=True),
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        me = client.get("/api/me").json()
        assert me["is_admin"] is False
        assert me["can_manage_profiles"] is True
        assert client.get("/profiles").status_code == 200
        settings = client.get("/settings", follow_redirects=False)
        assert settings.status_code == 302
        assert settings.headers["location"] == "/reviews?error=admin_required"

    _assert_client_nav_visibility()


def _assert_client_nav_visibility() -> None:
    script = f"""
const fs = require('node:fs');
const http = require('node:http');
const {{ chromium }} = require('playwright');

const sidebar = fs.readFileSync({json.dumps(str(SIDEBAR_JS))}, 'utf8');
const palette = fs.readFileSync({json.dumps(str(COMMAND_PALETTE_JS))}, 'utf8');
const chromiumPath = '/opt/pw-browsers/chromium-1223/chrome-linux/chrome';
const launchOptions = fs.existsSync(chromiumPath) ? {{ executablePath: chromiumPath }} : {{}};

const roles = {{
  admin: {{ is_admin: true, can_manage_profiles: true }},
  manager: {{ is_admin: false, can_manage_profiles: true }},
  ordinary: {{ is_admin: false, can_manage_profiles: false }},
}};
let currentRole = 'ordinary';

function pageHtml(role) {{
  return `<!doctype html>
    <html><body>
      <aside id="sidebar"></aside>
      <script>window.__currentUser = ${{JSON.stringify(roles[role])}};<\\/script>
      <script src="/static/sidebar.js"><\\/script>
      <script src="/static/command-palette.js"><\\/script>
    </body></html>`;
}}

function json(res, status, body) {{
  res.writeHead(status, {{ 'Content-Type': 'application/json' }});
  res.end(JSON.stringify(body));
}}

const server = http.createServer((req, res) => {{
  const url = new URL(req.url, 'http://127.0.0.1');
  const role = url.searchParams.get('role') || 'ordinary';
  if (url.pathname === '/') {{
    currentRole = role;
    res.writeHead(200, {{ 'Content-Type': 'text/html' }});
    res.end(pageHtml(role));
  }} else if (url.pathname === '/static/sidebar.js') {{
    res.writeHead(200, {{ 'Content-Type': 'text/javascript' }});
    res.end(sidebar);
  }} else if (url.pathname === '/static/command-palette.js') {{
    res.writeHead(200, {{ 'Content-Type': 'text/javascript' }});
    res.end(palette);
  }} else if (url.pathname === '/api/me') {{
    json(res, 200, roles[currentRole]);
  }} else if (url.pathname === '/api/dashboard/reviews') {{
    json(res, 200, []);
  }} else {{
    json(res, 404, {{ detail: 'not found' }});
  }}
}});

(async () => {{
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({{ viewport: {{ width: 1280, height: 800 }} }});
  try {{
    for (const [role, expected] of Object.entries({{
      admin: {{ profiles: true, settings: true }},
      manager: {{ profiles: true, settings: false }},
      ordinary: {{ profiles: false, settings: false }},
    }})) {{
      await page.goto(`http://127.0.0.1:${{port}}/?role=${{role}}`);
      await page.locator('#sidebar .sidebar-nav').waitFor();
      const sidebarText = await page.locator('#sidebar').textContent();
      if (sidebarText.includes('Profiles') !== expected.profiles) {{
        throw new Error(`${{role}} sidebar Profiles visibility mismatch: ${{sidebarText}}`);
      }}
      if (sidebarText.includes('Settings') !== expected.settings) {{
        throw new Error(`${{role}} sidebar Settings visibility mismatch: ${{sidebarText}}`);
      }}
      await page.evaluate(() => window.__cmdPalette.open());
      const paletteText = await page.locator('#cmd-palette').textContent();
      if (paletteText.includes('Profiles') !== expected.profiles) {{
        throw new Error(`${{role}} palette Profiles visibility mismatch: ${{paletteText}}`);
      }}
      if (paletteText.includes('Settings') !== expected.settings) {{
        throw new Error(`${{role}} palette Settings visibility mismatch: ${{paletteText}}`);
      }}
      await page.evaluate(() => window.__cmdPalette.close());
    }}
  }} finally {{
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }}
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""
    subprocess.run(["node", "-e", script], cwd=Path.cwd(), check=True)

    with (
        patch("pr_guardian.auth.identity._db_available", return_value=True),
        patch(
            "pr_guardian.auth.identity.IdentityMiddleware._resolve",
            return_value=_identity(),
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        me = client.get("/api/me").json()
        assert me["is_admin"] is False
        assert me["can_manage_profiles"] is False
        profiles = client.get("/profiles", follow_redirects=False)
        assert profiles.status_code == 302
        assert profiles.headers["location"] == "/reviews?error=profile_manager_required"
        settings = client.get("/settings", follow_redirects=False)
        assert settings.status_code == 302
        assert settings.headers["location"] == "/reviews?error=admin_required"
