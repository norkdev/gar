"""Command-line interface — run the full agent flow against a local vault.

Mirrors the web UI's three HITL gates as terminal prompts. Designed for
the local-mode workflow where the agent has filesystem access (and the
final report saves back to the vault directory + `.ignore` is updated).

Invocation::

    uv run --package gar-backend gar /path/to/vault

The CLI shares all governance machinery with the HTTP layer — same
agent loop, same audit log, same grounding validator, same RBAC. The
only thing that changes is how user approvals arrive: stdin instead of
HTTP POSTs. The vault-write seam in ``api/gates.approve_report`` is the
same one this CLI uses directly.
"""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv

from gar_backend.agent.loop import AgentContext, create_run, run_until_gate
from gar_backend.agent.tools import register_default_tools
from gar_backend.api.deps import (
    get_access_context,
    get_audit_log_path,
    get_audit_logger,
    get_llm_client,
    get_public_source,
)
from gar_backend.governance.hitl import (
    InvalidTransition,
    RunStatus,
    approve_concept,
    approve_report,
    is_terminal,
    select_sources,
)
from gar_backend.governance.rbac import ToolRegistry
from gar_backend.ideas.search import IdeasSource
from gar_backend.reports.builder import save_report
from gar_backend.state.runs import InMemoryRunStore


def _header(text: str) -> None:
    print()
    print(f"━━━ {text} ━━━")
    print()


def _open_editor(initial: str) -> str:
    """Open ``$EDITOR`` (falls back to ``vim``) on ``initial``; return the result."""
    editor = os.environ.get("EDITOR", "vim")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    ) as tf:
        tf.write(initial)
        tmp_path = Path(tf.name)
    try:
        subprocess.run([editor, str(tmp_path)], check=True)
        return tmp_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)


def _prompt_concept(concept: str) -> str | None:
    """Show the concept; return the (possibly edited) text, or None to abort."""
    _header("Gate 1 — Concept review")
    print(concept)
    print()
    while True:
        choice = (
            input("Approve [a] / Edit [e] / Abort [q] (default a): ").strip().lower()
        )
        if choice in ("", "a", "approve"):
            return concept
        if choice in ("e", "edit"):
            return _open_editor(concept)
        if choice in ("q", "abort"):
            return None
        print("  (invalid — type a, e, or q)")


def _prompt_sources(candidates: list[dict]) -> list[str] | None:
    """Show numbered candidates; return composite IDs of selected, or None to abort."""
    _header(f"Gate 2 — Source selection ({len(candidates)} candidates)")
    if not candidates:
        print("(no candidates returned — adopting zero will produce an honest report)")
        return []
    for i, c in enumerate(candidates, start=1):
        title = c.get("title") or "(no title)"
        meta = f"{c.get('source_name', '?')}:{c.get('external_id', '?')}"
        published = (c.get("published") or "")[:10]
        authors_list = c.get("authors") or []
        authors = ", ".join(authors_list[:3])
        if len(authors_list) > 3:
            authors += " et al."
        print(f"  [{i:2d}] {title}")
        print(f"       {meta}  {published}  {authors}")
        snippet = (c.get("snippet") or "").strip().replace("\n", " ")
        if snippet:
            print(f"       {snippet[:200]}{'…' if len(snippet) > 200 else ''}")
        print()
    while True:
        raw = (
            input("Adopt numbers (e.g. 1,3,5), 'all', 'none', or 'q' to abort: ")
            .strip()
            .lower()
        )
        if raw == "q":
            return None
        if raw == "all":
            return [f"{c['source_name']}:{c['external_id']}" for c in candidates]
        if raw in ("", "none"):
            return []
        try:
            indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            print("  (invalid — try '1,3,5' or 'all' or 'none')")
            continue
        if any(i < 1 or i > len(candidates) for i in indices):
            print(f"  (out of range — must be 1..{len(candidates)})")
            continue
        return [
            f"{candidates[i - 1]['source_name']}:{candidates[i - 1]['external_id']}"
            for i in indices
        ]


def _prompt_report(report: str) -> bool:
    """Show the report; return True if user approves saving to vault."""
    _header("Gate 3 — Final report")
    print(report)
    print()
    while True:
        choice = input("Approve & save to vault [a/q] (default a): ").strip().lower()
        if choice in ("", "a", "approve"):
            return True
        if choice in ("q", "abort"):
            return False
        print("  (invalid — type a or q)")


async def run_cli(vault_path: Path) -> int:
    if not vault_path.exists():
        print(f"error: vault_path does not exist: {vault_path}", file=sys.stderr)
        return 2

    # Wire dependencies. Audit / LLM / public-source come from the same
    # process-wide singletons as the HTTP server so audit.jsonl, the
    # public-source rate-limit clock, and the LLM client all share one
    # state. RunStore is fresh — CLI runs are isolated.
    store = InMemoryRunStore()
    audit = get_audit_logger()
    llm = get_llm_client()
    public_source = get_public_source()
    access = get_access_context()

    ideas = IdeasSource(vault_path)
    registry = ToolRegistry()
    register_default_tools(registry, public_source=public_source, ideas=ideas)
    ctx = AgentContext(
        llm=llm, registry=registry, audit=audit, store=store, access=access
    )

    run_id = str(uuid.uuid4())
    state = create_run(run_id=run_id, tenant_id=access.tenant_id, vault_path=vault_path)
    await store.save(state)

    print(f"Run ID: {run_id}")
    print(f"Vault:  {vault_path}")
    print(f"Audit:  {get_audit_log_path()}")
    print()
    print("Deriving concept...  (first LLM call usually 5-15 s)")

    while True:
        state = await run_until_gate(run_id=run_id, ctx=ctx)

        if state.status == RunStatus.FAILED:
            print(f"\nRun failed: {state.error}", file=sys.stderr)
            return 1
        if is_terminal(state):
            break

        if state.status == RunStatus.AWAITING_CONCEPT_APPROVAL:
            original = state.pending_payload["concept"]
            chosen = _prompt_concept(original)
            if chosen is None:
                print("Aborted.")
                return 130
            try:
                state = approve_concept(
                    state, edited_concept=(chosen if chosen != original else None)
                )
            except InvalidTransition as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            await store.save(state)
            print("Searching related work...  (typically 1-3 min)")

        elif state.status == RunStatus.AWAITING_SOURCE_SELECTION:
            candidates = state.pending_payload.get("candidates", [])
            adopted = _prompt_sources(candidates)
            if adopted is None:
                print("Aborted.")
                return 130
            try:
                state = select_sources(state, adopted_source_ids=adopted)
            except InvalidTransition as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            await store.save(state)
            print(f"Composing report  ({len(adopted)} adopted)…")

        elif state.status == RunStatus.AWAITING_REPORT_APPROVAL:
            report = state.pending_payload.get("report", "")
            if not _prompt_report(report):
                print("Aborted.")
                return 130
            try:
                state = approve_report(state)
            except InvalidTransition as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            saved_path = save_report(content=report, vault_path=vault_path)
            await store.save(state)
            print(f"\nSaved: {saved_path}")
            break

    return 0


def main() -> None:
    # Load .env so ANTHROPIC_API_KEY is picked up (the HTTP server's main.py
    # does this too; we replicate it here since the CLI bypasses FastAPI).
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="gar",
        description="Guided Agentic Retrieval — local-mode CLI.",
    )
    parser.add_argument(
        "vault_path",
        type=Path,
        help="Path to a Markdown vault folder or a single .md file.",
    )
    args = parser.parse_args()

    try:
        code = asyncio.run(run_cli(args.vault_path))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
