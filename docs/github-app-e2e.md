# Guardian GitHub App E2E Harness

The `scripts/github-app-e2e.sh` script is an opt-in end-to-end harness that
validates the full GitHub App review flow against `esbenwiberg/pr-guardian-e2e`
without depending on real LLM output or live webhook delivery.

## Quick start

```bash
# 1. Check local prerequisites (no network required, always exits 0)
bash scripts/github-app-e2e.sh --check

# 2. Set credentials and run the full harness
export GUARDIAN_E2E_GITHUB_APP_ID=123456
export GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE=/path/to/guardian-app.pem
bash scripts/github-app-e2e.sh
```

## Prerequisites

| Requirement | Notes |
|---|---|
| `python3` | Must be importable and have `pr_guardian` installed (`pip install -e '.[dev]'`) |
| `curl` | HTTP calls to GitHub API and Guardian |
| `openssl` | Generates webhook HMAC signatures |
| `gh` or `GH_TOKEN` | Sandbox actor: creates branches, PRs, and comments |
| `GUARDIAN_E2E_GITHUB_APP_ID` | Numeric GitHub App ID |
| `GUARDIAN_E2E_GITHUB_PRIVATE_KEY` or `GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE` | RSA PEM private key for the App |

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GUARDIAN_E2E_GITHUB_APP_ID` | Yes | GitHub App ID (numeric) |
| `GUARDIAN_E2E_GITHUB_PRIVATE_KEY` | One of two | PEM private key content |
| `GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE` | One of two | Path to PEM private key file |
| `GH_TOKEN` | No | GitHub token for sandbox actor; otherwise uses `gh auth` |
| `GUARDIAN_PUBLIC_URL` | No | Enables live webhook delivery; default is signed replay |
| `GUARDIAN_E2E_KEEP` | No | Set to `1` to keep the test branch and PR after the run |

## How the full run works

The harness exercises the complete GitHub App review flow in twelve steps:

1. **GitHub access check** — verifies sandbox actor can read `esbenwiberg/pr-guardian-e2e`.
2. **Installation discovery** — generates a GitHub App JWT and fetches the installation ID for the sandbox repo.
3. **Webhook secret** — generates a local HMAC secret for signed webhook replay.
4. **Start Guardian** — starts Guardian locally on an available port with `GUARDIAN_LLM_PROVIDER=fake` and `GUARDIAN_DEV_ADMIN=1`.
5. **Reset branch protection** — resets `esbenwiberg/pr-guardian-e2e` branch protection to a clean state (the sandbox repo is the only place the harness resets protection freely).
6. **Create PR** — creates a test branch and opens a PR that includes a file containing the `GUARDIAN_E2E_FINDING` fixture marker.
7. **Configure Guardian** — creates a GitHub App Connection, a Profile, and a repo link through Guardian's API.
8. **Replay `pull_request` webhook** — replays a signed `pull_request` opened event to trigger readiness/auto-review.
9. **Post `@guardian` comment** — the sandbox actor posts `@guardian please review this PR`, then replays a signed `issue_comment` webhook.
10. **Wait for outputs** — polls GitHub for `guardian/review` commit status and the sticky guidance comment (up to 60 seconds).
11. **Validate** — asserts that `guardian/review` was posted; warns if the guidance comment is missing.
12. **Cleanup** — closes the PR and deletes the test branch (skip with `GUARDIAN_E2E_KEEP=1`).

## Deterministic fake LLM

The harness configures Guardian to use the fake LLM provider by setting
`GUARDIAN_LLM_PROVIDER=fake`. No real Anthropic or OpenAI credentials are
required.

The fake provider (`src/pr_guardian/llm/fake.py`) emits deterministic JSON:

- **Pass** — when the user message (diff) does not contain `GUARDIAN_E2E_FINDING`.
- **Warn + one finding** — when the diff contains `GUARDIAN_E2E_FINDING`.

To activate the fake provider via config instead of the env var, add this to
your Guardian config or profile:

```yaml
llm:
  default_provider: fake
  providers:
    fake:
      type: fake
      default_model: fake-deterministic-v1
```

The fake provider is **never** used unless explicitly configured.

## Live webhook mode

By default the harness uses signed webhook replay (no public URL required).
To test live webhook delivery, set `GUARDIAN_PUBLIC_URL` to a public URL where
Guardian can receive GitHub webhooks (e.g. via `ngrok` or a cloud deployment):

```bash
export GUARDIAN_PUBLIC_URL=https://your-tunnel.example.com
bash scripts/github-app-e2e.sh
```

## Keeping test artifacts

To inspect the test branch and PR after the run:

```bash
GUARDIAN_E2E_KEEP=1 bash scripts/github-app-e2e.sh
```

The script prints the branch name and PR number before exiting.

## What the harness does NOT do

- Does not use `gh` as Guardian's runtime GitHub API identity (`gh` is only the sandbox actor).
- Does not run in the default `pytest` suite; `pytest` remains hermetic.
- Does not require real Anthropic/OpenAI keys.
- Does not make live GitHub API calls in `--check` mode.
- Does not auto-merge PRs; authors still click merge.
