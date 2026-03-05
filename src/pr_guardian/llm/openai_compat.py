from __future__ import annotations

import os

from pr_guardian.llm.protocol import LLMResponse


class OpenAICompatClient:
    """OpenAI-compatible provider (Ollama, vLLM, etc.)."""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "not-needed",
        default_model: str = "llama3.3:70b",
    ):
        self._base_url = base_url
        self._api_key = api_key
        self._default_model = default_model
        self._client: object | None = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
            )
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

        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice else ""
        usage = response.usage

        return LLMResponse(
            content=content or "",
            model=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    @property
    def provider_name(self) -> str:
        return "openai-compatible"
