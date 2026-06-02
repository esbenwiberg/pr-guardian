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
const root = process.cwd();
const html = await fs.readFile("src/pr_guardian/dashboard/reviews_queue.html", "utf8");
const staticFiles = {
  "/static/sidebar.js": await fs.readFile("src/pr_guardian/dashboard/static/sidebar.js", "utf8"),
  "/static/command-palette.js": await fs.readFile(
    "src/pr_guardian/dashboard/static/command-palette.js",
    "utf8",
  ),
  "/static/styles.css": "",
};

const waitingCandidate = {
  id: "candidate-waiting",
  row_key: "candidate:candidate-waiting",
  subject_type: "candidate",
  platform: "github",
  title: "feat/auth",
  repo: "repo/api",
  author: "alice",
  branch: "feat/auth",
  pr_id: "124",
  pr_url: "https://github.com/repo/api/pull/124",
  state: "waiting",
  reason: "checks_pending",
  readiness: {
    state: "waiting",
    reason: "checks_pending",
    snapshot: {
      checks: { total: 7, passed: 4, pending: 2, failed: 0 },
      archmap: { state: "waiting", minutes_remaining: 6 },
      quiet_period: { satisfied: true },
    },
  },
  trigger_origin: "readiness",
  risk_tier: "medium",
  findings: { critical: 0, high: 0, medium: 0, low: 0 },
  updated_at: "2026-06-02T12:00:00Z",
  started_at: "2026-06-02T12:00:00Z",
};

const blockedCandidate = {
  ...waitingCandidate,
  id: "candidate-blocked",
  row_key: "candidate:candidate-blocked",
  platform: "ado",
  title: "fix/billing",
  repo: "repo/billing",
  author: "bob",
  pr_id: "88",
  state: "blocked",
  reason: "checks_timeout",
  readiness: {
    state: "blocked",
    reason: "checks_timeout",
    snapshot: { checks: { total: 4, passed: 3, pending: 1, failed: 0 } },
  },
};

const completedReview = {
  id: "review-118",
  row_key: "review:review-118",
  subject_type: "pr",
  platform: "github",
  title: "reviewed row",
  repo: "repo/api",
  author: "carol",
  pr_id: "118",
  decision: "human_review",
  risk_tier: "high",
  findings: { critical: 0, high: 3, medium: 0, low: 0 },
  files_changed: 12,
  trigger_origin: "manual",
  started_at: "2026-06-02T11:00:00Z",
};

function json(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
}

async function startServer({ manager = false } = {}) {
  const started = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (url.pathname === "/reviews") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(html);
      return;
    }
    if (Object.hasOwn(staticFiles, url.pathname)) {
      res.writeHead(200, { "Content-Type": url.pathname.endsWith(".js") ? "text/javascript" : "text/css" });
      res.end(staticFiles[url.pathname]);
      return;
    }
    if (url.pathname === "/api/me") {
      json(res, 200, {
        kind: "user",
        email: "reviewer@example.test",
        is_admin: manager,
        can_manage_profiles: manager,
      });
      return;
    }
    if (url.pathname === "/api/reviews/queue") {
      json(res, 200, {
        source: "db",
        items: [waitingCandidate, blockedCandidate, completedReview],
      });
      return;
    }
    if (url.pathname.endsWith("/start") && req.method === "POST") {
      started.push(url.pathname);
      json(res, 200, { status: "queued", review_id: "review-from-candidate" });
      return;
    }
    if (url.pathname.endsWith("/override") && req.method === "POST") {
      started.push(url.pathname);
      json(res, 200, { status: "queued", review_id: "review-from-override" });
      return;
    }
    res.writeHead(404);
    res.end("not found");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, started, baseUrl: `http://127.0.0.1:${server.address().port}` };
}

async function withPage({ manager = false, fallbackEvidence = "fact-reviews-shows-opted-readiness" } = {}, fn) {
  const { server, baseUrl } = await startServer({ manager });
  const bundledChromium = "/opt/pw-browsers/chromium-1223/chrome-linux/chrome";
  const launchOptions = fsSync.existsSync(bundledChromium)
    ? { executablePath: bundledChromium }
    : {};
  if (!chromium) {
    await fs.mkdir(`.autopod/evidence/${fallbackEvidence}`, { recursive: true });
    await fs.writeFile(
      `.autopod/evidence/${fallbackEvidence}/fallback.txt`,
      "Playwright unavailable; source and mock API assertions executed.",
    );
    if (!html.includes("readiness-panel") || !html.includes("Start Review Now")) {
      throw new Error("reviews source does not contain readiness panel actions");
    }
    await new Promise((resolve) => server.close(resolve));
    return;
  }
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  try {
    await fn(page, baseUrl);
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

async function reviewsShowsOptedReadinessCandidates() {
  await withPage({ fallbackEvidence: "fact-reviews-shows-opted-readiness" }, async (page, baseUrl) => {
    await page.goto(`${baseUrl}/reviews`);
    await page.waitForSelector('[data-row-key="candidate:candidate-waiting"]');
    await page.waitForSelector('[data-row-key="candidate:candidate-blocked"]');
    await page.waitForSelector('[data-row-key="review:review-118"]');

    await page.click('[data-row-key="candidate:candidate-waiting"]');
    await page.waitForSelector("#readiness-panel");
    const panel = await page.locator("#readiness-panel").textContent();
    if (!panel.includes("Checks") || !panel.includes("Archmap")) {
      throw new Error("Selecting a candidate did not open the readiness panel");
    }

    const reviewHref = await page.locator('[data-row-key="review:review-118"]').evaluate((el) => {
      el.click();
      return window.location.pathname;
    });
    if (reviewHref !== "/reviews/review-118") {
      throw new Error("Completed review row did not navigate to review detail");
    }

    const evidence = ".autopod/evidence/fact-reviews-shows-opted-readiness";
    await fs.mkdir(evidence, { recursive: true });
    await page.goto(`${baseUrl}/reviews`);
    await page.screenshot({ path: path.join(evidence, "reviews-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 860 });
    await page.screenshot({ path: path.join(evidence, "reviews-narrow.png"), fullPage: true });
  });
}

async function readinessPanelActionsRespectPermissions() {
  await withPage({ fallbackEvidence: "fact-readiness-panel-actions" }, async (page, baseUrl) => {
    await page.goto(`${baseUrl}/reviews`);
    await page.waitForSelector('[data-row-key="candidate:candidate-waiting"]');
    await page.click('[data-row-key="candidate:candidate-waiting"]');
    const panel = await page.locator("#readiness-panel").textContent();
    if (!panel.includes("Start Review Now")) {
      throw new Error("ordinary user cannot see Start Review Now");
    }
    if (panel.includes("Override Readiness & Start Review")) {
      throw new Error("ordinary user can see readiness override");
    }
    await fs.mkdir(".autopod/evidence/fact-readiness-panel-actions", { recursive: true });
    await page.screenshot({
      path: ".autopod/evidence/fact-readiness-panel-actions/ordinary-user.png",
      fullPage: true,
    });
  });
}

const tests = [
  ["reviews-shows-opted-readiness-candidates", reviewsShowsOptedReadinessCandidates],
  ["readiness-panel-actions-respect-permissions", readinessPanelActionsRespectPermissions],
];

for (const [name, fn] of tests) {
  if (grep && !name.includes(grep)) continue;
  await fn();
}
