#!/usr/bin/env bash
# Conventional Commits gate.
#
# Pre-commit passes the path to the prepared message as $1. Rejects subjects
# that don't match `type(scope)?!?: subject` where type is one of the
# canonical set. Merge commits, revert commits, and fixup/squash autosquashes
# are exempt — those are mechanical and shouldn't be rewritten by the author.
set -euo pipefail

msg_file="${1:?usage: check-commit-msg.sh <commit-msg-file>}"
subject="$(head -n1 "$msg_file")"

# Exempt mechanical subjects.
case "$subject" in
  "Merge "*|"Revert "*|"fixup!"*|"squash!"*|"amend!"*) exit 0 ;;
esac

pattern='^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|breaking|security)(\([^)]+\))?!?:[[:space:]].+$'

if [[ "$subject" =~ $pattern ]]; then
  exit 0
fi

cat >&2 <<EOF
Commit subject does not follow Conventional Commits:

  $subject

Expected: <type>(<scope>)?!?: <subject>
Types:    feat fix docs style refactor perf test build ci chore revert breaking security
Examples:
  feat(api): add /reviews/queue endpoint
  fix(platform/ado): post comment body in request_changes
  docs: clarify auto-approve gate

See CONTRIBUTING.md for the full convention.
EOF
exit 1
