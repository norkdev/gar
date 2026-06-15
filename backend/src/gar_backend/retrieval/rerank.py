"""Rerank candidate sources by relevance to the concept.

spec §5 frames retrieval techniques (semantic search, rerank, keyword) as
swappable tools inside the loop, to be compared in a later evaluation phase.
``Reranker`` is that swap point. v1 ships ``BM25Reranker`` — a dependency-free
lexical reranker scored over the candidate pool itself. Embedding- or
LLM-based rerankers can implement the same Protocol later without touching the
agent loop.

Why rerank at all: arXiv (and public sources generally) do not return results
in concept-relevance order, so a relevant prior work can sit deep in the pool.
Ordering the pool by relevance puts the work a human is most likely to care
about at the top — and lets a downstream cap (e.g. the MCP candidate limit)
drop the low-relevance tail rather than an arbitrary slice.
"""

from __future__ import annotations

import math
import re
from typing import Any, Protocol

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A light stoplist. BM25's IDF already down-weights ubiquitous terms, so this
# only removes the most common function words that would otherwise inflate
# document lengths.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "be",
        "by",
        "as",
        "at",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "from",
        "into",
        "than",
        "then",
        "which",
        "such",
        "can",
        "may",
        "we",
        "their",
        "they",
        "based",
        "using",
        "use",
    }
)


def _tokenize(text: str) -> list[str]:
    return [
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) > 1 and tok not in _STOPWORDS
    ]


def _candidate_text(candidate: dict[str, Any]) -> str:
    """The text BM25 scores: title plus abstract/snippet."""
    return f"{candidate.get('title', '')} {candidate.get('snippet', '')}"


class Reranker(Protocol):
    """Reorder candidates by descending relevance to ``query``.

    Pure and order-stable: equal-scoring candidates keep their input order, so
    a no-signal rerank is a no-op.
    """

    def rank(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...


class BM25Reranker:
    """Okapi BM25 over the candidate pool as the corpus.

    The query is the concept text; each candidate's title+abstract is a
    document. IDF is computed across the candidates being ranked, so terms that
    are common *within this pool* are down-weighted — exactly the terms that
    don't discriminate relevance among these candidates.
    """

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    def rank(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        docs = [_tokenize(_candidate_text(c)) for c in candidates]
        n_docs = len(docs)
        avgdl = sum(len(d) for d in docs) / n_docs or 1.0

        doc_freq: dict[str, int] = {}
        for tokens in docs:
            for term in set(tokens):
                doc_freq[term] = doc_freq.get(term, 0) + 1
        idf = {
            term: math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            for term, df in doc_freq.items()
        }

        query_terms = set(_tokenize(query))
        scores = [self._score(tokens, query_terms, idf, avgdl) for tokens in docs]

        # Stable sort by descending score: ties keep input order, so a pool
        # with no matching terms comes back unchanged.
        order = sorted(range(n_docs), key=lambda i: -scores[i])
        return [candidates[i] for i in order]

    def _score(
        self,
        tokens: list[str],
        query_terms: set[str],
        idf: dict[str, float],
        avgdl: float,
    ) -> float:
        tf: dict[str, int] = {}
        for term in tokens:
            tf[term] = tf.get(term, 0) + 1
        dl = len(tokens)
        score = 0.0
        for term in query_terms:
            freq = tf.get(term)
            if not freq:
                continue
            denom = freq + self.k1 * (1 - self.b + self.b * dl / avgdl)
            score += idf.get(term, 0.0) * (freq * (self.k1 + 1)) / denom
        return score
