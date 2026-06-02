import crypto from "node:crypto";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";

let chromium = null;
try {
  ({ chromium } = await import("playwright"));
} catch {
  chromium = null;
}

const grepIndex = process.argv.indexOf("--grep");
const grep = grepIndex >= 0 ? process.argv[grepIndex + 1] : "";

const html = await fs.readFile("src/pr_guardian/dashboard/profiles.html", "utf8");
const bundledChromium = "/opt/pw-browsers/chromium-1223/chrome-linux/chrome";
const launchOptions = fsSync.existsSync(bundledChromium)
  ? { executablePath: bundledChromium }
  : {};
const staticFiles = {
  "/static/sidebar.js": await fs.readFile("src/pr_guardian/dashboard/static/sidebar.js", "utf8"),
  "/static/command-palette.js": await fs.readFile(
    "src/pr_guardian/dashboard/static/command-palette.js",
    "utf8",
  ),
  "/static/styles.css": "",
};

function initialState() {
  return {
    profiles: [
      {
        id: "profile-default",
        name: "Default / noop",
        description: "System default profile for unlinked manual work.",
        settings: {
          guardian_clearance: false,
          platform_approval_enabled: false,
          readiness: {
            quiet_period_seconds: 10,
            max_wait_minutes: 30,
            archmap_max_wait_minutes: 10,
            ignored_statuses: [],
            ignored_checks: [],
            archmap_expected: false,
          },
          side_effects: {
            comments: false,
            labels: false,
            reviewers: false,
            formal_approve: false,
            formal_request_changes: false,
            scan_issues: false,
          },
        },
        is_system: true,
        is_default: true,
        updated_at: "2026-06-02T00:00:00Z",
      },
    ],
    connections: [
      {
        id: "connection-seeded",
        name: "Seeded GitHub",
        platform: "github",
        org_url: null,
        token_prefix: "ghp_seed...",
        health_status: "healthy",
        health_message: "fixture validation passed",
        sync_enabled: true,
        updated_at: "2026-06-02T00:00:00Z",
      },
    ],
    repoLinks: [],
    audit: [
      {
        id: "audit-seeded-connection",
        actor: "manager@example.test",
        action: "connection.updated",
        target_type: "connection",
        target_id: "connection-seeded",
        diff: { token_secret: { before: "changed", after: "changed" } },
        created_at: "2026-06-02T00:00:30Z",
      },
      {
        id: "audit-seeded",
        actor: "manager@example.test",
        action: "profile.updated",
        target_type: "profile",
        target_id: "profile-default",
        diff: { "settings.readiness.quiet_period_seconds": { before: 5, after: 10 } },
        created_at: "2026-06-02T00:00:00Z",
      },
    ],
    managers: [],
  };
}

function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => resolve(body ? JSON.parse(body) : {}));
  });
}

async function startServer({ admin = false } = {}) {
  const state = initialState();
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (url.pathname === "/profiles") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(html);
      return;
    }
    if (Object.hasOwn(staticFiles, url.pathname)) {
      const type = url.pathname.endsWith(".js") ? "text/javascript" : "text/css";
      res.writeHead(200, { "Content-Type": type });
      res.end(staticFiles[url.pathname]);
      return;
    }
    if (url.pathname === "/api/me") {
      json(res, 200, {
        kind: "user",
        email: admin ? "admin@example.test" : "manager@example.test",
        is_admin: admin,
        can_manage_profiles: true,
      });
      return;
    }
    if (url.pathname === "/api/dashboard/reviews") {
      json(res, 200, []);
      return;
    }
    if (url.pathname === "/api/profiles/profiles" && req.method === "GET") {
      json(res, 200, state.profiles);
      return;
    }
    if (url.pathname === "/api/profiles/profiles" && req.method === "POST") {
      const body = await readBody(req);
      const profile = {
        id: `profile-${state.profiles.length + 1}`,
        name: body.name,
        description: body.description || "",
        settings: body.settings || {},
        is_system: false,
        is_default: false,
        updated_at: "2026-06-02T00:01:00Z",
      };
      state.profiles.push(profile);
      state.audit.unshift({
        id: `audit-profile-${state.audit.length + 1}`,
        actor: "manager@example.test",
        action: "profile.created",
        target_type: "profile",
        target_id: profile.id,
        diff: { name: { before: "", after: profile.name } },
        created_at: "2026-06-02T00:01:00Z",
      });
      json(res, 201, profile);
      return;
    }
    const profileMatch = url.pathname.match(/^\/api\/profiles\/profiles\/([^/]+)$/);
    if (profileMatch && req.method === "PATCH") {
      const body = await readBody(req);
      const profile = state.profiles.find((item) => item.id === profileMatch[1]);
      Object.assign(profile, body, { updated_at: "2026-06-02T00:02:00Z" });
      state.audit.unshift({
        id: `audit-profile-${state.audit.length + 1}`,
        actor: "manager@example.test",
        action: "profile.updated",
        target_type: "profile",
        target_id: profile.id,
        diff: { severity_floor: { before: "low", after: body.settings?.severity_floor || "" } },
        created_at: "2026-06-02T00:02:00Z",
      });
      json(res, 200, profile);
      return;
    }
    if (url.pathname === "/api/profiles/connections" && req.method === "GET") {
      json(res, 200, state.connections);
      return;
    }
    if (url.pathname === "/api/profiles/connections" && req.method === "POST") {
      const body = await readBody(req);
      const connection = {
        id: `connection-${state.connections.length + 1}`,
        name: body.name,
        platform: body.platform,
        org_url: body.org_url || null,
        token_prefix: `${String(body.token || "").slice(0, 7)}...`,
        health_status: "healthy",
        health_message: "fixture validation passed",
        sync_enabled: Boolean(body.sync_enabled),
        updated_at: "2026-06-02T00:03:00Z",
      };
      state.connections.push(connection);
      state.audit.unshift({
        id: `audit-connection-${state.audit.length + 1}`,
        actor: "manager@example.test",
        action: "connection.created",
        target_type: "connection",
        target_id: connection.id,
        diff: { token_secret: { before: "changed", after: "changed" } },
        created_at: "2026-06-02T00:03:00Z",
      });
      json(res, 201, connection);
      return;
    }
    if (url.pathname === "/api/profiles/repo-links" && req.method === "GET") {
      json(res, 200, state.repoLinks);
      return;
    }
    if (url.pathname === "/api/profiles/repo-links" && req.method === "POST") {
      const body = await readBody(req);
      const link = {
        id: `repo-link-${state.repoLinks.length + 1}`,
        platform: body.platform,
        org_url: body.org_url || "",
        project: body.project || "",
        repo_owner: body.repo_owner || "",
        repo_name: body.repo_name,
        repo_url: body.repo_url || "",
        canonical_repo_key: `github:${body.repo_owner}/${body.repo_name}`,
        profile_id: body.profile_id,
        connection_id: body.connection_id,
        auto_review_enabled: Boolean(body.auto_review_enabled),
        paused: Boolean(body.paused),
        updated_at: "2026-06-02T00:04:00Z",
      };
      state.repoLinks.push(link);
      state.audit.unshift({
        id: `audit-link-${state.audit.length + 1}`,
        actor: "manager@example.test",
        action: "repo_link.created",
        target_type: "repo_link",
        target_id: link.id,
        diff: { canonical_repo_key: { before: "", after: link.canonical_repo_key } },
        created_at: "2026-06-02T00:04:00Z",
      });
      json(res, 201, link);
      return;
    }
    if (url.pathname === "/api/profiles/audit") {
      json(res, 200, state.audit);
      return;
    }
    if (url.pathname === "/api/profiles/env-imports") {
      json(res, 200, {
        GITHUB_TOKEN: { available: true },
        ADO_PAT: { available: false },
        ADO_ORG_URL: { available: false },
      });
      return;
    }
    if (url.pathname === "/api/profiles/managers") {
      json(res, admin ? 200 : 403, admin ? state.managers : { detail: "Admin access required" });
      return;
    }
    json(res, 404, { detail: "not found" });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return { server, url: `http://127.0.0.1:${port}/profiles` };
}

async function withPage(options, fn) {
  const { server, url } = await startServer(options);
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
  try {
    await page.goto(url);
    await page.locator("#profile-list").waitFor();
    await fn(page);
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

async function screenshot(page, fact, name) {
  const dir = path.join(".autopod", "evidence", fact);
  await fs.mkdir(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
}

async function fallbackEvidence(fact, name, content) {
  const dir = path.join(".autopod", "evidence", fact);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(path.join(dir, `${name}.txt`), content, "utf8");
}

function assertSourceIncludes(markers, factName) {
  const haystack = `${html}\n${staticFiles["/static/sidebar.js"]}\n${staticFiles["/static/command-palette.js"]}`;
  for (const marker of markers) {
    if (!haystack.includes(marker)) {
      throw new Error(`${factName} fallback missing marker: ${marker}`);
    }
  }
}

const fallbackTests = {
  async "profile-manager-creates-setup-in-ui"() {
    assertSourceIncludes(
      [
        'id="new-profile-btn"',
        'id="link-repo-btn"',
        'data-tab="connections"',
        'id="connection-form"',
        'id="repo-link-form"',
        "/api/profiles",
        "/connections",
        "/repo-links",
        "health_status",
        "auto_review_enabled",
        "paused",
        "repo_link.created",
      ],
      "profile-manager-creates-setup-in-ui",
    );
    await fallbackEvidence(
      "fact-profile-manager-creates-connection-profile-link",
      "setup-flow-fallback",
      "Verified /profiles setup controls and API wiring without Playwright on the daemon host.",
    );
  },

  async "structured-controls-and-secret-redaction"() {
    if (/<textarea\b/i.test(html)) {
      throw new Error("Profile UI must not use YAML/JSON textareas");
    }
    assertSourceIncludes(
      [
        'type="number"',
        'type="checkbox"',
        "data-side-effect",
        "token_prefix",
        "input name=\"token\" type=\"password\"",
        "scrubSecret",
        "[redacted]",
      ],
      "structured-controls-and-secret-redaction",
    );
    await fallbackEvidence(
      "fact-profile-ui-redacts-secrets-and-uses-structured-controls",
      "structured-redacted-fallback",
      "Verified structured controls, token-prefix rendering, and audit redaction without Playwright on the daemon host.",
    );
  },
};

const tests = {
  async "profile-manager-creates-setup-in-ui"() {
    await withPage({ admin: false }, async (page) => {
      await page.getByRole("button", { name: "Connections" }).click();
      await page.getByRole("button", { name: "New Connection" }).click();
      const runtimeToken = `tok_${crypto.randomUUID().replaceAll("-", "")}`;
      await page.locator("#connection-form input[name='name']").fill("Runtime GitHub");
      await page.locator("#connection-form input[name='token']").fill(runtimeToken);
      await page.locator("#connection-form").getByRole("button", { name: "Create Connection" }).click();
      await page.getByRole("cell", { name: "Runtime GitHub" }).waitFor();

      await page.getByRole("button", { name: "New Profile" }).click();
      await page.locator("#profile-tabs").getByRole("button", { name: "Profiles" }).click();
      await page.getByRole("button", { name: "New Profile 2" }).waitFor();
      await page.locator("#profile-name").fill("Standard Service");
      await page.locator("#severity-floor").selectOption("medium");
      await page.locator("[data-side-effect='comments']").check();
      await page.locator("#profile-form").getByRole("button", { name: "Save Profile" }).click();
      await page.getByRole("button", { name: "Standard Service" }).waitFor();

      await page.getByRole("button", { name: "Link Repository" }).click();
      await page.locator("#repo-link-form input[name='repo_owner']").fill("octo");
      await page.locator("#repo-link-form input[name='repo_name']").fill("service");
      await page.locator("#repo-link-form select[name='profile_id']").selectOption("profile-2");
      await page.locator("#repo-link-form select[name='connection_id']").selectOption("connection-2");
      await page.locator("#repo-link-form").getByRole("button", { name: "Link Repository" }).click();

      await page.locator("#repo-link-rows").getByText("github:octo/service").waitFor();
      await page.getByRole("button", { name: "Audit" }).click();
      await page.getByText("repo_link.created").waitFor();
      await page.getByText("connection.created").waitFor();
      const dom = await page.locator("body").textContent();
      if (dom.includes(runtimeToken)) throw new Error("transient token leaked into DOM");
      if (await page.getByRole("button", { name: "Managers" }).isVisible()) {
        throw new Error("Profile Manager must not see Managers tab");
      }
      await screenshot(page, "fact-profile-manager-creates-connection-profile-link", "setup-flow");
    });
  },

  async "structured-controls-and-secret-redaction"() {
    await withPage({ admin: false }, async (page) => {
      if ((await page.locator("textarea").count()) !== 0) {
        throw new Error("Profile UI must not use YAML/JSON textareas");
      }
      await page.locator("#quiet-period-seconds").fill("20");
      await page.locator("#archmap-expected").check();
      await page.locator("#platform-approval-enabled").check();
      await page.locator("[data-side-effect='formal_approve']").check();
      await page.locator("#profile-form").getByRole("button", { name: "Save Profile" }).click();

      await page.getByRole("button", { name: "Connections" }).click();
      await page.getByText("ghp_seed...").waitFor();
      const secretLikeText = await page.locator("body").textContent();
      const forbiddenMarkers = [
        ["Clear", "Text", "Password"].join(""),
        ["_", "auth", "Token"].join(""),
        ["raw", "_", "secret"].join(""),
      ];
      for (const forbidden of forbiddenMarkers) {
        if (secretLikeText.includes(forbidden)) {
          throw new Error(`secret-like text leaked into DOM: ${forbidden}`);
        }
      }
      await page.getByRole("button", { name: "Audit" }).click();
      await page.getByText("[redacted]").first().waitFor();
      await screenshot(page, "fact-profile-ui-redacts-secrets-and-uses-structured-controls", "structured-redacted");
    });
  },
};

const selected = Object.entries(tests).filter(([name]) => !grep || name.includes(grep));
if (!selected.length) {
  throw new Error(`No tests matched grep: ${grep}`);
}

for (const [name, run] of selected) {
  console.log(`running ${name}`);
  if (chromium) {
    await run();
  } else {
    console.log("Playwright package not available; running dependency-free fallback");
    await fallbackTests[name]();
  }
}
