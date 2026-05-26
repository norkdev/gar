"""arXiv source unit tests.

Feed parsing is offline (inline Atom fixtures). The async `search` tests use
``httpx.MockTransport`` so no network calls happen and rate-limit / retry
behavior can be exercised deterministically by patching the module-level
delay constants down to ~0.001 seconds.
"""

from datetime import datetime, timezone

import httpx
import pytest

from gar_backend.sources import arxiv as arxiv_module
from gar_backend.sources.arxiv import ArxivSource, _parse_feed


FEED_TWO_ENTRIES = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2301.12345v1</id>
    <updated>2023-02-01T00:00:00Z</updated>
    <published>2023-01-15T00:00:00Z</published>
    <title>Graph Neural Networks for Protein Folding</title>
    <summary>We present a novel GNN architecture for protein folding.</summary>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <link href="http://arxiv.org/abs/2301.12345v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2301.12345v1" rel="related" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2302.00001v1</id>
    <updated>2023-02-15T00:00:00Z</updated>
    <published>2023-02-15T00:00:00Z</published>
    <title>Second Paper</title>
    <summary>Abstract for second paper.</summary>
    <author><name>Carol Doe</name></author>
    <link href="http://arxiv.org/abs/2302.00001v1" rel="alternate" type="text/html"/>
  </entry>
</feed>"""

FEED_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"/>"""

FEED_MISSING_PUBLISHED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2303.99999v1</id>
    <title>No date</title>
    <summary>Abstract.</summary>
    <author><name>Dave</name></author>
    <link href="http://arxiv.org/abs/2303.99999v1" rel="alternate" type="text/html"/>
  </entry>
</feed>"""

FEED_WHITESPACE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/foo/bar/2304.00002v2</id>
    <title>
      Padded Title
    </title>
    <summary>
      Padded summary.
    </summary>
    <author><name>X</name></author>
    <link href="http://x" rel="alternate"/>
  </entry>
</feed>"""


def test_parse_feed_extracts_short_id_from_url() -> None:
    first, _ = _parse_feed(FEED_TWO_ENTRIES)
    assert first.external_id == "2301.12345v1"


def test_parse_feed_extracts_authors_as_tuple_preserving_order() -> None:
    first, _ = _parse_feed(FEED_TWO_ENTRIES)
    assert first.authors == ("Alice Smith", "Bob Jones")


def test_parse_feed_published_is_timezone_aware_datetime() -> None:
    first, _ = _parse_feed(FEED_TWO_ENTRIES)
    assert first.published == datetime(2023, 1, 15, tzinfo=timezone.utc)


def test_parse_feed_url_uses_alternate_link_not_pdf() -> None:
    first, _ = _parse_feed(FEED_TWO_ENTRIES)
    assert first.url == "http://arxiv.org/abs/2301.12345v1"


def test_parse_feed_source_name_is_arxiv() -> None:
    first, _ = _parse_feed(FEED_TWO_ENTRIES)
    assert first.source_name == "arxiv"


def test_parse_feed_empty_returns_empty_list() -> None:
    assert _parse_feed(FEED_EMPTY) == []


def test_parse_feed_missing_published_field_is_none() -> None:
    (entry,) = _parse_feed(FEED_MISSING_PUBLISHED)
    assert entry.published is None


def test_parse_feed_strips_whitespace_in_title_and_summary() -> None:
    (entry,) = _parse_feed(FEED_WHITESPACE)
    assert entry.title == "Padded Title"
    assert entry.snippet == "Padded summary."


def test_parse_feed_id_extraction_takes_last_path_segment() -> None:
    """The id may include arbitrary path; only the last segment is the arXiv ID."""
    (entry,) = _parse_feed(FEED_WHITESPACE)
    assert entry.external_id == "2304.00002v2"


# ---------- async search + 429 retry behavior ----------


def _fast_arxiv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Squash arXiv rate-limit + retry delays so tests run fast."""
    monkeypatch.setattr(arxiv_module, "ARXIV_RATE_LIMIT_SECONDS", 0.001)
    monkeypatch.setattr(
        arxiv_module, "ARXIV_RETRY_DELAYS", (0.001, 0.001, 0.001)
    )
    monkeypatch.setattr(arxiv_module, "ARXIV_MAX_RETRY_DELAY", 0.001)


def _mock_client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        follow_redirects=True,
    )


async def test_search_returns_parsed_results_on_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_arxiv(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FEED_TWO_ENTRIES)

    async with _mock_client(handler) as client:
        results = await ArxivSource(client=client).search("q")
    assert len(results) == 2
    assert results[0].external_id == "2301.12345v1"


async def test_search_retries_on_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ToU minimum 3-sec wait + exponential backoff: once 429 clears, succeed."""
    _fast_arxiv(monkeypatch)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, text="Rate exceeded.")
        return httpx.Response(200, text=FEED_TWO_ENTRIES)

    async with _mock_client(handler) as client:
        results = await ArxivSource(client=client).search("q")
    assert len(calls) == 2
    assert len(results) == 2


async def test_search_propagates_after_repeated_429s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After ``ARXIV_RETRY_DELAYS + 1`` attempts of 429, raise."""
    _fast_arxiv(monkeypatch)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, text="Rate exceeded.")

    async with _mock_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await ArxivSource(client=client).search("q")
        assert excinfo.value.response.status_code == 429
    expected_attempts = len(arxiv_module.ARXIV_RETRY_DELAYS) + 1
    assert len(calls) == expected_attempts


async def test_search_retries_on_read_timeout_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient read-timeout from arXiv is retried on the same back-off as 429."""
    _fast_arxiv(monkeypatch)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("simulated read timeout")
        return httpx.Response(200, text=FEED_TWO_ENTRIES)

    async with _mock_client(handler) as client:
        results = await ArxivSource(client=client).search("q")
    assert len(calls) == 2
    assert len(results) == 2


async def test_search_propagates_after_repeated_read_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After all attempts time out, the timeout propagates."""
    _fast_arxiv(monkeypatch)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("simulated read timeout")

    async with _mock_client(handler) as client:
        with pytest.raises(httpx.ReadTimeout):
            await ArxivSource(client=client).search("q")
    expected_attempts = len(arxiv_module.ARXIV_RETRY_DELAYS) + 1
    assert len(calls) == expected_attempts


async def test_search_does_not_retry_on_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-429 server errors propagate immediately."""
    _fast_arxiv(monkeypatch)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, text="oops")

    async with _mock_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await ArxivSource(client=client).search("q")
    assert len(calls) == 1


async def test_search_respects_rate_limit_between_consecutive_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two back-to-back calls on the same source must wait ≥ rate-limit window."""
    monkeypatch.setattr(arxiv_module, "ARXIV_RATE_LIMIT_SECONDS", 0.05)
    monkeypatch.setattr(arxiv_module, "ARXIV_RETRY_DELAYS", ())
    monkeypatch.setattr(arxiv_module, "ARXIV_MAX_RETRY_DELAY", 0.001)

    import time

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FEED_TWO_ENTRIES)

    async with _mock_client(handler) as client:
        source = ArxivSource(client=client)
        start = time.perf_counter()
        await source.search("a")
        await source.search("b")
        elapsed = time.perf_counter() - start
    # Second call must have waited at least the rate-limit window.
    assert elapsed >= 0.05
