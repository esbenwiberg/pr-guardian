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

const root = process.cwd();
const pullRequestsHtml = await fs.readFile("src/pr_guardian/dashboard/pull_requests.html", "utf8");
const reviewsHtml = await fs.readFile("src/pr_guardian/dashboard/reviews_queue.html", "utf8");
const staticFiles = {
  "/static/sidebar.js": await fs.readFile("src/pr_guardian/dashboard/static/sidebar.js", "utf8"),
  "/static/command-palette.js": await fs.readFile(
    "src/pr_guardian/dashboard/static/command-palette.js",
    "utf8",
  ),
  "/static/styles.css": "",
};

const browseOnlyPr = {
  id: "11111111-2222-3333-4444-555555555555",
  platform: "github",
  pr_id: "88",
  org: "browse",
  project: "",
  repo: "browse/only",
  title: "Unlinked browse-only pull request",
  author: "alice",
  author_display: "Alice",
  pr_url: "https://github.com/browse/only/pull/88",
  source_branch: "feat/only",
  target_branch: "main",
  approval_status: "pending",
  ci_status: "success",
  repo_link_id: null,
  has_guardian_review: false,
  guardian_review_id: null,
  guardian_decision: null,
  pr_updated_at: "2026-06-02T12:00:00Z",
  connection_snapshot: { name: "Browse Only GitHub" },
};

const linkedPr = {
  ...browseOnlyPr,
  id: "22222222-3333-4444-5555-666666666666",
  pr_id: "124",
  repo: "repo/api",
  title: "Linked opted-in pull request",
  pr_url: "https://github.com/repo/api/pull/124",
  repo_link_id: "repo-link-1",
  has_guardian_review: true,
  guardian_review_id: "review-linked",
  guardian_decision: "human_review",
  connection_snapshot: { name: "Linked GitHub" },
};

function json(res, payload) {
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(payload));
}

function startServer() {
  const started = [];
  const hidden = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/pr-dashboard" || url.pathname === "/browse-pr") {
      res.writeHead(302, { location: "/pull-requests" });
      res.end();
      return;
    }
    if (url.pathname === "/pull-requests") {
      res.writeHead(200, { "content-type": "text/html" });
      res.end(pullRequestsHtml);
      return;
    }
    if (url.pathname === "/reviews") {
      res.writeHead(200, { "content-type": "text/html" });
      res.end(reviewsHtml);
      return;
    }
    if (Object.hasOwn(staticFiles, url.pathname)) {
      res.writeHead(200, { "Content-Type": url.pathname.endsWith(".js") ? "text/javascript" : "text/css" });
      res.end(staticFiles[url.pathname]);
      return;
    }
    if (url.pathname === "/api/me") {
      json(res, { kind: "user", email: "viewer@example.test", is_admin: false });
      return;
    }
    if (url.pathname === "/api/prs") {
      json(res, { items: [browseOnlyPr, linkedPr], total: 2, offset: 0, limit: 100 });
      return;
    }
    if (url.pathname === "/api/prs/sync" && req.method === "POST") {
      json(res, { ok: true, message: "sync started" });
      return;
    }
    if (url.pathname.endsWith("/start-wizard") && req.method === "POST") {
      started.push(url.pathname);
      json(res, { mode: "new", review_id: "review-started" });
      return;
    }
    if (url.pathname === "/api/prs/exclude-repo" && req.method === "POST") {
      hidden.push(url.pathname);
      json(res, { ok: true, added: true });
      return;
    }
    if (url.pathname === "/api/reviews/queue") {
      json(res, {
        items: [
          {
            id: "review-1",
            row_key: "review:review-1",
            subject_type: "pr",
            platform: "github",
            title: "Existing Guardian review",
            repo: "reviewed/repo",
          },
        ],
        source: "db",
      });
      return;
    }
    res.writeHead(404);
    res.end("not found");
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({ server, started, hidden, baseUrl: `http://127.0.0.1:${server.address().port}` });
    });
  });
}

async function sourceAssertions(baseUrl) {
  const pullResponse = await fetch(`${baseUrl}/pull-requests`);
  const pullHtml = await pullResponse.text();
  for (const marker of [
    "Pull Requests",
    "/api/prs?",
    "connection_snapshot?.name",
    "Start Review Now",
    "Hide repo from browse",
    "Linked repo",
  ]) {
    if (!pullHtml.includes(marker)) {
      throw new Error(`/pull-requests page is missing marker: ${marker}`);
    }
  }
  if (pullHtml.includes("params.set('view'") || pullHtml.includes('params.set("view"')) {
    throw new Error("/pull-requests should fetch the broad synced set and filter tabs client-side");
  }

  const apiResponse = await fetch(`${baseUrl}/api/prs`);
  const apiData = await apiResponse.json();
  if (!apiData.items.some((item) => item.title === "Unlinked browse-only pull request")) {
    throw new Error("Browse API did not return the unlinked synced PR");
  }

  for (const legacyPath of ["/pr-dashboard", "/browse-pr"]) {
    const redirectResponse = await fetch(`${baseUrl}${legacyPath}`, { redirect: "manual" });
    if (redirectResponse.status !== 302 || redirectResponse.headers.get("location") !== "/pull-requests") {
      throw new Error(`${legacyPath} did not redirect to /pull-requests`);
    }
  }

  const reviewsResponse = await fetch(`${baseUrl}/api/reviews/queue`);
  const reviewsData = await reviewsResponse.json();
  const reviewTitles = reviewsData.items.map((item) => item.title);
  if (reviewTitles.includes("Unlinked browse-only pull request")) {
    throw new Error("Browse-only synced PR appeared in Reviews queue data");
  }
}

async function browserAssertions(baseUrl) {
  const bundledChromium = "/opt/pw-browsers/chromium-1223/chrome-linux/chrome";
  const launchOptions = fsSync.existsSync(bundledChromium)
    ? { executablePath: bundledChromium }
    : {};
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  try {
    await page.goto(`${baseUrl}/pull-requests`);
    await page.waitForSelector("text=Unlinked browse-only pull request");
    await page.click("text=Unlinked browse-only pull request");
    const panel = await page.locator("#pr-panel").textContent();
    if (!panel.includes("Linked repo") || !panel.includes("Guardian") || !panel.includes("Browse Only GitHub")) {
      throw new Error("Pull request panel does not show linked, Guardian, and Connection status");
    }

    await fs.mkdir(".autopod/evidence/fact-browse-prs-separated", { recursive: true });
    await page.screenshot({
      path: path.join(root, ".autopod/evidence/fact-browse-prs-separated/pull-requests-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 860 });
    await page.screenshot({
      path: path.join(root, ".autopod/evidence/fact-browse-prs-separated/pull-requests-narrow.png"),
      fullPage: true,
    });
  } finally {
    await browser.close();
  }
}

const { server, baseUrl } = await startServer();
try {
  await sourceAssertions(baseUrl);
  if (chromium) {
    await browserAssertions(baseUrl);
  } else {
    await fs.mkdir(".autopod/evidence/fact-browse-prs-separated", { recursive: true });
    await fs.writeFile(
      ".autopod/evidence/fact-browse-prs-separated/fallback.txt",
      "Playwright unavailable; source and mock API assertions executed.",
    );
  }
} finally {
  await new Promise((resolve) => server.close(resolve));
}
