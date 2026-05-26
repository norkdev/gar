"""LLM client abstraction. Anthropic ↔ Bedrock swap point (spec §10 seam #5).

The agent loop talks to `LLMClient` — a Protocol with one `complete()` method.
v1 ships AnthropicLLM backed by anthropic.AsyncAnthropic. Future: a Bedrock
implementation of the same Protocol so the swap is one constructor line.

v1 does not stream. SSE wiring (agent step events → frontend) will be added
when the agent loop is implemented.
"""

from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
from anthropic import AsyncAnthropic


class RateLimitError(Exception):
    """Provider-agnostic rate-limit error raised by `LLMClient` implementations.

    Concrete LLM clients translate their provider's rate-limit exception into
    this so the agent loop can retry without knowing the provider. ``retry_after``
    is in seconds if the provider returned one, else None.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class ToolDefinition:
    """Anthropic-compatible tool schema."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolUse:
    """A tool-use request emitted by the model in one turn."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """One conversational turn (user or assistant).

    `content` matches Anthropic's block shape: a list of text /
    tool_use / tool_result block dicts as appropriate for the role.
    """

    role: str
    content: list[dict[str, Any]]


@dataclass(frozen=True)
class LLMResponse:
    """Decoded response from one LLM turn."""

    text_blocks: tuple[str, ...]
    tool_uses: tuple[ToolUse, ...]
    stop_reason: str
    raw: Any = None


class LLMClient(Protocol):
    """Abstract LLM client. Same shape across providers."""

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolDefinition],
        model: str,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...


class AnthropicLLM:
    """LLMClient backed by anthropic.AsyncAnthropic."""

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        self._client = client or AsyncAnthropic()

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolDefinition],
        model: str,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        try:
            response = await self._client.messages.create(
                model=model,
                system=system,
                max_tokens=max_tokens,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=[
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.input_schema,
                    }
                    for t in tools
                ],
            )
        except anthropic.RateLimitError as exc:
            raise RateLimitError(str(exc), retry_after=_parse_retry_after(exc)) from exc
        return _parse_response(response)


def _parse_retry_after(exc: anthropic.RateLimitError) -> float | None:
    """Extract `retry-after` (seconds) from the rate-limit response, best-effort."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _parse_response(response: Any) -> LLMResponse:
    text_blocks: list[str] = []
    tool_uses: list[ToolUse] = []
    for block in response.content:
        if block.type == "text":
            text_blocks.append(block.text)
        elif block.type == "tool_use":
            tool_uses.append(ToolUse(id=block.id, name=block.name, input=block.input))
    return LLMResponse(
        text_blocks=tuple(text_blocks),
        tool_uses=tuple(tool_uses),
        stop_reason=response.stop_reason,
        raw=response,
    )
