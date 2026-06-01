import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

const root = process.cwd();
const pullRequestsHtml = join(root, "src/pr_guardian/dashboard/pull_requests.html");
const reviewsHtml = join(root, "src/pr_guardian/dashboard/reviews_queue.html");

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
  connection_snapshot: { name: "Browse Only GitHub" },
};

function json(res, payload) {
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(payload));
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/pr-dashboard") {
    res.writeHead(302, { location: "/pull-requests" });
    res.end();
    return;
  }
  if (url.pathname === "/pull-requests") {
    res.writeHead(200, { "content-type": "text/html" });
    res.end(await readFile(pullRequestsHtml, "utf8"));
    return;
  }
  if (url.pathname === "/reviews") {
    res.writeHead(200, { "content-type": "text/html" });
    res.end(await readFile(reviewsHtml, "utf8"));
    return;
  }
  if (url.pathname === "/api/prs") {
    json(res, { items: [browseOnlyPr], total: 1, offset: 0, limit: 100 });
    return;
  }
  if (url.pathname === "/api/reviews/queue") {
    json(res, {
      items: [{
        id: "review-1",
        subject_type: "pr",
        platform: "github",
        title: "Existing Guardian review",
        repo: "reviewed/repo",
      }],
      source: "db",
    });
    return;
  }
  res.writeHead(404);
  res.end("not found");
});

await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const { port } = server.address();
const baseUrl = `http://127.0.0.1:${port}`;

try {
  const pullResponse = await fetch(`${baseUrl}/pull-requests`);
  const pullHtml = await pullResponse.text();
  if (!pullHtml.includes("Pull Requests")) {
    throw new Error("/pull-requests page shell did not render the browse title");
  }
  if (!pullHtml.includes("/api/prs?")) {
    throw new Error("/pull-requests page does not load broad synced PR data");
  }
  if (!pullHtml.includes("connection_snapshot?.name")) {
    throw new Error("/pull-requests page does not render Connection provenance");
  }

  const apiResponse = await fetch(`${baseUrl}/api/prs`);
  const apiData = await apiResponse.json();
  if (apiData.items[0].title !== "Unlinked browse-only pull request") {
    throw new Error("Browse API did not return the unlinked synced PR");
  }

  const redirectResponse = await fetch(`${baseUrl}/pr-dashboard`, { redirect: "manual" });
  if (redirectResponse.status !== 302 || redirectResponse.headers.get("location") !== "/pull-requests") {
    throw new Error("/pr-dashboard did not redirect to /pull-requests");
  }

  const reviewsResponse = await fetch(`${baseUrl}/api/reviews/queue`);
  const reviewsData = await reviewsResponse.json();
  const reviewTitles = reviewsData.items.map(item => item.title);
  if (reviewTitles.includes("Unlinked browse-only pull request")) {
    throw new Error("Browse-only synced PR appeared in Reviews queue data");
  }

  const reviewsPage = await fetch(`${baseUrl}/reviews`);
  const reviewsHtmlText = await reviewsPage.text();
  if (reviewsHtmlText.includes("Unlinked browse-only pull request")) {
    throw new Error("Browse-only synced PR is hard-coded into Reviews page");
  }
} finally {
  await new Promise(resolve => server.close(resolve));
}
