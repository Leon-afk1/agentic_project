"""The portal is never contacted here: httpx.MockTransport stands in for it."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from oabench.portal.http import (
    RETRY_DELAYS_S,
    USER_AGENT,
    CachedClient,
    PortalError,
    RateLimiter,
    ResponseCache,
)
from oabench.portal.search import SOURCE_INCLUDES, iter_hits

URL = "https://portal.test/search"


class FakeClock:
    """A clock that only moves when someone sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(
    handler: Any, clock: FakeClock, *, ttl_s: float | None = 3600.0
) -> tuple[CachedClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response: httpx.Response = handler(request)
        return response

    client = CachedClient(
        ResponseCache(":memory:", clock=clock),
        RateLimiter(1.0, clock=clock, sleep=clock.sleep),
        transport=httpx.MockTransport(recording),
        ttl_s=ttl_s,
        sleep=clock.sleep,
    )
    return client, requests


def ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"answer": 42})


def test_rate_limiter_spaces_calls_by_min_interval() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    assert limiter.wait() == 0.0  # first call is free
    assert limiter.wait() == 1.0
    clock.now += 0.4
    assert limiter.wait() == pytest.approx(0.6)
    clock.now += 5.0
    assert limiter.wait() == 0.0  # idle time is not "saved up" as a burst
    assert limiter.wait() == 1.0


def test_second_identical_request_is_served_from_cache() -> None:
    client, requests = make_client(ok, FakeClock())
    assert client.post_json(URL, {"q": "a"}) == {"answer": 42}
    assert client.post_json(URL, {"q": "a"}) == {"answer": 42}
    assert len(requests) == 1
    assert client.stats.cache_hits == 1


def test_different_payloads_are_cached_separately() -> None:
    client, requests = make_client(ok, FakeClock())
    client.post_json(URL, {"q": "a"})
    client.post_json(URL, {"q": "b"})
    assert len(requests) == 2


def test_network_calls_are_rate_limited() -> None:
    clock = FakeClock()
    client, _ = make_client(ok, clock)
    for i in range(3):
        client.post_json(URL, {"q": i})
    assert clock.sleeps == [1.0, 1.0]


def test_cache_entry_expires_after_ttl() -> None:
    clock = FakeClock()
    client, requests = make_client(ok, clock, ttl_s=60.0)
    client.post_json(URL, {"q": "a"})
    clock.now += 61.0
    client.post_json(URL, {"q": "a"})
    assert len(requests) == 2


def test_refresh_bypasses_the_cache() -> None:
    client, requests = make_client(ok, FakeClock())
    client.post_json(URL, {"q": "a"})
    client.post_json(URL, {"q": "a"}, refresh=True)
    assert len(requests) == 2


def test_user_agent_names_the_project_and_holds_no_email() -> None:
    client, requests = make_client(ok, FakeClock())
    client.post_json(URL, {})
    assert requests[0].headers["user-agent"] == USER_AGENT
    assert USER_AGENT.startswith("oabench/")
    assert "@" not in USER_AGENT


def test_server_error_is_retried_then_succeeds() -> None:
    answers = iter([httpx.Response(503), httpx.Response(200, json={"ok": True})])
    clock = FakeClock()
    client, requests = make_client(lambda _: next(answers), clock)
    assert client.post_json(URL, {}) == {"ok": True}
    assert len(requests) == 2
    assert client.stats.retries == 1
    assert RETRY_DELAYS_S[0] in clock.sleeps


def test_gives_up_after_bounded_retries_and_caches_nothing() -> None:
    client, requests = make_client(lambda _: httpx.Response(500), FakeClock())
    with pytest.raises(PortalError, match="giving up"):
        client.post_json(URL, {})
    assert len(requests) == len(RETRY_DELAYS_S) + 1
    with pytest.raises(PortalError):
        client.post_json(URL, {})
    assert client.stats.cache_hits == 0


def test_client_error_is_not_retried() -> None:
    client, requests = make_client(lambda _: httpx.Response(400), FakeClock())
    with pytest.raises(PortalError, match="HTTP 400"):
        client.post_json(URL, {})
    assert len(requests) == 1


def test_timeout_is_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client, requests = make_client(handler, FakeClock())
    with pytest.raises(PortalError, match="ReadTimeout"):
        client.post_json(URL, {})
    assert len(requests) == len(RETRY_DELAYS_S) + 1


def paged_catalog(total: int) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        start, size = body["from"], body["size"]
        hits = [{"_id": f"id-{i:03d}"} for i in range(start, min(start + size, total))]
        return httpx.Response(200, json={"hits": {"total": {"value": total}, "hits": hits}})

    return handler


def test_iter_hits_pages_until_total_is_reached() -> None:
    client, requests = make_client(paged_catalog(5), FakeClock())
    ids = [hit["_id"] for hit in iter_hits(client, url=URL, page_size=2)]
    assert ids == [f"id-{i:03d}" for i in range(5)]
    assert [json.loads(r.content)["from"] for r in requests] == [0, 2, 4]


def test_iter_hits_respects_max_pages() -> None:
    client, requests = make_client(paged_catalog(50), FakeClock())
    assert len(list(iter_hits(client, url=URL, page_size=10, max_pages=2))) == 20
    assert len(requests) == 2


def test_iter_hits_rejects_unexpected_response() -> None:
    client, _ = make_client(lambda _: httpx.Response(200, json={"error": "x"}), FakeClock())
    with pytest.raises(PortalError, match="unexpected"):
        list(iter_hits(client, url=URL))


def test_requested_fields_exclude_contact_information() -> None:
    client, requests = make_client(paged_catalog(1), FakeClock())
    list(iter_hits(client, url=URL))
    includes = json.loads(requests[0].content)["_source"]["includes"]
    assert includes == SOURCE_INCLUDES
    assert not [f for f in includes if "contact" in f.lower() or "email" in f.lower()]
