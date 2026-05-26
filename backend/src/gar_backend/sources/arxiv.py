"""arXiv public source — keyword search against the arXiv API.

v1 scope: title + abstract + metadata only (no PDF body).

ToU compliance (https://info.arxiv.org/help/api/tou.html):
- "make no more than one request every three seconds, and limit requests
  to a single connection at a time" — enforced via a process-wide single
  ArxivSource instance (singleton) holding the lock + last-request clock.
- "Attempt to circumvent rate limits" is prohibited — do NOT shard across
  ArxivSource instances. ``api/deps.py:get_public_source`` returns a
  module-level singleton that owns the rate-limit clock + connection pool.
- "incorporate a 3 second delay in your code" (user manual §3.1.1.2).

Transient-failure handling (429 and read timeouts):
The arXiv API can be slow (occasional multi-tens-of-seconds responses)
or return 429 when rate-limited. Either failure mode is retried with the
same back-off schedule ``ARXIV_RETRY_DELAYS`` (3 → 6 → 12 s, capped) up
to ``len(ARXIV_RETRY_DELAYS) + 1`` attempts. After that the error is
propagated; the agent loop turns it into an ``is_error`` tool_result and
the LLM can pick a different query without the whole run failing.

Read timeout is 60 s per attempt — slightly above arXiv's typical worst
case so we don't pre-empt a slow-but-successful response.
"""

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlencode

import httpx

from gar_backend.sources.base import SearchResult

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_RATE_LIMIT_SECONDS = 3.0
ARXIV_REQUEST_TIMEOUT_SECONDS = 60.0
USER_AGENT = "gar/0.1 (+https://github.com/norkdev/gar)"

# Back-off schedule shared by 429 and read-timeout retries. Each entry is
# the number of seconds slept BEFORE the next attempt; 1 initial attempt +
# len(...) retries. Doubling sequence, capped via ARXIV_MAX_RETRY_DELAY so
# a single hiccup never blocks for too long.
ARXIV_RETRY_DELAYS: tuple[float, ...] = (3.0, 6.0, 12.0)
ARXIV_MAX_RETRY_DELAY: float = 30.0

ATOM = "{http://www.w3.org/2005/Atom}"


class ArxivSource:
    """arXiv search source. Use one instance per process to satisfy the ToU."""

    name = "arxiv"
    tool_name = "search_arxiv"
    tool_description = (
        "Search arXiv (public preprint repository) by keyword. Returns title, "
        "abstract, authors, publication date, and a canonical URL for each "
        "match. Use this for surveying public literature relevant to the "
        "user's idea."
    )

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=ARXIV_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        self._lock = asyncio.Lock()
        self._last_request_at: float = 0.0

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        url = f"{ARXIV_API}?{urlencode(params)}"
        total_attempts = len(ARXIV_RETRY_DELAYS) + 1

        for attempt in range(total_attempts):
            await self._respect_rate_limit()
            last_attempt = attempt + 1 >= total_attempts
            try:
                resp = await self._client.get(url)
            except httpx.TimeoutException:
                if last_attempt:
                    raise
                await asyncio.sleep(
                    min(ARXIV_RETRY_DELAYS[attempt], ARXIV_MAX_RETRY_DELAY)
                )
                continue
            if resp.status_code != 429:
                resp.raise_for_status()
                return _parse_feed(resp.text)
            if last_attempt:
                resp.raise_for_status()  # propagate the 429
            await asyncio.sleep(min(ARXIV_RETRY_DELAYS[attempt], ARXIV_MAX_RETRY_DELAY))

        # Unreachable: the final attempt either returns or raises above.
        raise RuntimeError("arXiv retry loop fell through (should not happen)")

    async def _respect_rate_limit(self) -> None:
        """Block until ``ARXIV_RATE_LIMIT_SECONDS`` have elapsed since the
        last request from THIS source. Combined with the singleton policy
        in deps.py, this enforces the ToU's process-wide ≥3s spacing.
        """
        async with self._lock:
            now = asyncio.get_running_loop().time()
            wait = ARXIV_RATE_LIMIT_SECONDS - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = asyncio.get_running_loop().time()


def _parse_feed(xml_text: str) -> list[SearchResult]:
    root = ET.fromstring(xml_text)
    results: list[SearchResult] = []
    for entry in root.findall(f"{ATOM}entry"):
        arxiv_id = (entry.findtext(f"{ATOM}id") or "").rsplit("/", 1)[-1]
        results.append(
            SearchResult(
                source_name="arxiv",
                external_id=arxiv_id,
                title=(entry.findtext(f"{ATOM}title") or "").strip(),
                snippet=(entry.findtext(f"{ATOM}summary") or "").strip(),
                authors=tuple(
                    (a.findtext(f"{ATOM}name") or "").strip()
                    for a in entry.findall(f"{ATOM}author")
                ),
                published=_parse_date(entry.findtext(f"{ATOM}published")),
                url=_alternate_link(entry),
            )
        )
    return results


def _alternate_link(entry: ET.Element) -> str:
    for link in entry.findall(f"{ATOM}link"):
        if link.get("rel") == "alternate":
            return link.get("href", "")
    return ""


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
