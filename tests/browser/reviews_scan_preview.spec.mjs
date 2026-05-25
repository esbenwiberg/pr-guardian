import { pathToFileURL } from "node:url";
import { chromium } from "playwright";

const pageUrl = pathToFileURL("src/pr_guardian/dashboard/reviews_queue.html").href;

const browser = await chromium.launch();
const page = await browser.newPage();

try {
  await page.goto(pageUrl);

  const input = page.locator("#trigger-url");
  const platform = page.locator("#scan-platform");
  const preview = page.locator("#scan-preview");

  await input.fill("https://github.com/octocat/spoon");
  await preview.waitFor({ state: "visible" });
  if ((await preview.textContent()) !== "Will scan GitHub repo: octocat/spoon") {
    throw new Error("GitHub URL preview did not render canonical owner/repo");
  }

  await input.fill("https://github.com/octocat/spoon.git");
  if ((await preview.textContent()) !== "Will scan GitHub repo: octocat/spoon") {
    throw new Error("GitHub .git suffix was not stripped");
  }

  await input.fill("https://dev.azure.com/myorg/myproj/_git/myrepo");
  if ((await preview.textContent()) !== "Will scan Azure DevOps repo: myproj/myrepo") {
    throw new Error("ADO URL preview did not render canonical project/repo");
  }

  await input.fill("project/repo");
  await platform.selectOption("ado");
  if ((await preview.textContent()) !== "Will scan Azure DevOps repo: project/repo") {
    throw new Error("ADO two-segment shorthand did not respect platform selector");
  }

  await input.fill("https://github.com/octocat/spoon/pull/1");
  if (await preview.isVisible()) {
    throw new Error("PR URLs must not show repo-scan preview");
  }
} finally {
  await browser.close();
}
