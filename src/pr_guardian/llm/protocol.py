from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    """Thin LLM client protocol — ~30 lines, 3 implementations."""

    async def complete(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        response_format: str | None = None,
    ) -> LLMResponse:
        """Send a completion request and return structured response."""
        ...

    @property
    def provider_name(self) -> str:
        """Name of this provider (for logging)."""
        ...
