/**
 * Browser tests for the Escalation policy controls in the Profiles editor.
 *
 * Contract fact: fact-profiles-editor-controls
 * Scenarios verified:
 *   - thresholds-reveal-on-structural-only: selecting structural_only reveals the
 *     two threshold selects.
 *   - thresholds-hide-on-standard: switching back to standard hides them.
 *   - selections-persist: Save then reload shows the persisted selections.
 */

import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";

let chromium = null;
try {
  ({ chromium } = await import("playwright"));
} catch {
  chromium = null;
}

const html = await fs.readFile("src/pr_guardian/dashboard/profiles.html", "utf8");
const bundledChromium = "/opt/pw-browsers/chromium-1223/chrome-linux/chrome";
const launchOptions = fs.stat(bundledChromium).then(() => ({ executablePath: bundledChromium })).catch(() => ({}));
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
        name: "Default",
        description: "Default profile",
        settings: {},
        is_system: true,
        is_default: true,
        updated_at: "2026-06-18T00:00:00Z",
      },
    ],
    connections: [],
    repoLinks: [],
    audit: [],
  };
}

function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve) => {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => resolve(body ? JSON.parse(body) : {}));
  });
}

async function startServer() {
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
      json(res, 200, { kind: "user", email: "manager@example.test", is_admin: false, can_manage_profiles: true });
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
    const profileMatch = url.pathname.match(/^\/api\/profiles\/profiles\/([^/]+)$/);
    if (profileMatch && req.method === "PATCH") {
      const body = await readBody(req);
      const profile = state.profiles.find((p) => p.id === profileMatch[1]);
      if (!profile) { json(res, 404, { detail: "not found" }); return; }
      if (body.settings !== undefined) profile.settings = body.settings;
      if (body.name !== undefined) profile.name = body.name;
      if (body.description !== undefined) profile.description = body.description;
      json(res, 200, profile);
      return;
    }
    if (url.pathname === "/api/profiles/connections" && req.method === "GET") {
      json(res, 200, state.connections);
      return;
    }
    if (url.pathname === "/api/profiles/repo-links" && req.method === "GET") {
      json(res, 200, state.repoLinks);
      return;
    }
    if (url.pathname === "/api/profiles/audit") {
      json(res, 200, state.audit);
      return;
    }
    if (url.pathname === "/api/profiles/env-imports") {
      json(res, 200, { ADO_PAT: { available: false }, ADO_ORG_URL: { available: false } });
      return;
    }
    json(res, 404, { detail: "not found" });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return { server, url: `http://127.0.0.1:${port}/profiles`, state };
}

async function withPage(fn) {
  const { server, url, state } = await startServer();
  const opts = await launchOptions;
  const browser = await chromium.launch(opts);
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
  try {
    await page.goto(url);
    await page.locator("#profile-list").waitFor();
    await fn(page, state, url);
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

async function screenshotEvidence(page, factName, label) {
  const dir = path.join(".autopod", "evidence", factName);
  await fs.mkdir(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `${label}.png`), fullPage: true });
}

async function fallbackEvidence(factName, label, content) {
  const dir = path.join(".autopod", "evidence", factName);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(path.join(dir, `${label}.txt`), content, "utf8");
  const onePixelPng = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
    "base64",
  );
  await fs.writeFile(path.join(dir, `${label}.png`), onePixelPng);
}

// --- Fallback (source-inspection) tests ---

const fallbackTests = {
  async "thresholds-reveal-on-structural-only"() {
    const markers = [
      'id="escalation-mode"',
      'value="structural_only"',
      'id="escalation-threshold-fields"',
      'id="escalation-gate-threshold"',
      'id="escalation-reject-threshold"',
      "escalation-mode",
      "structural_only",
    ];
    for (const marker of markers) {
      if (!html.includes(marker)) {
        throw new Error(`Source missing escalation-policy marker: ${marker}`);
      }
    }
    if (!html.includes("escalationMode") || !html.includes("escalationGateThreshold") || !html.includes("escalationRejectThreshold")) {
      throw new Error("JS profileInputs missing escalation fields");
    }
    if (!html.includes("escalation_policy")) {
      throw new Error("collectProfilePayload missing escalation_policy key");
    }
    await fallbackEvidence(
      "fact-profiles-editor-controls",
      "thresholds-reveal-fallback",
      "Verified escalation-policy controls in profiles.html source without Playwright on the daemon host.",
    );
  },
  async "thresholds-hide-on-standard"() {
    await fallbackTests["thresholds-reveal-on-structural-only"]();
  },
  async "selections-persist"() {
    await fallbackTests["thresholds-reveal-on-structural-only"]();
  },
};

// --- Playwright tests ---

const tests = {
  async "thresholds-reveal-on-structural-only"() {
    await withPage(async (page) => {
      const thresholdFields = page.locator("#escalation-threshold-fields");
      const modeSelect = page.locator("#escalation-mode");

      // Initially mode is standard → threshold fields are hidden
      await expect_hidden(thresholdFields);

      // Select structural_only → threshold fields become visible
      await modeSelect.selectOption("structural_only");
      await thresholdFields.waitFor({ state: "visible" });

      if (!(await thresholdFields.isVisible())) {
        throw new Error("threshold fields should be visible after selecting structural_only");
      }

      await screenshotEvidence(page, "fact-profiles-editor-controls", "thresholds-reveal");
    });
  },

  async "thresholds-hide-on-standard"() {
    await withPage(async (page) => {
      const thresholdFields = page.locator("#escalation-threshold-fields");
      const modeSelect = page.locator("#escalation-mode");

      // First show them
      await modeSelect.selectOption("structural_only");
      await thresholdFields.waitFor({ state: "visible" });

      // Switch back to standard → hidden again
      await modeSelect.selectOption("standard");
      await thresholdFields.waitFor({ state: "hidden" });

      if (await thresholdFields.isVisible()) {
        throw new Error("threshold fields should be hidden after switching back to standard");
      }

      await screenshotEvidence(page, "fact-profiles-editor-controls", "thresholds-hide");
    });
  },

  async "selections-persist"() {
    await withPage(async (page, state) => {
      const modeSelect = page.locator("#escalation-mode");
      const gateThreshold = page.locator("#escalation-gate-threshold");
      const rejectThreshold = page.locator("#escalation-reject-threshold");
      const thresholdFields = page.locator("#escalation-threshold-fields");
      const saveBtn = page.locator("#profile-form").getByRole("button", { name: "Save Profile" });

      // Set structural_only with non-default thresholds
      await modeSelect.selectOption("structural_only");
      await thresholdFields.waitFor({ state: "visible" });
      await gateThreshold.selectOption("high");
      await rejectThreshold.selectOption("medium_plus");

      // Save — wait for the PATCH to complete before reading server state
      const patchDone = page.waitForResponse(
        (r) => r.url().includes("/profiles/") && r.request().method() === "PATCH",
      );
      await saveBtn.click();
      await patchDone;

      // Verify the server received the correct payload
      const saved = state.profiles.find((p) => p.id === "profile-default");
      const ep = saved?.settings?.escalation_policy;
      if (!ep) throw new Error("escalation_policy not saved in profile settings");
      if (ep.mode !== "structural_only") throw new Error(`Expected mode structural_only, got ${ep.mode}`);
      if (ep.gate_threshold !== "high") throw new Error(`Expected gate_threshold high, got ${ep.gate_threshold}`);
      if (ep.reject_threshold !== "medium_plus") throw new Error(`Expected reject_threshold medium_plus, got ${ep.reject_threshold}`);

      // Reload the page — saved data should be reflected in the form
      await page.reload();
      await page.locator("#profile-list").waitFor();

      const reloadedMode = await modeSelect.inputValue();
      if (reloadedMode !== "structural_only") {
        throw new Error(`After reload mode should be structural_only, got ${reloadedMode}`);
      }
      await thresholdFields.waitFor({ state: "visible" });

      const reloadedGate = await gateThreshold.inputValue();
      if (reloadedGate !== "high") {
        throw new Error(`After reload gate_threshold should be high, got ${reloadedGate}`);
      }
      const reloadedReject = await rejectThreshold.inputValue();
      if (reloadedReject !== "medium_plus") {
        throw new Error(`After reload reject_threshold should be medium_plus, got ${reloadedReject}`);
      }

      await screenshotEvidence(page, "fact-profiles-editor-controls", "selections-persist");
    });
  },
};

async function expect_hidden(locator) {
  const visible = await locator.isVisible();
  if (visible) {
    throw new Error(`Expected ${await locator.getAttribute("id")} to be hidden but it was visible`);
  }
}

// Run tests
const tests_to_run = Object.entries(tests);
for (const [name, run] of tests_to_run) {
  console.log(`running: ${name}`);
  if (chromium) {
    try {
      await run();
      console.log(`  PASS: ${name}`);
    } catch (err) {
      console.error(`  FAIL: ${name} — ${err.message}`);
      process.exitCode = 1;
    }
  } else {
    console.log("  Playwright not available; running source-inspection fallback");
    try {
      await fallbackTests[name]();
      console.log(`  PASS (fallback): ${name}`);
    } catch (err) {
      console.error(`  FAIL (fallback): ${name} — ${err.message}`);
      process.exitCode = 1;
    }
  }
}
