import fsSync from "node:fs";
import fs from "node:fs/promises";
import { pathToFileURL } from "node:url";

let chromium = null;
try {
  ({ chromium } = await import("playwright"));
} catch {
  chromium = null;
}

const htmlPath = "src/pr_guardian/dashboard/reviews_queue.html";
const html = await fs.readFile(htmlPath, "utf8");

if (!html.includes("repository review options")) {
  throw new Error("Repository review options copy is missing");
}
if (!html.includes("Will review ") || html.includes("Will scan ")) {
  throw new Error("Repository review preview still uses scan copy");
}
if (!html.includes('data-filter="scans">Scans')) {
  throw new Error("Actual scan filter copy was removed from Reviews");
}

await fs.mkdir(".autopod/evidence/fact-repo-review-copy-distinct-from-scan", {
  recursive: true,
});

const bundledChromium = "/opt/pw-browsers/chromium-1223/chrome-linux/chrome";
const launchOptions = fsSync.existsSync(bundledChromium)
  ? { executablePath: bundledChromium }
  : {};

if (chromium) {
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 900, height: 640 } });
  try {
    await page.goto(pathToFileURL(htmlPath).href);
    const input = page.locator("#trigger-url");
    const preview = page.locator("#scan-preview");
    await input.fill("https://github.com/octocat/spoon");
    await preview.waitFor({ state: "visible" });
    const text = await preview.textContent();
    if (text !== "Will review GitHub repository: octocat/spoon") {
      throw new Error("Repository URL preview did not use Review repository copy");
    }
    await page.screenshot({
      path: ".autopod/evidence/fact-repo-review-copy-distinct-from-scan/repo-review-copy.png",
      fullPage: true,
    });
  } finally {
    await browser.close();
  }
} else {
  await fs.writeFile(
    ".autopod/evidence/fact-repo-review-copy-distinct-from-scan/fallback.txt",
    "Playwright unavailable; source assertions verified repository review copy.",
  );
}
