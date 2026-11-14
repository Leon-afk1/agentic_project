from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import httpx
import numpy as np

from oabench.catalog import Catalog
from oabench.portal.http import CachedClient, RateLimiter, ResponseCache
from oabench.retrieval.engines import (
    BM25Engine,
    EmbeddingEngine,
    Matrix,
    PortalEngine,
    embedding_text,
)
from oabench.snapshot.records import Dataset

VOCABULARY = ["velo", "ecole", "eau", "recharge", "stationnement"]


class FakeEmbedder:
    """Bag of five words: enough to test the search mechanics without a model."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> Matrix:
        self.calls += 1
        rows = [[float(text.lower().count(word)) for word in VOCABULARY] for text in texts]
        return np.asarray(rows, dtype=np.float32)


def docs() -> list[Dataset]:
    return [
        Dataset(uuid="a", title="velo velo", abstract="stationnement"),
        Dataset(uuid="b", title="ecole"),
        Dataset(uuid="c", title="eau", abstract="velo"),
    ]


def test_bm25_engine_returns_uuids_best_first(catalog: Catalog) -> None:
    ranking = BM25Engine(catalog).search("bornes de recharge", 3)
    assert ranking[0][0] == "charging"
    assert all(isinstance(score, float) for _, score in ranking)


def test_embedding_engine_ranks_by_cosine_similarity() -> None:
    engine = EmbeddingEngine(docs(), FakeEmbedder())
    assert [uuid for uuid, _ in engine.search("velo", 3)] == ["a", "c", "b"]
    assert engine.search("velo", 1)[0][1] <= 1.0 + 1e-6
    assert engine.search("   ", 3) == []


def test_embedding_engine_reuses_its_cache(tmp_path: Path) -> None:
    cache = tmp_path / "vectors.npz"
    first = FakeEmbedder()
    EmbeddingEngine(docs(), first, cache=cache)
    second = FakeEmbedder()
    engine = EmbeddingEngine(docs(), second, cache=cache)
    assert (first.calls, second.calls) == (1, 0)
    assert engine.search("ecole", 1)[0][0] == "b"


def test_embedding_cache_is_ignored_for_another_corpus_or_model(tmp_path: Path) -> None:
    cache = tmp_path / "vectors.npz"
    EmbeddingEngine(docs(), FakeEmbedder(), cache=cache)
    other_model = FakeEmbedder("other")
    EmbeddingEngine(docs(), other_model, cache=cache)
    other_corpus = FakeEmbedder("other")
    EmbeddingEngine(docs()[:2], other_corpus, cache=cache)
    assert (other_model.calls, other_corpus.calls) == (1, 1)


def test_embedding_text_puts_title_and_keywords_first() -> None:
    dataset = Dataset(uuid="x", title="Titre", keywords=["k1", "k2"], abstract="a" * 2000)
    text = embedding_text(dataset)
    assert text.startswith("Titre. k1, k2. ")
    assert len(text) < 700


def make_portal(hits: list[dict[str, object]]) -> tuple[PortalEngine, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"hits": {"total": {"value": len(hits)}, "hits": hits}})

    client = CachedClient(
        ResponseCache(":memory:"),
        RateLimiter(0.0),
        transport=httpx.MockTransport(handler),
    )
    return PortalEngine(client, {"a", "b"}, url="https://portal.test/search"), requests


def test_portal_engine_keeps_only_records_of_the_snapshot() -> None:
    engine, requests = make_portal(
        [
            {"_score": 9.0, "_source": {"uuid": "a-map"}},
            {"_score": 8.0, "_source": {"uuid": "b"}},
            {"_score": 7.0, "_source": {"uuid": "a"}},
        ]
    )
    assert engine.search("écoles", 5) == [("b", 8.0), ("a", 7.0)]
    body = json.loads(requests[0].content)
    assert body["size"] == 15  # over-fetch, because maps and services are filtered out
    assert body["query"]["bool"]["must"][0]["query_string"]["default_operator"] == "AND"


def test_portal_engine_neutralises_query_syntax() -> None:
    engine, requests = make_portal([])
    assert engine.search("où sont les écoles ? (Strasbourg) title:x", 5) == []
    sent = json.loads(requests[0].content)["query"]["bool"]["must"][0]["query_string"]["query"]
    assert sent == "où sont les écoles Strasbourg title x"


def test_portal_engine_does_not_call_the_portal_for_an_empty_query() -> None:
    engine, requests = make_portal([])
    assert engine.search(" ?? ", 5) == []
    assert requests == []
