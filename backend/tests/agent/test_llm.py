"""agent/llm unit tests. Anthropic client is mocked end-to-end."""

from unittest.mock import AsyncMock, MagicMock

from gar_backend.agent.llm import (
    AnthropicLLM,
    Message,
    ToolDefinition,
    ToolUse,
    _parse_response,
)


def _block(type_: str, **kwargs: object) -> MagicMock:
    block = MagicMock()
    block.type = type_
    for k, v in kwargs.items():
        setattr(block, k, v)
    return block


def test_parse_response_extracts_text_blocks() -> None:
    raw = MagicMock()
    raw.content = [_block("text", text="Hello"), _block("text", text="World")]
    raw.stop_reason = "end_turn"
    result = _parse_response(raw)
    assert result.text_blocks == ("Hello", "World")
    assert result.tool_uses == ()


def test_parse_response_extracts_tool_uses() -> None:
    raw = MagicMock()
    raw.content = [
        _block("text", text="thinking..."),
        _block("tool_use", id="t1", name="some_tool", input={"q": "x"}),
    ]
    raw.stop_reason = "tool_use"
    result = _parse_response(raw)
    assert result.tool_uses == (ToolUse(id="t1", name="some_tool", input={"q": "x"}),)
    assert result.text_blocks == ("thinking...",)


def test_parse_response_preserves_stop_reason() -> None:
    raw = MagicMock()
    raw.content = []
    raw.stop_reason = "max_tokens"
    assert _parse_response(raw).stop_reason == "max_tokens"


def test_parse_response_keeps_raw_for_debugging() -> None:
    raw = MagicMock()
    raw.content = []
    raw.stop_reason = "end_turn"
    assert _parse_response(raw).raw is raw


async def test_anthropic_llm_passes_params_to_sdk() -> None:
    mock_response = MagicMock()
    mock_response.content = [_block("text", text="Hi")]
    mock_response.stop_reason = "end_turn"

    mock_client = MagicMock()
    mock_client.messages = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    llm = AnthropicLLM(client=mock_client)
    result = await llm.complete(
        system="You are an agent.",
        messages=[Message(role="user", content=[{"type": "text", "text": "Hi"}])],
        tools=[
            ToolDefinition(
                name="some_tool",
                description="A test tool.",
                input_schema={"type": "object"},
            )
        ],
        model="claude-sonnet-4-6",
    )

    assert result.text_blocks == ("Hi",)
    kwargs = mock_client.messages.create.await_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["system"] == "You are an agent."
    assert kwargs["max_tokens"] == 4096
    assert kwargs["tools"][0]["name"] == "some_tool"
    assert kwargs["messages"][0]["role"] == "user"


async def test_anthropic_llm_max_tokens_override() -> None:
    mock_response = MagicMock()
    mock_response.content = []
    mock_response.stop_reason = "end_turn"

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    llm = AnthropicLLM(client=mock_client)
    await llm.complete(
        system="",
        messages=[],
        tools=[],
        model="x",
        max_tokens=1000,
    )
    assert mock_client.messages.create.await_args.kwargs["max_tokens"] == 1000
