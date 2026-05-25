# AGENTS — quick reference

Guidance for any coding agent (Codex, Claude, Cursor, Aider, Copilot, etc.)
working in this repo lives in [`CLAUDE.md`](./CLAUDE.md).

Why one file: keeps build/test commands, layout, conventions, and safety rules
from drifting between agents. Read [`CLAUDE.md`](./CLAUDE.md) first.

## Fast path

- Install: `pip install -e ".[dev]"`.
- Test: `python -m pytest`.
- Lint/format/typecheck: `ruff check .`, `ruff format .`, `mypy src`.
- Build: `python -m build`.
- Run app locally: `bash scripts/agent-serve.sh`, then check
  `http://localhost:8000/api/health`.
- Agent-fitness audit: `bash scripts/repofit-check.sh --include executed`.

## Boundaries

- `mechanical/` must not import `llm/`.
- `decision/` must stay IO-free.
- `core/` must not import `api/` or `dashboard/`.
- Prompts live in `prompts/<agent>/`; do not inline LLM prompts in Python.
- Migrations are append-only; add a new numbered migration after merge.

`CLAUDE.md` remains the source of truth for the full command table, layout, and
runtime notes.
