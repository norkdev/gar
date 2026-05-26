"""CLI smoke tests — argument parsing and the failure paths that don't
require a live LLM. Full end-to-end coverage of the CLI flow would
duplicate the HTTP-level integration tests, since both share the agent
loop; we exercise the CLI-specific seams (argparse, missing vault,
prompt parsing) here.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from gar_backend.cli import _prompt_sources


def test_cli_help_runs_and_mentions_vault_path() -> None:
    """``gar --help`` works and explains the only positional argument."""
    result = subprocess.run(
        [sys.executable, "-m", "gar_backend.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "vault_path" in result.stdout


def test_cli_with_missing_vault_path_arg_exits_nonzero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "gar_backend.cli"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    # argparse writes the usage line to stderr
    assert "vault_path" in result.stderr.lower()


def test_cli_with_nonexistent_vault_returns_exit_code_2(tmp_path: Path) -> None:
    """``gar /does/not/exist`` should report a clear error and exit nonzero."""
    nonexistent = tmp_path / "no-such-vault"
    result = subprocess.run(
        [sys.executable, "-m", "gar_backend.cli", str(nonexistent)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "does not exist" in result.stderr


# ---------- _prompt_sources parser ----------


def test_prompt_sources_parses_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "all")
    candidates = [
        {"source_name": "arxiv", "external_id": "1.1"},
        {"source_name": "arxiv", "external_id": "2.2"},
    ]
    result = _prompt_sources(candidates)
    assert result == ["arxiv:1.1", "arxiv:2.2"]


def test_prompt_sources_parses_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "none")
    candidates = [{"source_name": "arxiv", "external_id": "1.1"}]
    assert _prompt_sources(candidates) == []


def test_prompt_sources_parses_specific_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "1, 3")
    candidates = [
        {"source_name": "arxiv", "external_id": "a"},
        {"source_name": "arxiv", "external_id": "b"},
        {"source_name": "arxiv", "external_id": "c"},
    ]
    assert _prompt_sources(candidates) == ["arxiv:a", "arxiv:c"]


def test_prompt_sources_returns_none_on_q(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")
    candidates = [{"source_name": "arxiv", "external_id": "a"}]
    assert _prompt_sources(candidates) is None


def test_prompt_sources_retries_on_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad input re-prompts; second try succeeds."""
    inputs = iter(["bogus", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    candidates = [{"source_name": "arxiv", "external_id": "a"}]
    assert _prompt_sources(candidates) == ["arxiv:a"]


def test_prompt_sources_retries_on_out_of_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = iter(["99", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    candidates = [{"source_name": "arxiv", "external_id": "a"}]
    assert _prompt_sources(candidates) == ["arxiv:a"]


def test_prompt_sources_empty_input_means_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    candidates = [{"source_name": "arxiv", "external_id": "a"}]
    assert _prompt_sources(candidates) == []


def test_prompt_sources_empty_candidate_list_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the agent returned zero candidates, the prompt short-circuits."""
    # Even if input were provided, no prompt should be issued.
    monkeypatch.setattr("builtins.input", lambda _prompt: "all")
    assert _prompt_sources([]) == []
