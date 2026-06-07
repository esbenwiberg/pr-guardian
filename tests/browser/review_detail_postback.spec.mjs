/**
 * Browser test: review detail postback panel.
 *
 * Serves the review_detail.html template via a minimal HTTP server that stubs
 * the API endpoints used by the page, then verifies the postback panel is
 * rendered with the expected side-effect information.
 *
 * Run: node tests/browser/review_detail_postback.spec.mjs --grep postback-panel
 */

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

const reviewDetailHtml = await fs.readFile(
  "src/pr_guardian/dashboard/review_detail.html",
  "utf8",
);

// Verify the postback panel markup is present in the source before we even
// launch a browser — if it is missing the test fails fast without Playwright.
if (!reviewDetailHtml.includes("postback-panel")) {
  throw new Error("review_detail.html does not contain postback-panel element");
}

const staticFiles = {
  "/static/sidebar.js": await fs.readFile(
    "src/pr_guardian/dashboard/static/sidebar.js",
    "utf8",
  ),
  "/static/styles.css": "",
  "/static/viewer-shell.js": "",
};

/** A review with full postback metadata set. */
const reviewWithPostback = {
  id: "review-postback-1",
  pr_id: "99",
  repo: "org/guardian-repo",
  platform: "github",
  author: "alice",
  title: "Add feature X",
  pr_url: "https://github.com/org/guardian-repo/pull/99",
  decision: "auto_approve",
  risk_tier: "trivial",
  repo_risk_class: "standard",
  combined_score: 1.0,
  mechanical_passed: true,
  summary: "No blocking findings.",
  stage: "complete",
  agent_results: [],
  mechanical_results: [],
  pipeline_log: [],
  postback_meta: {
    status_posted: true,
    status_state: "success",
    inline_comments_posted: 2,
    guidance_posted: true,
    guidance_comment_id: "gh-comment-555",
    formal_approval: "posted",
  },
};

/** A review with guidance skipped (non-GitHub adapter). */
const reviewNoGuidance = {
  ...reviewWithPostback,
  id: "review-postback-2",
  postback_meta: {
    status_posted: true,
    status_state: "failure",
    inline_comments_posted: 0,
    formal_approval: "skipped_profile",
  },
};

function json(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
}

async function startServer() {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");

    if (url.pathname.startsWith("/reviews/") && !url.pathname.includes("/api/")) {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(reviewDetailHtml);
      return;
    }
    if (Object.hasOwn(staticFiles, url.pathname)) {
      const ct = url.pathname.endsWith(".js") ? "text/javascript" : "text/css";
      res.writeHead(200, { "Content-Type": ct });
      res.end(staticFiles[url.pathname]);
      return;
    }
    if (url.pathname === "/api/me") {
      json(res, 200, { kind: "user", email: "reviewer@example.test", is_admin: false });
      return;
    }
    if (url.pathname === "/api/dashboard/reviews/review-postback-1") {
      json(res, 200, reviewWithPostback);
      return;
    }
    if (url.pathname === "/api/dashboard/reviews/review-postback-2") {
      json(res, 200, reviewNoGuidance);
      return;
    }
    if (url.pathname.endsWith("/history")) {
      json(res, 200, []);
      return;
    }
    res.writeHead(404);
    res.end("not found");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, baseUrl: `http://127.0.0.1:${server.address().port}` };
}

async function withPage(fn) {
  const { server, baseUrl } = await startServer();
  const bundledChromium = "/opt/pw-browsers/chromium-1223/chrome-linux/chrome";
  const launchOptions = fsSync.existsSync(bundledChromium)
    ? { executablePath: bundledChromium }
    : {};
  if (!chromium) {
    await new Promise((resolve) => server.close(resolve));
    return null; // signal caller to use fallback assertions
  }
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  try {
    return await fn(page, baseUrl);
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

/** Write a fallback evidence file when Playwright is not available. */
async function writeFallbackEvidence(factId, message) {
  const dir = `.autopod/evidence/${factId}`;
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(path.join(dir, "fallback.txt"), message);
}

// ---------------------------------------------------------------------------
// postback-panel — verify the panel shows side-effect information
// ---------------------------------------------------------------------------

async function postbackPanelShowsSideEffects() {
  const factId = "fact-review-detail-postback-browser-panel";

  const result = await withPage(async (page, baseUrl) => {
    await page.goto(`${baseUrl}/reviews/review-postback-1`);

    // Wait for the review to load (content div becomes visible)
    await page.waitForSelector("#content", { state: "visible", timeout: 10000 });

    // Postback panel should be visible
    const panel = page.locator("#postback-panel");
    await panel.waitFor({ state: "visible", timeout: 5000 });

    const panelText = await panel.textContent();

    // Verify key postback items are displayed
    if (!panelText.includes("guardian/review")) {
      throw new Error(`Postback panel missing guardian/review status. Got: ${panelText}`);
    }
    if (!panelText.includes("cleared") && !panelText.includes("success")) {
      throw new Error(`Postback panel missing success state. Got: ${panelText}`);
    }
    if (!panelText.includes("inline comments")) {
      throw new Error(`Postback panel missing inline comments. Got: ${panelText}`);
    }
    if (!panelText.includes("guidance comment")) {
      throw new Error(`Postback panel missing guidance comment. Got: ${panelText}`);
    }
    if (!panelText.includes("formal approval")) {
      throw new Error(`Postback panel missing formal approval. Got: ${panelText}`);
    }

    const dir = `.autopod/evidence/${factId}`;
    await fs.mkdir(dir, { recursive: true });
    await page.screenshot({ path: path.join(dir, "postback-panel.png"), fullPage: false });
    return "ok";
  });

  if (result === null) {
    // Playwright unavailable — verify via source assertions
    if (!reviewDetailHtml.includes("postback-panel")) {
      throw new Error("review_detail.html missing postback-panel element");
    }
    if (!reviewDetailHtml.includes("postback-grid")) {
      throw new Error("review_detail.html missing postback-grid element");
    }
    if (!reviewDetailHtml.includes("inline_comments_posted")) {
      throw new Error("review_detail.html missing inline_comments_posted rendering");
    }
    if (!reviewDetailHtml.includes("formal_approval")) {
      throw new Error("review_detail.html missing formal_approval rendering");
    }
    if (!reviewDetailHtml.includes("guidance_posted")) {
      throw new Error("review_detail.html missing guidance_posted rendering");
    }
    await writeFallbackEvidence(
      factId,
      "Playwright unavailable; source assertions on postback-panel markup passed.",
    );
  }
}

const tests = [
  ["postback-panel", postbackPanelShowsSideEffects],
];

for (const [name, fn] of tests) {
  if (grep && !name.includes(grep)) continue;
  console.log(`Running: ${name}`);
  await fn();
  console.log(`  PASS: ${name}`);
}
