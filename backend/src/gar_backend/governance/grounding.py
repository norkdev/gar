"""Validator: every assertion cites a retrieved source.

Citations follow the format ``[source_name:external_id]``. The validator
parses these from text and checks them against a list of ``SearchResult``
records (the evidence the agent received from its retrieval tools). If a
citation has no matching evidence, it is flagged as fabricated.

v1 does NOT enforce "every sentence must cite". It validates:
1. Every citation present in the text refers to a SearchResult in the evidence.
2. (Reported) whether the text contains any citations at all.
3. (Reported) which retrieved results were not cited — useful for diagnosing
   whether the agent under-used its retrievals.

The ``source_name`` in each citation is taken verbatim from the value the
retrieval source emits in its results; this validator is source-agnostic.

The caller (agent loop) decides what to do with violations: re-prompt the LLM,
fail the run, etc. This module is a pure validator: no I/O, no side effects.

Source-name convention: identifiers are alphanumeric / underscore / hyphen,
no spaces. Sources whose display name has a space (e.g. "web search") use
an underscored identifier (e.g. "web_search") in the code and in citations.
"""

import re
from dataclasses import dataclass

from gar_backend.sources.base import SearchResult

# `[source_name:external_id]` — source_name is ascii / digit / underscore /
# hyphen; external_id excludes whitespace and `]`.
CITATION_PATTERN = re.compile(r"\[([A-Za-z][A-Za-z0-9_-]*):([^\]\s]+)\]")


@dataclass(frozen=True)
class Citation:
    """A parsed citation reference from text."""

    source_name: str
    external_id: str
    raw: str
    span: tuple[int, int]


@dataclass(frozen=True)
class GroundingReport:
    """Result of validating grounded text against an evidence set."""

    citations: tuple[Citation, ...]
    unknown_citations: tuple[Citation, ...]
    unused_evidence: tuple[str, ...]
    has_citations: bool
    is_valid: bool


def extract_citations(text: str) -> list[Citation]:
    """Parse ``[source_name:external_id]`` references from ``text``."""
    return [
        Citation(
            source_name=match.group(1),
            external_id=match.group(2),
            raw=match.group(0),
            span=match.span(),
        )
        for match in CITATION_PATTERN.finditer(text)
    ]


def validate(text: str, evidence: list[SearchResult]) -> GroundingReport:
    """Check every citation in ``text`` refers to a SearchResult in ``evidence``.

    ``is_valid`` is True iff there are no unknown (fabricated) citations.
    """
    citations = extract_citations(text)
    evidence_keys = {(r.source_name, r.external_id) for r in evidence}
    unknown = tuple(
        c for c in citations if (c.source_name, c.external_id) not in evidence_keys
    )
    cited_keys = {(c.source_name, c.external_id) for c in citations}
    unused = tuple(
        r.external_id
        for r in evidence
        if (r.source_name, r.external_id) not in cited_keys
    )
    return GroundingReport(
        citations=tuple(citations),
        unknown_citations=unknown,
        unused_evidence=unused,
        has_citations=bool(citations),
        is_valid=not unknown,
    )
