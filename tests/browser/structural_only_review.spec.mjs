/**
 * Smoke tests for the structural-only gate decision panel on review_detail.html.
 *
 * Contract fact: fact-review-detail-gate-panel
 * Scenarios verified:
 *   - auto-approve-shows-gate-panel: auto-approved structural_only review shows
 *     the Gate decision panel (level + reason + "not blocking" line) and NO
 *     finding-signals panel.
 *   - human-gated-shows-gate-trigger: human-gated structural_only review shows
 *     the gate_agent sticky trigger in the structural-triggers panel.
 *
 * Strategy: open the static HTML via file:// URL, wait for scripts to load,
 * then inject mock review data and call render() directly — bypassing the
 * real fetch so no server is needed.
 */

import { pathToFileURL } from "node:url";
import { existsSync } from "node:fs";
import { chromium } from "playwright";

const pageUrl =
  pathToFileURL("src/pr_guardian/dashboard/review_detail.html").href +
  "?review_id=test-review-id";

/**
 * Minimal auto-approved structural_only review with low-danger gate verdict
 * and 3 findings (all noise, not blocking).
 */
const AUTO_APPROVED_REVIEW = {
  id: "test-review-id",
  pr_id: "42",
  repo: "org/repo",
  platform: "github",
  decision: "auto_approve",
  stage: "complete",
  escalation_mode: "structural_only",
  gate_read: {
    level: "low",
    reason: "CI/workflow-only change. No prod paths, no trust boundary.",
    gated: false,
  },
  sticky_triggers: [],
  finding_reasons: ["9 suspected findings (noise)"],
  agent_results: [
    {
      agent_name: "security_privacy",
      verdict: "warn",
      findings: [
        {
          id: "f1",
          severity: "medium",
          certainty: "suspected",
          category: "style",
          file: "a.py",
          line: 1,
          description: "Suspected issue",
        },
        {
          id: "f2",
          severity: "medium",
          certainty: "suspected",
          category: "style",
          file: "b.py",
          line: 2,
          description: "Another suspected issue",
        },
        {
          id: "f3",
          severity: "medium",
          certainty: "suspected",
          category: "style",
          file: "c.py",
          line: 3,
          description: "Third suspected issue",
        },
      ],
    },
  ],
  mechanical_results: [],
  profile_snapshot: {
    escalation_policy: {
      mode: "structural_only",
      gate_threshold: "medium_plus",
      reject_threshold: "confident_only",
    },
  },
  risk_tier: "low",
  combined_score: 1.2,
  cost_usd: 0.001,
  duration_ms: 1200,
};

/**
 * Human-gated structural_only review escalated by the gate agent.
 */
const HUMAN_GATED_REVIEW = {
  id: "test-review-id-gated",
  pr_id: "43",
  repo: "org/repo",
  platform: "github",
  decision: "human_review",
  stage: "complete",
  escalation_mode: "structural_only",
  gate_read: {
    level: "high",
    reason: "Drops a column in 003_*.py; destructive migration.",
    gated: true,
  },
  sticky_triggers: [
    {
      kind: "archmap_hub",
      label: "Archmap hub touched: src/core/orchestrator.py",
      reason: "Hub file with 40 dependents",
      source: "src/core/orchestrator.py",
    },
    {
      kind: "gate_agent",
      label: "Gate agent: HIGH danger",
      reason: "Drops a column in 003_*.py; destructive migration.",
      source: "gate_agent",
    },
  ],
  finding_reasons: [],
  agent_results: [],
  mechanical_results: [],
  profile_snapshot: {
    escalation_policy: {
      mode: "structural_only",
      gate_threshold: "medium_plus",
      reject_threshold: "confident_only",
    },
  },
  risk_tier: "high",
  combined_score: 0,
  cost_usd: 0.002,
  duration_ms: 1500,
};

async function runTest(browser, label, reviewData, assertFn) {
  const page = await browser.newPage();
  try {
    // Suppress console noise from the review fetch failure (file:// origin).
    page.on("console", () => {});
    page.on("pageerror", () => {});

    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });

    // Wait for the JS to finish loading (loadReview runs, fails, shows error — that's OK).
    await page.waitForTimeout(300);

    // Inject mock data and render directly — bypasses fetch entirely.
    await page.evaluate((data) => {
      // Expose content div and hide error/loading so render() can show the panel.
      document.getElementById("loading").style.display = "none";
      const errEl = document.getElementById("error");
      if (errEl) errEl.style.display = "none";

      // Call the global render function with our mock data.
      window.reviewData = data;
      render(data);
    }, reviewData);

    await assertFn(page);
    console.log(`  ✓ ${label}`);
  } finally {
    await page.close();
  }
}

// Resolve a headless_shell executable. The system may have a different
// minor version than what this Playwright release expects; fall through
// candidates until one exists so the tests run without root access.
function resolveHeadlessShell() {
  const candidates = [
    // Exact version expected by this Playwright release (happy path).
    "/opt/pw-browsers/chromium_headless_shell-1217/chrome-linux/headless_shell",
    // System version (installed by the container image — may differ by minor).
    "/opt/pw-browsers/chromium_headless_shell-1223/chrome-linux/headless_shell",
  ];
  for (const p of candidates) {
    if (existsSync(p)) return p;
  }
  return undefined; // let Playwright use its own default (will error with instructions)
}

const browser = await chromium.launch({ executablePath: resolveHeadlessShell() });

try {
  // -----------------------------------------------------------------------
  // Scenario: auto-approve-shows-gate-panel
  // -----------------------------------------------------------------------
  await runTest(
    browser,
    "auto-approved structural_only: gate panel visible, finding-signals hidden",
    AUTO_APPROVED_REVIEW,
    async (page) => {
      // Gate decision panel must be visible.
      const gateSection = page.locator("#gate-decision-section");
      await gateSection.waitFor({ state: "visible" });

      // Level label must contain "LOW danger".
      const levelLabel = await page.locator("#gate-level-label").textContent();
      if (!levelLabel.includes("LOW")) {
        throw new Error(
          `Gate level label should include "LOW", got: "${levelLabel}"`
        );
      }

      // Reason text must echo the mock reason.
      const reasonText = await page.locator("#gate-reason-text").textContent();
      if (!reasonText.includes("CI/workflow-only change")) {
        throw new Error(
          `Gate reason not rendered, got: "${reasonText}"`
        );
      }

      // Findings line must say "not blocking".
      const findingsCell = await page.locator("#gate-findings-cell").textContent();
      if (!findingsCell.includes("not blocking")) {
        throw new Error(
          `Findings cell must include "not blocking", got: "${findingsCell}"`
        );
      }

      // Finding-signals panel must NOT be shown in structural_only.
      const findingSignals = page.locator("#finding-reasons-section");
      const isVisible = await findingSignals.isVisible();
      if (isVisible) {
        throw new Error("finding-reasons-section must be hidden in structural_only mode");
      }
    }
  );

  // -----------------------------------------------------------------------
  // Scenario: human-gated-shows-gate-trigger
  // -----------------------------------------------------------------------
  await runTest(
    browser,
    "human-gated structural_only: gate_agent trigger in structural-triggers panel",
    HUMAN_GATED_REVIEW,
    async (page) => {
      // Structural-triggers panel must be visible.
      const stickiesSection = page.locator("#sticky-triggers-section");
      await stickiesSection.waitFor({ state: "visible" });

      // The gate_agent trigger label must appear in the list.
      const triggersList = await page.locator("#sticky-triggers-list").textContent();
      if (!triggersList.includes("Gate agent: HIGH danger")) {
        throw new Error(
          `gate_agent trigger not found in structural-triggers list, got: "${triggersList}"`
        );
      }

      // The gate reason must also appear.
      if (!triggersList.includes("destructive migration")) {
        throw new Error(
          `gate_agent reason not found in structural-triggers list`
        );
      }

      // Finding-signals panel must NOT appear in structural_only.
      const isVisible = await page.locator("#finding-reasons-section").isVisible();
      if (isVisible) {
        throw new Error("finding-reasons-section must be hidden in structural_only mode");
      }
    }
  );

  console.log("\nAll structural_only review smoke tests passed.");
} finally {
  await browser.close();
}
