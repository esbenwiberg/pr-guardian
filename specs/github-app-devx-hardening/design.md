# Design - GitHub App DevX Hardening

## Blast Radius

```text
alembic/versions/<new>.py                     - append GitHub App connection/guidance columns
src/pr_guardian/persistence/models.py         - Connection shape, guidance comment tracking
src/pr_guardian/persistence/storage.py        - CRUD, redaction, token/secret removal, guidance helpers
src/pr_guardian/persistence/crypto.py         - reuse encryption for private keys
src/pr_guardian/api/profiles.py               - GitHub App Connection API, repo-link gate enforcement
src/pr_guardian/platform/factory.py           - remove GitHub PAT/env fallback, resolve App Connections
src/pr_guardian/platform/github.py            - installation auth, comments, reactions, branch gates
src/pr_guardian/platform/protocol.py          - guidance/comment/reaction/gate methods
src/pr_guardian/core/readiness.py             - App adapter resolution, pending guidance update
src/pr_guardian/core/orchestrator.py          - inline default, sticky guidance, formal approval gate
src/pr_guardian/core/github_chatops.py        - @guardian commands, eyes reaction, first review
src/pr_guardian/core/pr_sync.py               - broad sync through GitHub App Connections
src/pr_guardian/api/webhooks.py               - command payload remains signed and routed
src/pr_guardian/llm/factory.py                - deterministic fake provider for E2E/dev harness
src/pr_guardian/llm/fake.py                   - deterministic fake LLM implementation
src/pr_guardian/dashboard/profiles.html       - GitHub Apps tab, gate state, repo-link fields
src/pr_guardian/dashboard/review_detail.html  - postback/guidance/gate state surface
src/pr_guardian/dashboard/static/sidebar.js   - Profiles navigation remains discoverable
tests/                                        - unit, integration, browser, and E2E tests
scripts/github-app-e2e.sh                     - sandbox E2E entrypoint
docs/ and CLAUDE.md                           - GitHub App setup and token cleanup
```

## Seams

**Seam 1 - Connection storage -> GitHub auth**
Brief 01 owns the persisted GitHub App Connection shape and secret redaction.
Brief 02 owns the runtime auth helper that turns a Connection into cached
installation tokens.

**Seam 2 - Profile/repo-link API -> platform gate**
Brief 03 owns API/UI flows that validate a GitHub App Connection and add the
`guardian/review` required check. It calls platform methods from Brief 02.

**Seam 3 - Review lifecycle -> platform postback**
Brief 04 owns sticky guidance updates, inline default behavior for auto-review,
status deeplinks, and formal approval gating. It depends on Brief 02 adapter
methods and Brief 01 guidance storage.

**Seam 4 - ChatOps -> review runner**
Brief 05 owns command recognition, authorization, idempotency, and reactions.
It invokes existing manual review or re-review paths through stored repo links
and GitHub App adapters.

**Seam 5 - E2E harness -> all product seams**
Brief 06 creates a deterministic opt-in harness. It uses `gh` only as the
sandbox actor and validates Guardian's own GitHub App actor behavior through
real GitHub APIs plus signed webhook replay.

**Seam 6 - Docs cleanup -> runtime contracts**
Brief 07 removes GitHub PAT instructions after the code paths are gone and
documents the exact App permissions and E2E requirements.

## Contracts

### GitHub App Connection DTO

```python
{
    "id": "...",
    "name": "Guardian Sandbox",
    "platform": "github",
    "auth_kind": "github_app",
    "app_id": "12345",
    "app_slug": "guardian",
    "installation_id": "98765",
    "installation_account": "esbenwiberg",
    "installation_target_type": "User | Organization",
    "private_key_fingerprint": "sha256:...",
    "health_status": "unknown | healthy | unhealthy",
    "health_message": "...",
    "permissions": {
        "contents": "read",
        "pull_requests": "write",
        "issues": "write",
        "statuses": "write",
        "checks": "read",
        "administration": "write",
    },
    "sync_enabled": true,
}
```

Raw private keys, installation tokens, JWTs, and encrypted values never leave
storage APIs. Audit diffs use stable redacted markers.

### GitHub App Auth

```python
class GitHubAppCredentials:
    app_id: str
    private_key_pem: str
    installation_id: str

class GitHubInstallationToken:
    token: str
    expires_at: datetime
```

The auth helper generates RS256 app JWTs with `iat`, `exp`, and `iss`, exchanges
them for installation access tokens, caches tokens in memory per installation,
and refreshes before expiry. Callers must not assume token length or format.

### Platform Adapter Additions

```python
async def upsert_guidance_comment(pr: PlatformPR, body: str, marker: str) -> str
async def create_issue_comment_reaction(repo: str, comment_id: str, content: str) -> None
async def ensure_required_review_check(repo: str, branch: str, context: str) -> GateResult
async def get_required_review_check_state(repo: str, branch: str, context: str) -> GateResult
async def get_installation_for_repo(repo: str) -> InstallationMetadata
```

`ensure_required_review_check()` is additive for normal repos. It preserves
existing contexts, checks, review requirements, rules, bypass actors, and admin
settings. The sandbox E2E script may use a separate reset path outside product
code.

### Sticky Guidance Comment

Storage tracks the platform comment ID by `platform + repo + pr_id`.
If storage has no ID or the comment was deleted, Guardian searches issue
comments for `<!-- guardian-guidance -->` and recreates when absent.

The body stays short:

```markdown
<!-- guardian-guidance -->
Guardian is watching this PR.

Latest review: pending | green | changes requested | blocked
View Guardian review: <url>
Need another pass? Comment `@guardian`.
```

### ChatOps Grammar

- `@guardian`
- `@guardian re-review`
- `@pr-guardian`
- `@pr-guardian re-review`

If a completed Guardian review exists for the PR, the command queues focused
re-review. If no review exists and the repo is linked, it queues a first review.
If the repo is not linked or the actor is unauthorized, Guardian updates command
audit state and does not start work.

### E2E Credentials

The harness uses:

- `gh auth` or `GH_TOKEN` for sandbox repo setup and user-like actions.
- `GUARDIAN_E2E_GITHUB_APP_ID`.
- `GUARDIAN_E2E_GITHUB_PRIVATE_KEY` or `GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE`.

The harness discovers installation ID from the sandbox repo. It generates a
local webhook secret for signed replay unless live webhook mode is enabled.

## UX Flows

Approved wireframe:

```text
/profiles

[ Profiles ]                                      [New Profile] [Link Repository]

[ Profiles ] [ Repositories ] [ GitHub Apps ] [ Audit ] [ Managers ]

GitHub Apps tab
name                 installation        health       merge gate       repos        actions
Guardian Sandbox     esbenwiberg         healthy      enforced         1           Validate
Prod App             acme-org            warning      missing in 2     18          Fix gates

[ Add GitHub App ]

Add GitHub App modal
Name
App ID
Private key
[Validate App]

After validation:
Installation        Account        Repositories       Permissions
123456              esbenwiberg    selected/all       PR write, Issues write, Statuses write, Admin write

[Save Connection]


Repositories tab
repo                         profile       app connection      auto-review      merge gate       actions
github:esbenwiberg/demo      Standard      Guardian Sandbox    on               enforced         Fix / Validate
github:acme/service          High Risk     Prod App             on               missing          Fix gate

Link Repository modal
Owner / Repo
Profile
GitHub App connection
Auto-review enabled
Require guardian/review check  [on]
[Link Repository]


/reviews/{id}

Header: review status, PR link, head SHA, Guardian review URL
Postback panel:
- inline comments posted
- sticky guidance comment updated
- guardian/review status posted
- formal approval posted/skipped
- branch gate enforced/missing
```

States:

- Loading: preserve current `/profiles` skeleton/table behavior.
- Empty GitHub Apps: show Add GitHub App action and no token import path.
- Invalid App credentials: save disabled until validation reports an actionable
  error.
- Missing Administration write: Connection can be saved as unhealthy/warning,
  but repo-link auto-review cannot be enabled until merge gate enforcement can
  succeed or the operator explicitly fixes permissions.
- Repo-link gate warning: show `missing` with a `Fix gate` action.
- Review detail postback partial failure: show which platform side effects
  succeeded and which failed.

## Reference Reading

- `CLAUDE.md` - commands, repo layout, layer boundaries, migration rule.
- `docs/decisions/ADR-001-inline-comment-mode-tristate.md` - comment mode.
- `docs/decisions/ADR-007-guardian-owned-profiles-and-connections.md` -
  Profiles, Connections, and repo links are Guardian-owned.
- `docs/decisions/ADR-008-readiness-candidates-are-durable-state-machine-records.md`
  - auto-review starts through readiness candidates.
- `docs/decisions/ADR-009-guardian-clearance-is-separate-from-platform-approval.md`
  - approval side effects require Profile configuration.
- `src/pr_guardian/platform/github.py` - status, comment, inline comment, checks,
  and PR API patterns.
- `src/pr_guardian/core/readiness.py` - pending `guardian/review` from PR open.
- `src/pr_guardian/core/orchestrator.py` - review lifecycle and platform
  side effects.
- `src/pr_guardian/core/github_chatops.py` - current re-review command path.
- GitHub App JWT docs:
  https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app
- GitHub installation token docs:
  https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app
- GitHub issue comments docs:
  https://docs.github.com/en/rest/issues/comments
- GitHub reactions docs:
  https://docs.github.com/en/rest/reactions
- GitHub branch protection docs:
  https://docs.github.com/en/rest/branches/branch-protection
- GitHub rulesets docs:
  https://docs.github.com/en/rest/repos/rules

## Brief Order

1. `01-github-app-connection-data-model`
2. `02-github-installation-token-adapter`
3. `03-github-app-setup-and-merge-gates`
4. `04-review-postback-guidance-and-approvals`
5. `05-chatops-mention-reactions-and-rereview`
6. `06-deterministic-github-app-e2e`
7. `07-docs-and-pat-removal-cleanup`

Briefs 03, 04, and 05 depend on Brief 02. Brief 06 depends on all product
briefs. Brief 07 lands last so docs match the final runtime behavior.

## Decisions

- ADR-010: GitHub App Connections are the only GitHub runtime authentication
  mode.
- ADR-011: Guardian manages the `guardian/review` required merge check
  additively.
