---
description: Stage, write a Conventional Commits message, and commit the current diff.
---

The project follows Conventional Commits (`type(scope): subject`). Valid
types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `ci`,
`style`, `security`. Breaking change → `type!:` plus a `BREAKING CHANGE:`
footer.

Steps:

1. Run `git status` and `git diff --stat` in parallel. Skim what's changed.
2. Group the changes by concern. If two unrelated concerns are mixed, stop
   and ask whether to split into two commits.
3. Stage with explicit paths (never `git add -A` blindly — it grabs secrets).
4. Write the commit subject:
   - One concern → one type.
   - Subject ≤ 72 chars, imperative ("add", not "added").
   - Body explains *why*, not what. The diff already shows what.
5. Run pre-commit hooks. If they fail, fix and create a NEW commit. Do not
   `--amend` unless the user explicitly asks.

Never commit:
- `.env*` files
- `uv.lock` is tracked — but never commit credentials inside any lockfile.
- Files matching `*-key.pem`, `*-secret*`, `*-token*`.

If anything looks like a secret, stop and surface it.
