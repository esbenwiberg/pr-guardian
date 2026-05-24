# Contributing

Quick reference for humans and agents.

## Branches & PRs

- Branch off `main`. Naming: `<type>/<short-slug>` (e.g. `feat/inline-comment-dismissals`, `fix/triage-null-diff`).
- One concern per PR. Refactors stay separate from feature work.
- Open a draft early if you want feedback on direction before scope grows.
- PRs run CI (lint, format, typecheck, tests, dep audit) — keep them green
  before requesting review.

## Commit messages

Conventional Commits: `type(scope): subject`.

- `feat` — user-facing change
- `fix` — bug fix
- `refactor` — no behavior change
- `perf` — measurable performance improvement
- `test` — tests only
- `docs` — docs only
- `chore` — tooling / housekeeping
- `ci` — workflow changes
- `style` — formatting only
- `security` — security fix or hardening

Breaking changes get a `!` (`feat!: …`) and a `BREAKING CHANGE:` footer.

## Local loop

```bash
pip install -e ".[dev]"
pre-commit install              # secret + lint hooks
python -m pytest                # 500+ tests, ~1.5s
ruff check . && ruff format .   # lint + format
mypy src                        # typecheck (currently soft)
pip-audit --strict              # dep CVE scan
```

Boot the app: `bash scripts/agent-serve.sh`. Health: `GET /api/health`.

## Where things live

| Want to | Look at |
|---|---|
| Add a feature | Write a `specs/<feature>/` brief first |
| Change a verdict rule | `decision/`, plus probably a new ADR in `docs/decisions/` |
| Add an agent | New dir in `prompts/`, class in `agents/`, wire in `triage/` |
| Add a mechanical check | `mechanical/` — no LLM calls allowed |
| Change DB schema | New numbered migration in `alembic/versions/` |
| Tweak UI | Jinja templates in `src/pr_guardian/dashboard/`, then `npm run build:css` |

See [ARCHITECTURE.md](./ARCHITECTURE.md) for invariants and layer rules, and
[CLAUDE.md](./CLAUDE.md) for the full command list and conventions.

## Tests

- Pytest, `asyncio_mode = auto`. Aim for behavior, not implementation.
- A new agent finding type needs a test that proves the evidence anchor format
  (file + line + quote) round-trips through storage.
- Don't mock the database when an integration test would catch more — the
  in-memory mode exists for exactly this.

## Decisions

Non-trivial architecture choices land as ADRs under `docs/decisions/`. Use the
existing files as a template: Context → Decision → Consequences → Status.

## Reporting issues

Open a GitHub issue with: what you tried, what happened, what you expected.
Logs from `bash scripts/agent-serve.sh` help a lot.
