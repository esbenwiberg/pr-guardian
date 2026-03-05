from __future__ import annotations

import os

from pr_guardian.llm.protocol import LLMResponse


class AnthropicClient:
    """Anthropic Claude provider."""

    def __init__(self, api_key: str | None = None, default_model: str = "claude-sonnet-4-6"):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._default_model = default_model
        self._client: object | None = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def complete(
        self,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        response_format: str | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        model = model or self._default_model

        message = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        content = message.content[0].text if message.content else ""
        return LLMResponse(
            content=content,
            model=model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"
