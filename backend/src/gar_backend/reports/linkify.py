"""Convert ``[source_name:external_id]`` citations in a composed report
into Markdown hyperlinks pointing at each source's canonical URL.

Every occurrence of a citation that matches an adopted-evidence record
becomes ``\\[[source:external_id](url)\\]`` — the brackets stay visible
via Markdown escapes so the citation reads the same whether rendered or
not, and the middle is a clickable link.

We considered a two-level scheme (in-text citation → References anchor
→ external URL), but raw-HTML ``<a id>`` anchors are not recognised as
jump targets by many Markdown viewers (VS Code's preview being the most
visible example). Direct external linking works uniformly across viewers
and keeps the implementation small.

URLs come straight from each adopted-evidence record's ``url`` field —
populated by the source's own ``search()``. The linkifier is therefore
source-agnostic; a new ``PublicSource`` requires no changes here as long
as it emits a URL on its ``SearchResult``.
"""

import re
from typing import Any

CITATION_PATTERN = re.compile(r"\[([A-Za-z][A-Za-z0-9_-]*):([^\]\s]+)\]")


def linkify_report(report: str, adopted_evidence: list[dict[str, Any]]) -> str:
    """Return ``report`` with every citation that has a known URL linkified."""
    url_by_key: dict[tuple[str, str], str] = {
        (e["source_name"], e["external_id"]): (e.get("url") or "")
        for e in adopted_evidence
    }

    def repl(m: re.Match[str]) -> str:
        source, ext_id = m.group(1), m.group(2)
        url = url_by_key.get((source, ext_id), "")
        if not url:
            return m.group(0)
        return rf"\[[{source}:{ext_id}]({url})\]"

    return CITATION_PATTERN.sub(repl, report)
