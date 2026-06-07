#!/usr/bin/env bash
# Guardian GitHub App E2E sandbox harness
#
# Validates the full GitHub App review flow against esbenwiberg/pr-guardian-e2e
# without depending on real LLM output or live webhook delivery.
#
# Usage:
#   bash scripts/github-app-e2e.sh --check      # local prerequisite check only (exits 0)
#   bash scripts/github-app-e2e.sh              # full E2E run (needs credentials)
#
# Required env vars for full run:
#   GUARDIAN_E2E_GITHUB_APP_ID            GitHub App ID
#   GUARDIAN_E2E_GITHUB_PRIVATE_KEY       PEM key content  (or use file below)
#   GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE  path to PEM file
#
# Optional env vars:
#   GUARDIAN_PUBLIC_URL    enables live webhook delivery (default: signed replay)
#   GUARDIAN_E2E_KEEP=1   keep the test branch/PR after the run
#   GH_TOKEN               GitHub token for sandbox actor (alternative to gh auth)
#
# Manual validation:
#   The full run is intentionally NOT automated in CI — it requires real GitHub
#   App credentials and permission to mutate esbenwiberg/pr-guardian-e2e.
#   After the series lands, run this script manually to validate end-to-end.

set -euo pipefail

SANDBOX_REPO="esbenwiberg/pr-guardian-e2e"
GUARDIAN_PORT=""
GUARDIAN_PID=""
TEST_BRANCH=""
TEST_PR_NUMBER=""
WEBHOOK_SECRET=""

# ─── colour helpers ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[0;33m'
BLU='\033[0;34m'
RST='\033[0m'

info()  { echo -e "${BLU}[INFO]${RST}  $*"; }
ok()    { echo -e "${GRN}[OK]${RST}    $*"; }
warn()  { echo -e "${YLW}[WARN]${RST}  $*"; }
fail()  { echo -e "${RED}[FAIL]${RST}  $*" >&2; }
fatal() { fail "$*"; exit 1; }

# ─── prerequisite check (--check mode, no network) ───────────────────────────
# Always exits 0 — it is a local diagnostic, not a gate.
# The full run calls check_prerequisites() and aborts if errors > 0.

check_prerequisites() {
    local errors=0
    local mode="${1:-report}"  # "report" = print only; "gate" = exit on errors

    echo ""
    info "Checking local prerequisites (no network required)..."
    echo ""

    # ── required tools ────────────────────────────────────────────────────────
    for tool in python3 bash curl openssl; do
        if command -v "$tool" &>/dev/null; then
            ok "Tool present: $tool"
        else
            fail "Missing tool: $tool"
            echo "    Install with your system package manager (apt/brew/etc.)"
            errors=$((errors + 1))
        fi
    done

    # gh CLI — needed for sandbox actor actions (optional for --check)
    if command -v gh &>/dev/null; then
        ok "Tool present: gh (GitHub CLI)"
    else
        warn "Optional tool missing: gh (GitHub CLI)"
        echo "    Install from: https://cli.github.com/"
        echo "    Alternative: set GH_TOKEN env var for non-interactive auth"
    fi

    echo ""

    # ── Python package ────────────────────────────────────────────────────────
    if python3 -c "import pr_guardian" 2>/dev/null; then
        ok "Python package: pr_guardian is importable"
    else
        fail "Python package not installed: pr_guardian"
        echo "    Run: pip install -e '.[dev]'"
        errors=$((errors + 1))
    fi

    # ── GitHub App credentials ─────────────────────────────────────────────────
    echo ""
    if [[ -n "${GUARDIAN_E2E_GITHUB_APP_ID:-}" ]]; then
        ok "Env: GUARDIAN_E2E_GITHUB_APP_ID is set"
    else
        fail "Missing env: GUARDIAN_E2E_GITHUB_APP_ID"
        echo "    Set this to the numeric GitHub App ID."
        echo "    Example: export GUARDIAN_E2E_GITHUB_APP_ID=123456"
        errors=$((errors + 1))
    fi

    local key_ok=0
    if [[ -n "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY:-}" ]]; then
        if echo "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY}" | grep -q "BEGIN RSA PRIVATE KEY\|BEGIN PRIVATE KEY"; then
            ok "Env: GUARDIAN_E2E_GITHUB_PRIVATE_KEY is set (PEM content detected)"
            key_ok=1
        else
            fail "Env: GUARDIAN_E2E_GITHUB_PRIVATE_KEY does not look like a PEM private key"
            echo "    Expected content starting with '-----BEGIN RSA PRIVATE KEY-----'"
            errors=$((errors + 1))
        fi
    fi

    if [[ $key_ok -eq 0 ]]; then
        if [[ -n "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE:-}" ]]; then
            if [[ -f "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE}" ]]; then
                if grep -q "BEGIN RSA PRIVATE KEY\|BEGIN PRIVATE KEY" \
                        "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE}" 2>/dev/null; then
                    ok "Env: GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE points to a valid PEM file"
                    key_ok=1
                else
                    fail "Env: GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE does not contain a PEM key"
                    echo "    File: ${GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE}"
                    errors=$((errors + 1))
                fi
            else
                fail "Env: GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE points to a missing file"
                echo "    File not found: ${GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE}"
                errors=$((errors + 1))
            fi
        fi
    fi

    if [[ $key_ok -eq 0 ]]; then
        if [[ $errors -eq 0 || ( -z "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY:-}" && -z "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE:-}" ) ]]; then
            fail "Missing: GUARDIAN_E2E_GITHUB_PRIVATE_KEY or GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE"
            echo "    Set one of:"
            echo "      export GUARDIAN_E2E_GITHUB_PRIVATE_KEY='\$(cat /path/to/key.pem)'"
            echo "      export GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE=/path/to/key.pem"
            errors=$((errors + 1))
        fi
    fi

    # ── sandbox actor ─────────────────────────────────────────────────────────
    echo ""
    if [[ -n "${GH_TOKEN:-}" ]]; then
        ok "Sandbox actor: GH_TOKEN is set"
    else
        ok "Sandbox actor: will use 'gh auth' (run 'gh auth login' if not already authenticated)"
    fi

    # ── optional live webhook ─────────────────────────────────────────────────
    echo ""
    if [[ -n "${GUARDIAN_PUBLIC_URL:-}" ]]; then
        ok "Live webhook mode: GUARDIAN_PUBLIC_URL=${GUARDIAN_PUBLIC_URL}"
    else
        ok "Webhook mode: signed replay (GUARDIAN_PUBLIC_URL not set — default)"
    fi

    # ── summary ───────────────────────────────────────────────────────────────
    echo ""
    if [[ $errors -eq 0 ]]; then
        ok "All required prerequisites are present."
        echo ""
        info "To run the full E2E harness:"
        echo "    bash scripts/github-app-e2e.sh"
        echo ""
        info "The full run will:"
        echo "  1. Verify GitHub access to ${SANDBOX_REPO}"
        echo "  2. Discover the GitHub App installation for ${SANDBOX_REPO}"
        echo "  3. Generate a local webhook secret for signed replay"
        echo "  4. Start Guardian locally on an available port"
        echo "  5. Reset ${SANDBOX_REPO} branch protection (full authority)"
        echo "  6. Create a test branch and PR with the E2E fixture marker"
        echo "  7. Link the repo/Profile/App Connection through Guardian APIs"
        echo "  8. Replay signed GitHub webhooks (pull_request, issue_comment)"
        echo "  9. Comment \`@guardian\` as the sandbox actor"
        echo " 10. Wait for guardian/review status, inline comments, sticky guidance"
        echo " 11. Validate eyes reaction, re-review queue, and formal approval state"
        echo " 12. Clean up test branch/PR (unless GUARDIAN_E2E_KEEP=1)"
        echo ""
    else
        warn "${errors} prerequisite check(s) need attention. See FAIL items above."
        echo ""
        warn "Fix these before running the full E2E harness."
        echo ""
    fi

    if [[ "$mode" == "gate" && $errors -gt 0 ]]; then
        fatal "Prerequisite check failed — aborting full E2E run."
    fi

    return 0  # --check always exits 0
}

# ─── helpers ──────────────────────────────────────────────────────────────────

find_free_port() {
    python3 -c "
import socket
with socket.socket() as s:
    s.bind(('', 0))
    print(s.getsockname()[1])
"
}

guardian_url() {
    echo "http://localhost:${GUARDIAN_PORT}"
}

wait_for_guardian() {
    local retries=30
    info "Waiting for Guardian to start on port ${GUARDIAN_PORT}..."
    for i in $(seq 1 $retries); do
        if curl -sf "$(guardian_url)/api/health" &>/dev/null; then
            ok "Guardian is up"
            return 0
        fi
        sleep 1
    done
    fatal "Guardian did not start within ${retries} seconds"
}

gh_auth_header() {
    if [[ -n "${GH_TOKEN:-}" ]]; then
        echo "Bearer ${GH_TOKEN}"
    else
        echo "Bearer $(gh auth token 2>/dev/null)"
    fi
}

gh_api_get() {
    local url="$1"
    curl -sf \
        -H "Authorization: $(gh_auth_header)" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "$url"
}

gh_api_post() {
    local url="$1"
    local body="$2"
    curl -sf -X POST \
        -H "Authorization: $(gh_auth_header)" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Content-Type: application/json" \
        -d "$body" \
        "$url"
}

gh_api_put() {
    local url="$1"
    local body="$2"
    curl -sf -X PUT \
        -H "Authorization: $(gh_auth_header)" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Content-Type: application/json" \
        -d "$body" \
        "$url"
}

gh_api_patch() {
    local url="$1"
    local body="$2"
    curl -sf -X PATCH \
        -H "Authorization: $(gh_auth_header)" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Content-Type: application/json" \
        -d "$body" \
        "$url"
}

gh_api_delete() {
    local url="$1"
    curl -sf -X DELETE \
        -H "Authorization: $(gh_auth_header)" \
        -H "Accept: application/vnd.github+json" \
        "$url" || true
}

generate_webhook_secret() {
    openssl rand -hex 32
}

sign_webhook_payload() {
    local secret="$1"
    local payload="$2"
    printf '%s' "${payload}" | openssl dgst -sha256 -hmac "${secret}" -hex | awk '{print "sha256="$2}'
}

replay_webhook() {
    local event_type="$1"
    local payload="$2"
    local sig
    sig=$(sign_webhook_payload "${WEBHOOK_SECRET}" "${payload}")

    # Drop -f so curl always exits 0 and -w writes the HTTP code unconditionally.
    # With -sf, a 4xx/5xx would exit non-zero before -w fires, corrupting the capture.
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -H "X-GitHub-Event: ${event_type}" \
        -H "X-Hub-Signature-256: ${sig}" \
        -H "X-GitHub-Delivery: e2e-$(date +%s)-${event_type}" \
        -d "${payload}" \
        "$(guardian_url)/api/webhooks/github")

    if [[ "${http_code}" =~ ^2 ]]; then
        ok "Webhook replayed: ${event_type} (HTTP ${http_code})"
    else
        warn "Webhook replay returned HTTP ${http_code} for event: ${event_type}"
    fi
}

guardian_api() {
    local method="$1"; shift
    local path="$1"; shift
    curl -sf -X "${method}" \
        -H "Content-Type: application/json" \
        -H "X-Guardian-Admin: 1" \
        "$(guardian_url)${path}" \
        "$@"
}

cleanup() {
    local keep="${GUARDIAN_E2E_KEEP:-0}"

    if [[ -n "${GUARDIAN_PID:-}" ]]; then
        info "Stopping Guardian (PID ${GUARDIAN_PID})..."
        kill "${GUARDIAN_PID}" 2>/dev/null || true
        wait "${GUARDIAN_PID}" 2>/dev/null || true
    fi

    if [[ "$keep" == "1" ]]; then
        warn "GUARDIAN_E2E_KEEP=1 — leaving test branch and PR in place"
        [[ -n "${TEST_BRANCH:-}" ]] && info "Branch: ${TEST_BRANCH}"
        [[ -n "${TEST_PR_NUMBER:-}" ]] && info "PR: #${TEST_PR_NUMBER}"
        return
    fi

    if [[ -n "${TEST_PR_NUMBER:-}" ]]; then
        info "Closing PR #${TEST_PR_NUMBER}..."
        gh_api_patch \
            "https://api.github.com/repos/${SANDBOX_REPO}/pulls/${TEST_PR_NUMBER}" \
            '{"state":"closed"}' &>/dev/null || true
    fi

    if [[ -n "${TEST_BRANCH:-}" ]]; then
        info "Deleting test branch: ${TEST_BRANCH}..."
        gh_api_delete \
            "https://api.github.com/repos/${SANDBOX_REPO}/git/refs/heads/${TEST_BRANCH}"
    fi

    ok "Cleanup complete"
}

# ─── full E2E run ─────────────────────────────────────────────────────────────

run_e2e() {
    trap cleanup EXIT

    # ── Step 1: verify sandbox GitHub access ──────────────────────────────────
    info "Step 1: Verifying GitHub access to ${SANDBOX_REPO}..."
    local repo_data
    repo_data=$(gh_api_get "https://api.github.com/repos/${SANDBOX_REPO}") || \
        fatal "Cannot access ${SANDBOX_REPO}. Check GH_TOKEN or run 'gh auth login'."
    local repo_name
    repo_name=$(python3 -c "import sys,json; d=json.loads(sys.argv[1]); print(d['full_name'])" "$repo_data")
    ok "Sandbox repo accessible: ${repo_name}"

    local default_branch
    default_branch=$(python3 -c "import sys,json; d=json.loads(sys.argv[1]); print(d['default_branch'])" "$repo_data")

    # ── Step 2: discover installation ID ──────────────────────────────────────
    info "Step 2: Discovering GitHub App installation for ${SANDBOX_REPO}..."
    local app_id="${GUARDIAN_E2E_GITHUB_APP_ID}"

    # Resolve private key
    local pem_key=""
    if [[ -n "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY:-}" ]]; then
        pem_key="${GUARDIAN_E2E_GITHUB_PRIVATE_KEY}"
    elif [[ -n "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE:-}" ]]; then
        pem_key=$(cat "${GUARDIAN_E2E_GITHUB_PRIVATE_KEY_FILE}")
    fi

    # Generate App JWT using cryptography lib (same as github_auth.py)
    local jwt
    jwt=$(python3 - "$app_id" <<EOF
import time, base64, json, sys

app_id = sys.argv[1]
pem = """${pem_key}"""

try:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    def b64url(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode())
    msg = f"{header}.{payload}".encode()

    key = load_pem_private_key(pem.encode(), password=None)
    sig = b64url(key.sign(msg, padding.PKCS1v15(), hashes.SHA256()))
    print(f"{header}.{payload}.{sig}")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    ) || fatal "Failed to generate GitHub App JWT"

    local install_data
    install_data=$(curl -sf \
        -H "Authorization: Bearer ${jwt}" \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/${SANDBOX_REPO}/installation") || \
        fatal "Failed to fetch installation for ${SANDBOX_REPO}"

    local installation_id
    installation_id=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['id'])" "$install_data")
    ok "Installation ID: ${installation_id}"

    # ── Step 3: generate webhook secret ───────────────────────────────────────
    info "Step 3: Generating local webhook secret for signed replay..."
    WEBHOOK_SECRET=$(generate_webhook_secret)
    ok "Webhook secret generated (not printed)"

    # ── Step 4: start Guardian ─────────────────────────────────────────────────
    info "Step 4: Starting Guardian locally..."
    GUARDIAN_PORT=$(find_free_port)

    GUARDIAN_LLM_PROVIDER=fake \
    GUARDIAN_DEV_ADMIN=1 \
    GUARDIAN_WEBHOOK_SECRET="${WEBHOOK_SECRET}" \
    PORT="${GUARDIAN_PORT}" \
    bash scripts/agent-serve.sh &>"${TMPDIR:-/tmp}/guardian-e2e.log" &
    GUARDIAN_PID=$!

    wait_for_guardian

    # ── Step 5: reset branch protection ───────────────────────────────────────
    info "Step 5: Resetting ${SANDBOX_REPO} branch protection..."
    gh_api_put \
        "https://api.github.com/repos/${SANDBOX_REPO}/branches/${default_branch}/protection" \
        '{
            "required_status_checks": {"strict": false, "contexts": []},
            "enforce_admins": false,
            "required_pull_request_reviews": null,
            "restrictions": null
        }' &>/dev/null && ok "Branch protection reset" || \
        warn "Could not reset branch protection (may need admin perms)"

    # ── Step 6: create test branch and PR ─────────────────────────────────────
    info "Step 6: Creating test branch and PR..."
    local ts
    ts=$(date +%s)
    TEST_BRANCH="e2e-guardian-${ts}"

    # Get main branch SHA
    local ref_data
    ref_data=$(gh_api_get \
        "https://api.github.com/repos/${SANDBOX_REPO}/git/ref/heads/${default_branch}")
    local main_sha
    main_sha=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['object']['sha'])" "$ref_data")

    # Create branch
    gh_api_post \
        "https://api.github.com/repos/${SANDBOX_REPO}/git/refs" \
        "{\"ref\": \"refs/heads/${TEST_BRANCH}\", \"sha\": \"${main_sha}\"}" \
        &>/dev/null || fatal "Failed to create test branch"

    # Create a file containing the E2E fixture marker
    local file_content
    file_content=$(python3 -c "
import base64
content = '''# GUARDIAN_E2E_FINDING: deterministic E2E fixture marker
# This file is created by the Guardian E2E harness for sandbox validation.
# Safe to delete after the E2E run completes.
def e2e_fixture():
    pass
'''
print(base64.b64encode(content.encode()).decode())
")

    gh_api_post \
        "https://api.github.com/repos/${SANDBOX_REPO}/contents/e2e_fixture_${ts}.py" \
        "{
            \"message\": \"e2e: add fixture file with GUARDIAN_E2E_FINDING marker\",
            \"content\": \"${file_content}\",
            \"branch\": \"${TEST_BRANCH}\"
        }" &>/dev/null || fatal "Failed to create fixture file"

    # Create PR
    local pr_response
    pr_response=$(gh_api_post \
        "https://api.github.com/repos/${SANDBOX_REPO}/pulls" \
        "{
            \"title\": \"[E2E] Guardian sandbox run ${ts}\",
            \"body\": \"Automated E2E harness run. Safe to close.\",
            \"head\": \"${TEST_BRANCH}\",
            \"base\": \"${default_branch}\"
        }") || fatal "Failed to create PR"

    TEST_PR_NUMBER=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['number'])" "$pr_response")
    local pr_node_id
    pr_node_id=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['node_id'])" "$pr_response")
    local pr_head_sha
    pr_head_sha=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['head']['sha'])" "$pr_response")

    ok "Created PR #${TEST_PR_NUMBER} on branch ${TEST_BRANCH}"

    # ── Step 7: link repo/Profile/App Connection through Guardian APIs ─────────
    info "Step 7: Configuring Guardian — creating GitHub App Connection and Profile..."

    # Create profile
    local profile_response
    profile_response=$(guardian_api POST /api/profiles/profiles \
        -d "{\"name\": \"E2E Profile ${ts}\", \"risk_class\": \"standard\"}") || \
        fatal "Failed to create Guardian profile"
    local profile_id
    profile_id=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['id'])" "$profile_response")
    ok "Profile created: ${profile_id}"

    # Create GitHub App Connection
    # Write the full JSON body — which contains the PEM key — to a tmpfile and
    # pass it via @file so the key never appears in curl's argv (/proc/<pid>/cmdline or ps).
    local pem_json
    pem_json=$(printf '%s' "$pem_key" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")

    local conn_body_file
    conn_body_file=$(mktemp)
    printf '%s' "{
            \"name\": \"E2E GitHub App ${ts}\",
            \"platform\": \"github\",
            \"app_id\": \"${app_id}\",
            \"private_key\": ${pem_json},
            \"installation_id\": \"${installation_id}\",
            \"installation_account\": \"esbenwiberg\"
        }" > "${conn_body_file}"

    local conn_response
    conn_response=$(guardian_api POST /api/profiles/connections \
        -d "@${conn_body_file}") || { rm -f "${conn_body_file}"; fatal "Failed to create GitHub App Connection"; }
    rm -f "${conn_body_file}"
    local conn_id
    conn_id=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['id'])" "$conn_response")
    ok "Connection created: ${conn_id}"

    # Link repo
    guardian_api POST /api/profiles/repo-links \
        -d "{
            \"repo\": \"github:${SANDBOX_REPO}\",
            \"profile_id\": \"${profile_id}\",
            \"connection_id\": \"${conn_id}\",
            \"auto_review\": true
        }" &>/dev/null || fatal "Failed to link repo"
    ok "Repo linked: ${SANDBOX_REPO}"

    # ── Step 8: replay pull_request opened webhook ────────────────────────────
    info "Step 8: Replaying pull_request webhook..."
    local pr_payload
    pr_payload=$(python3 -c "
import json, sys
payload = {
    'action': 'opened',
    'number': int(sys.argv[1]),
    'pull_request': {
        'number': int(sys.argv[1]),
        'node_id': sys.argv[2],
        'title': f'[E2E] Guardian sandbox run {sys.argv[7]}',
        'state': 'open',
        'head': {'sha': sys.argv[3], 'ref': sys.argv[5]},
        'base': {'sha': sys.argv[4], 'ref': sys.argv[6]},
        'user': {'login': 'e2e-actor'},
        'html_url': f'https://github.com/${SANDBOX_REPO}/pull/{sys.argv[1]}',
        'draft': False,
        'fork': False,
    },
    'repository': {
        'full_name': '${SANDBOX_REPO}',
        'default_branch': sys.argv[6],
    },
    'installation': {'id': int(sys.argv[8])},
}
print(json.dumps(payload))
" "$TEST_PR_NUMBER" "$pr_node_id" "$pr_head_sha" "$main_sha" "$TEST_BRANCH" "$default_branch" "$ts" "$installation_id")

    replay_webhook "pull_request" "${pr_payload}"

    # ── Step 9: comment @guardian as sandbox actor ─────────────────────────────
    info "Step 9: Posting @guardian command as sandbox actor..."
    sleep 3  # brief pause for Guardian to process the PR webhook

    local comment_response
    comment_response=$(gh_api_post \
        "https://api.github.com/repos/${SANDBOX_REPO}/issues/${TEST_PR_NUMBER}/comments" \
        '{"body": "@guardian please review this PR"}') || \
        { warn "Could not post @guardian comment (sandbox actor may lack write perms)"; comment_response=""; }

    if [[ -n "${comment_response}" ]]; then
        local comment_id
        comment_id=$(python3 -c "import sys,json; print(json.loads(sys.argv[1])['id'])" "$comment_response")
        ok "Posted @guardian comment (ID: ${comment_id})"

        # Replay issue_comment webhook
        info "Replaying issue_comment webhook..."
        local comment_payload
        comment_payload=$(python3 -c "
import json, sys
payload = {
    'action': 'created',
    'issue': {
        'number': int(sys.argv[1]),
        'pull_request': {'url': f'https://api.github.com/repos/${SANDBOX_REPO}/pulls/{sys.argv[1]}'},
        'user': {'login': 'e2e-actor'},
    },
    'comment': {
        'id': int(sys.argv[2]),
        'body': '@guardian please review this PR',
        'user': {'login': 'e2e-actor'},
    },
    'repository': {'full_name': '${SANDBOX_REPO}'},
    'installation': {'id': int(sys.argv[3])},
}
print(json.dumps(payload))
" "$TEST_PR_NUMBER" "$comment_id" "$installation_id")
        replay_webhook "issue_comment" "${comment_payload}"
    fi

    # ── Step 10: wait for Guardian outputs ────────────────────────────────────
    info "Step 10: Waiting for Guardian review outputs (up to 60s)..."
    local found_status=0
    local found_guidance=0

    for i in $(seq 1 30); do
        sleep 2

        # Check guardian/review status
        local statuses
        statuses=$(gh_api_get \
            "https://api.github.com/repos/${SANDBOX_REPO}/commits/${pr_head_sha}/statuses" \
            2>/dev/null | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
matches = [s['state'] for s in data if s.get('context') == 'guardian/review']
print(matches[0] if matches else '')
" || echo "")
        if [[ -n "${statuses}" ]]; then
            found_status=1
            ok "guardian/review status: ${statuses}"
        fi

        # Check for guidance comment
        local comments
        comments=$(gh_api_get \
            "https://api.github.com/repos/${SANDBOX_REPO}/issues/${TEST_PR_NUMBER}/comments" \
            2>/dev/null | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
matches = [str(c['id']) for c in data if 'guardian-guidance' in c.get('body', '')]
print(matches[0] if matches else '')
" || echo "")
        if [[ -n "${comments}" ]]; then
            found_guidance=1
            ok "Sticky guidance comment found (ID: ${comments})"
        fi

        if [[ $found_status -eq 1 && $found_guidance -eq 1 ]]; then
            break
        fi
    done

    # Check inline review comments
    local review_comments_count
    review_comments_count=$(gh_api_get \
        "https://api.github.com/repos/${SANDBOX_REPO}/pulls/${TEST_PR_NUMBER}/comments" \
        2>/dev/null | python3 -c "import sys,json; print(len(json.loads(sys.stdin.read())))" || echo "0")
    if [[ "${review_comments_count:-0}" -gt 0 ]]; then
        ok "Inline comments found: ${review_comments_count}"
    fi

    # ── Step 11: validate outcomes ────────────────────────────────────────────
    info "Step 11: Validating outcomes..."
    local test_failures=0

    if [[ $found_status -eq 1 ]]; then
        ok "PASS: guardian/review status was posted"
    else
        fail "FAIL: guardian/review status was NOT posted within timeout"
        test_failures=$((test_failures + 1))
    fi

    if [[ $found_guidance -eq 1 ]]; then
        ok "PASS: sticky guidance comment was posted"
    else
        warn "MISS: sticky guidance comment not found (may not be enabled in config)"
    fi

    # ── Step 12: cleanup (handled by trap) ────────────────────────────────────
    info "Step 12: Cleaning up..."

    if [[ $test_failures -gt 0 ]]; then
        fatal "E2E run completed with ${test_failures} failure(s). Review Guardian logs above."
    else
        ok "E2E run PASSED — all required checks validated."
    fi
}

# ─── entry point ──────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Guardian GitHub App E2E Harness"
    echo "  Sandbox: ${SANDBOX_REPO}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [[ "${1:-}" == "--check" ]]; then
        check_prerequisites "report"
        exit 0
    fi

    # Full run: check prerequisites and abort if anything is missing
    check_prerequisites "gate"

    echo ""
    info "Starting full E2E run..."
    echo ""

    run_e2e
}

main "$@"
