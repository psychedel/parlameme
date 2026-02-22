"""LLM provider abstraction — simple interface for agent runner.

Supports Anthropic (primary) and Ollama (free/local).
No framework dependency — just async message-in, message-out.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Common types
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single tool call extracted from an LLM response."""

    id: str
    name: str
    args: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    raw: Any = None  # Original provider response for debugging


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    """Minimal provider interface — messages in, response out."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
    ) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Anthropic Claude via official SDK.

    Default model: claude-haiku-4-5-20251001 (~$0.10-0.15 per 30-turn game).
    Set ANTHROPIC_API_KEY env var.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_tokens: int = 1024,
    ):
        self.model = model or os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
        self.max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError("anthropic package required: uv add anthropic")
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
    ) -> LLMResponse:
        client = self._get_client()

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _convert_tools_to_anthropic(tools)

        response = await client.messages.create(**kwargs)

        # Extract content and tool calls
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, args=block.input)
                )

        return LLMResponse(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            raw=response,
        )


def _convert_tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert MCP tool schemas to Anthropic tool format."""
    result = []
    for t in tools:
        tool_def: dict[str, Any] = {
            "name": t["name"],
            "description": t.get("description", ""),
        }
        schema = t.get("inputSchema")
        if schema:
            tool_def["input_schema"] = schema
        else:
            tool_def["input_schema"] = {"type": "object", "properties": {}}
        result.append(tool_def)
    return result


# ---------------------------------------------------------------------------
# Ollama provider (free, local)
# ---------------------------------------------------------------------------


class OllamaProvider:
    """Local Ollama for free testing. Uses /api/chat endpoint.

    Set OLLAMA_URL env var (default: http://localhost:11434).
    """

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str | None = None,
    ):
        self.model = model
        self.base_url = (
            base_url or os.environ.get("OLLAMA_URL", "http://localhost:11434")
        ).rstrip("/")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
    ) -> LLMResponse:
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx required for Ollama provider")

        # Build Ollama messages format
        ollama_messages = []
        if system:
            ollama_messages.append({"role": "system", "content": system})
        for msg in messages:
            ollama_messages.append(
                {
                    "role": msg.get("role", "user"),
                    "content": _extract_text(msg),
                }
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = _convert_tools_to_ollama(tools)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        msg = data.get("message", {})
        content = msg.get("content", "")
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", {})
            # Ollama may return arguments as a JSON string instead of a dict
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except (json.JSONDecodeError, TypeError):
                    raw_args = {}
            tool_calls.append(
                ToolCall(
                    id=f"ollama-{len(tool_calls)}",
                    name=fn.get("name", ""),
                    args=raw_args if isinstance(raw_args, dict) else {},
                )
            )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason="stop",
            raw=data,
        )


def _convert_tools_to_ollama(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert MCP tool schemas to Ollama/OpenAI function format."""
    result = []
    for t in tools:
        result.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get(
                        "inputSchema", {"type": "object", "properties": {}}
                    ),
                },
            }
        )
    return result


def _extract_text(msg: dict[str, Any]) -> str:
    """Extract text content from a message dict."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_provider(
    provider_type: str = "anthropic",
    model: str | None = None,
    **kwargs: Any,
) -> LLMProvider:
    """Create an LLM provider by type name.

    Args:
        provider_type: "anthropic" or "ollama"
        model: Model name override
        **kwargs: Additional provider-specific arguments
    """
    match provider_type:
        case "anthropic":
            return AnthropicProvider(model=model, **kwargs)
        case "ollama":
            return OllamaProvider(model=model or "llama3.2", **kwargs)
        case _:
            raise ValueError(f"Unknown provider type: {provider_type}")
