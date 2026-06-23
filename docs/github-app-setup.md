# GitHub App Setup

PR Guardian authenticates to GitHub exclusively through a **GitHub App Connection**.
There is no `GITHUB_TOKEN` or PAT fallback for Guardian runtime API calls.
This guide walks through creating the App, installing it, and wiring it into Guardian.

---

## 1. Create the GitHub App

1. Go to **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App**
   (or your org's **Settings → Developer settings → GitHub Apps → New GitHub App**).

2. Fill in the basics:
   - **App name**: something like `guardian-review` or `my-org-guardian`
   - **Homepage URL**: your Guardian deployment URL
   - **Webhook URL**: `https://<your-guardian-host>/api/webhooks/github`
     (or leave blank if you will use signed replay only)
   - **Webhook secret**: generate a random secret and keep it — you will set
     `GITHUB_WEBHOOK_SECRET` in your Guardian environment

3. Set **Repository permissions**:

   | Permission | Access |
   |---|---|
   | Actions | Read |
   | Contents | Read |
   | Pull requests | Read & Write |
   | Issues | Read & Write |
   | Commit statuses | Read & Write |
   | Checks | Read |
   | Administration | Read & Write |

   > `Administration: Write` is required for Guardian to add the `guardian/review`
   > required check to branch protection rules.
   >
   > `Actions: Read` is required for the Archmap readiness gate — Guardian polls
   > the Actions artifacts API for an `archmap-<head_sha>` artifact. Without it
   > the call 403s and PRs strand in `archmap_wait` until the soft timeout.

4. Set **Subscribe to events**:
   - `Pull request`
   - `Issue comment`

5. Choose **Where can this GitHub App be installed?**:
   - **Only on this account** for a personal or single-org deployment.
   - **Any account** if you plan to offer Guardian as a multi-tenant service.

6. Click **Create GitHub App**.

7. On the App settings page, scroll to **Private keys** and click
   **Generate a private key**. Save the downloaded `.pem` file securely —
   this is the credential Guardian uses to authenticate API calls.

8. Note the **App ID** shown at the top of the settings page.

---

## 2. Install the App

1. On the App settings page click **Install App**.
2. Choose the account (user or org) and select which repositories the App can
   access — either **All repositories** or a specific list.
3. Click **Install**.

After installation GitHub shows an installation ID in the URL
(`/installations/<installation_id>`). Guardian discovers this automatically
during Connection validation, so you do not need to copy it manually.

---

## 3. Add a GitHub App Connection in Guardian

1. Open the Guardian dashboard → **Profiles → GitHub Apps** tab.
2. Click **Add GitHub App**.
3. Enter:
   - **Name**: a friendly label (e.g. `Guardian Sandbox`)
   - **App ID**: the numeric ID from the App settings page
   - **Private key**: paste the contents of the `.pem` file
4. Click **Validate App** — Guardian generates a JWT, discovers the installation,
   and checks required permissions.
5. Review the discovered installation (account, repositories, permissions) and
   click **Save Connection**.

The Connection starts in `unknown` health state and moves to `healthy` once
Guardian confirms it can mint installation tokens.

---

## 4. Link a Repository

1. In the Guardian dashboard → **Profiles → Repositories** tab, click
   **Link Repository**.
2. Fill in:
   - **Owner / Repo**: `owner/repo`
   - **Profile**: the Guardian Profile that defines review policy
   - **GitHub App connection**: select the Connection from step 3
   - **Auto-review enabled**: toggle on to start reviews automatically on
     `pull_request` webhooks
   - **Require guardian/review check**: keep enabled (recommended) — this
     adds `guardian/review` as a required merge check
3. Click **Link Repository**.

Guardian immediately calls the GitHub branch protection API to add
`guardian/review` as a required status check on the default branch.

---

## 5. Merge Gate

When **Require guardian/review check** is enabled, Guardian calls
`ensure_required_review_check()` on the linked repo. This adds `guardian/review`
to the branch protection or ruleset **additively** — it does not touch existing
required checks, review requirements, bypass actors, or admin settings.

The check context is always `guardian/review`. You can see the current gate
state in the **Repositories** tab (column: **Merge gate**) — values are
`enforced` or `missing`. The **Fix gate** action re-runs enforcement.

PRs cannot merge until Guardian posts a green `guardian/review` status.

---

## 6. @guardian ChatOps

Guardian watches `issue_comment` webhooks for commands in PR conversations:

| Comment | Action |
|---|---|
| `@guardian` | Queue a first review (if no review exists) or a re-review |
| `@guardian re-review` | Queue a focused re-review of existing findings |
| `@pr-guardian` | Alias for `@guardian` |
| `@pr-guardian re-review` | Alias for `@guardian re-review` |

When Guardian receives a command it immediately reacts with 👀 (`eyes`) to
acknowledge receipt, then starts the review in the background.

---

## 7. Sticky Guidance Comment

Guardian maintains a single top-level PR comment identified by a hidden HTML
marker (`<!-- guardian-guidance -->`). The comment is created when Guardian
first processes a PR and updated throughout the PR's lifetime.

Example:

```
<!-- guardian-guidance -->
Guardian is watching this PR.

Latest review: pending
View Guardian review: https://guardian.example.com/reviews/abc123
Need another pass? Comment `@guardian`.
```

Guardian searches for the marker if its stored comment ID becomes stale (e.g.
the comment was deleted) and recreates the comment when absent.

---

## 8. Deterministic Sandbox E2E

`scripts/github-app-e2e.sh` is an opt-in harness that validates the full
GitHub App review flow against `esbenwiberg/pr-guardian-e2e` without needing
real LLM output or live webhook delivery.

```bash
# Check prerequisites (no network, always exits 0)
bash scripts/github-app-e2e.sh --check

# Full run
export GUARDIAN_E2E_GITHUB_APP_ID=123456
export GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE=/path/to/guardian-app.pem
bash scripts/github-app-e2e.sh
```

The harness uses a **deterministic fake LLM provider** (`GUARDIAN_LLM_PROVIDER=fake`)
so no Anthropic or OpenAI credentials are required. See
[docs/github-app-e2e.md](github-app-e2e.md) for full details.

> **Note**: This script is sandbox-only. It resets branch protection on
> `esbenwiberg/pr-guardian-e2e` freely. Do not point it at a production repo.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_WEBHOOK_SECRET` | Yes (GitHub integration) | Shared secret for verifying GitHub webhook signatures |
| `GUARDIAN_DEV_ADMIN` | Dev only | Set to `1` to bypass admin auth in development |

GitHub App credentials (App ID, private key) are stored encrypted in the
Guardian database via the Connections UI — they are **not** read from
environment variables at runtime.

For ADO: `ADO_PAT` and `ADO_ORG_URL` are still read from the environment.
