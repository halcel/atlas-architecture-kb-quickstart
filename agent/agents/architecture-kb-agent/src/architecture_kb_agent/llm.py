"""Claude via the Anthropic API or an Anthropic-Messages-compatible gateway."""

from __future__ import annotations

import os

from anthropic import Anthropic, AsyncAnthropic, DefaultAsyncHttpxClient, DefaultHttpxClient
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr


def build_llm() -> BaseChatModel:
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is missing; add it to .env or the workspace secrets")
    auth_header = os.environ.get("LLM_AUTH_HEADER", "x-api-key").lower()

    llm = ChatAnthropic(
        model_name=os.environ.get("LLM_MODEL", "claude-opus-5-5"),
        api_key=SecretStr(api_key if auth_header == "x-api-key" else "unused"),
        base_url=os.environ.get("LLM_BASE_URL") or None,
        max_tokens_to_sample=4000,
    )
    if auth_header == "x-api-key":
        return llm

    # Gateways that expect the key in another header (e.g. Authorization: Bearer, api-key).
    def inject(request) -> None:
        request.headers.pop("x-api-key", None)
        request.headers[auth_header] = f"Bearer {api_key}" if auth_header == "authorization" else api_key

    async def inject_async(request) -> None:
        inject(request)

    llm._client = Anthropic(**llm._client_params, http_client=DefaultHttpxClient(event_hooks={"request": [inject]}))
    llm._async_client = AsyncAnthropic(
        **llm._client_params, http_client=DefaultAsyncHttpxClient(event_hooks={"request": [inject_async]})
    )
    return llm
