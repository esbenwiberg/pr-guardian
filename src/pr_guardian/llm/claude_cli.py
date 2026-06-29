"""Dev-only LLM provider that shells out to the local ``claude`` CLI.

SELF-VALIDATION ONLY — never for production. It lets Guardian produce a *real*
LLM review locally without an Anthropic API key, by running the ``claude`` binary
in single-shot print mode. The factory refuses to build it outside a dev
environment (see ``factory._claude_cli_allowed``).

Why it must stay out of prod:
  - it executes an external binary from the server process (code-exec surface),
  - one subprocess per agent call (no throughput / OOM control),
  - authenticates as the CLI's logged-in account (no managed keys / billing),
  - couples prod to a CLI version + its stdout contract.

Safety bounds applied here: ``--max-turns 1`` (no agentic loop — the model
answers in one turn or not at all, so it cannot both call a tool and return a
result), our agent system prompt *replaces* Claude Code's default via
``--system-prompt``, and the process runs in a throwaway working directory so a
stray tool call can't see the repo.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile

import structlog

from pr_guardian.llm.protocol import LLMResponse

log = structlog.get_logger()


class ClaudeCLIClient:
    """LLMClient backed by ``claude -p --output-format json``."""

    def __init__(self, default_model: str = "", timeout_seconds: int = 180):
        self._default_model = default_model
        self._timeout_seconds = timeout_seconds

    async def complete(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        response_format: str | None = None,
    ) -> LLMResponse:
        binary = shutil.which("claude")
        if not binary:
            raise RuntimeError(
                "claude CLI not found on PATH — the claude-cli provider needs it installed."
            )

        cmd = [
            binary,
            "-p",
            "--output-format",
            "json",
            "--max-turns",
            "1",
            "--system-prompt",
            system,
        ]
        # Only forward a model when it looks like a real Claude model/alias —
        # config defaults (e.g. fake model names) would otherwise break the CLI.
        chosen = model or self._default_model
        if chosen and (
            "claude" in chosen.lower() or chosen.lower() in ("opus", "sonnet", "haiku")
        ):
            cmd += ["--model", chosen]

        # Throwaway cwd: even a stray single-turn tool call can't reach the repo.
        workdir = tempfile.mkdtemp(prefix="guardian-claude-cli-")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(user.encode()),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"claude CLI timed out after {self._timeout_seconds}s")

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {proc.returncode}: {err.decode(errors='replace')[:500]}"
            )

        try:
            envelope = json.loads(out.decode(errors="replace"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"claude CLI returned non-JSON output: {e}")

        if envelope.get("is_error"):
            raise RuntimeError(f"claude CLI reported an error: {envelope.get('result', '')[:500]}")

        content = envelope.get("result", "") or ""
        usage = envelope.get("usage", {}) or {}
        model_used = self._default_model or "claude-cli"
        model_usage = envelope.get("modelUsage") or {}
        if model_usage:
            model_used = next(iter(model_usage.keys()), model_used)

        return LLMResponse(
            content=content,
            model=model_used,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )

    @property
    def provider_name(self) -> str:
        return "claude-cli"
