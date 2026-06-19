"""LLM provider selection (spec seam #5) + the Bedrock stub's behavior."""

import pytest
from gar_backend.agent.llm import AnthropicLLM, BedrockLLM
from gar_backend.api.deps import make_llm_client


def test_defaults_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    # A key must be resolvable for AsyncAnthropic() to construct.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert isinstance(make_llm_client(), AnthropicLLM)


def test_selects_bedrock_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAR_LLM_PROVIDER", "bedrock")
    # No Anthropic key needed — the Bedrock branch never constructs AsyncAnthropic.
    assert isinstance(make_llm_client(), BedrockLLM)


def test_provider_value_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAR_LLM_PROVIDER", "Bedrock")
    assert isinstance(make_llm_client(), BedrockLLM)


async def test_bedrock_complete_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="seam"):
        await BedrockLLM().complete(system="", messages=[], tools=[], model="m")
