"""The search engines compared in the benchmark, behind one interface.

Every engine answers the same call: `search(query, k)` returns at most `k`
(uuid, score) pairs, best first. Scores are only comparable within one engine.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from oabench.catalog import Catalog
from oabench.portal.http import CachedClient
from oabench.portal.search import SEARCH_URL
from oabench.retrieval.text import STOPWORDS, fold
from oabench.snapshot.records import Dataset

Ranking = list[tuple[str, float]]
Matrix = NDArray[np.float32]


class Engine(Protocol):
    name: str

    def search(self, query: str, k: int) -> Ranking: ...


class BM25Engine:
    name = "bm25"

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def search(self, query: str, k: int) -> Ranking:
        hits, _ = self._catalog.search(query, limit=k)
        return [(hit.dataset.uuid, hit.score) for hit in hits]


def portal_query(query: str, size: int) -> dict[str, Any]:
    """The query sent by the portal's own search page (captured on 12 Nov 2026).

    Note `default_operator: AND`: every word of the question must match.
    """
    return {
        "from": 0,
        "size": size,
        "sort": [{"_score": "desc"}],
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {
                        "query_string": {
                            "query": query,
                            "default_operator": "AND",
                            "fields": [
                                "resourceTitleObject.langfre^5",
                                "tag.langfre^4",
                                "resourceAbstractObject.langfre^3",
                                "lineageObject.langfre^2",
                                "any.langfre",
                                "uuid",
                            ],
                        }
                    }
                ],
                "must_not": [
                    {
                        "query_string": {
                            "query": "resourceType:featureCatalog AND !resourceType:dataset "
                            "AND !cl_level.key:dataset"
                        }
                    }
                ],
                "filter": [{"terms": {"isTemplate": ["n"]}}],
            }
        },
        "_source": ["uuid"],
    }


# Characters with a meaning in the Lucene query syntax. A user question is plain
# text, so they are replaced by spaces rather than sent as operators.
_LUCENE_SPECIALS = str.maketrans(dict.fromkeys('+-&|!(){}[]^"~*?:\\/<>=', " "))


# Words a user used to catalog search boxes would not type. The list is fixed
# here, before any evaluation, and is not tuned on the questions.
QUESTION_WORDS = frozenset(
    """
    comment combien quel quelle quels quelles pourquoi quand quoi dont
    cherche chercher trouver trouve veux voudrais souhaite existe existent peut
    sont ont fait faire avoir etre ai as a t
    """.split()  # noqa: SIM905
)
_NOT_KEYWORDS = frozenset(fold(word) for word in STOPWORDS | QUESTION_WORDS)


def keywords_only(query: str) -> str:
    """Drop function and question words, keep the other words as typed."""
    return " ".join(word for word in query.split() if fold(word) not in _NOT_KEYWORDS)


class PortalEngine:
    """The live portal search, restricted to records present in the snapshot.

    It queries the real service (through the cache and the rate limiter), so it
    is the only engine that is not strictly reproducible: the portal's content
    and ranking may change after the snapshot date.

    Two variants:
    - `portal`: the question is sent as typed. The portal requires every word to
      match, so a question in natural language often returns nothing;
    - `portal_keywords`: function and question words are removed first, which is
      what a user familiar with catalog search would type. It is the fairer
      baseline for the portal's ranking itself.
    """

    OVERFETCH = 3  # the portal also returns maps and services, which are filtered out

    def __init__(
        self,
        client: CachedClient,
        known_uuids: Collection[str],
        *,
        url: str = SEARCH_URL,
        keywords: bool = False,
    ) -> None:
        self.name = "portal_keywords" if keywords else "portal"
        self._keywords = keywords
        self._client = client
        self._known = known_uuids
        self._url = url
        self.last_call_cached = False

    def search(self, query: str, k: int) -> Ranking:
        cleaned = " ".join(query.translate(_LUCENE_SPECIALS).split())
        if self._keywords:
            cleaned = keywords_only(cleaned)
        if not cleaned:
            return []
        network_calls = self._client.stats.network_calls
        data = self._client.post_json(self._url, portal_query(cleaned, k * self.OVERFETCH))
        self.last_call_cached = self._client.stats.network_calls == network_calls
        ranking: Ranking = []
        for hit in data.get("hits", {}).get("hits", []):
            uuid = hit.get("_source", {}).get("uuid") or hit.get("_id")
            if uuid in self._known:
                ranking.append((str(uuid), float(hit.get("_score") or 0.0)))
        return ranking[:k]


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> Matrix: ...


class FastEmbedEmbedder:
    """Multilingual sentence embeddings on CPU through ONNX Runtime (optional extra)."""

    def __init__(
        self,
        model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        *,
        batch_size: int = 16,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        from fastembed import TextEmbedding  # imported lazily: heavy and optional

        self.name = model
        self._model = TextEmbedding(model_name=model)
        self._batch_size = batch_size
        self._on_progress = on_progress

    def embed(self, texts: Sequence[str]) -> Matrix:
        rows: list[NDArray[Any]] = []
        for vector in self._model.embed(list(texts), batch_size=self._batch_size):
            rows.append(vector)
            if self._on_progress and len(rows) % 500 == 0:
                self._on_progress(len(rows), len(texts))
        return np.asarray(rows, dtype=np.float32)


def embedding_text(dataset: Dataset, max_chars: int = 600) -> str:
    """What is embedded for a record. Title and keywords come first because the
    model truncates long inputs (128 tokens for the default model)."""
    parts = [dataset.title, ", ".join(dataset.keywords[:10]), dataset.abstract[:max_chars]]
    return ". ".join(part for part in parts if part)


def _normalize(matrix: Matrix) -> Matrix:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return (matrix / np.maximum(norms, 1e-12)).astype(np.float32)


class EmbeddingEngine:
    """Exact cosine search over an in-memory matrix: no vector database needed
    for a few thousand records."""

    name = "embeddings"

    def __init__(
        self, datasets: Sequence[Dataset], embedder: Embedder, *, cache: Path | None = None
    ) -> None:
        self._uuids = [d.uuid for d in datasets]
        self._embedder = embedder
        matrix = self._load(cache)
        if matrix is None:
            matrix = _normalize(embedder.embed([embedding_text(d) for d in datasets]))
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                np.savez(cache, matrix=matrix, uuids=np.array(self._uuids), model=embedder.name)
        self._matrix = matrix

    def _load(self, cache: Path | None) -> Matrix | None:
        if cache is None or not cache.exists():
            return None
        with np.load(cache) as stored:
            same_corpus = stored["uuids"].tolist() == self._uuids
            same_model = str(stored["model"]) == self._embedder.name
            if not (same_corpus and same_model):
                return None  # stale cache: another snapshot or another model
            matrix: Matrix = stored["matrix"].astype(np.float32)
            return matrix

    def search(self, query: str, k: int) -> Ranking:
        if not self._uuids or not query.strip():
            return []
        vector = _normalize(self._embedder.embed([query]))[0]
        scores = self._matrix @ vector
        order = np.argsort(-scores, kind="stable")[:k]
        return [(self._uuids[i], float(scores[i])) for i in order]


class HybridEngine:
    """Reciprocal rank fusion of several engines (Cormack et al., 2009).

    Each engine votes with 1 / (k + rank) for the records it returns; votes are
    summed. Only ranks are used, so engines whose scores live on different
    scales (BM25 and cosine similarity) can be combined without calibration.
    The fused score is rank-based: it is not a confidence and is not used to
    decide abstention.
    """

    name = "hybrid"
    RRF_K = 60  # the constant of the original paper; not tuned here

    def __init__(self, engines: Sequence[Engine], *, depth: int = 50) -> None:
        self._engines = list(engines)
        self._depth = depth

    def search(self, query: str, k: int) -> Ranking:
        fused: dict[str, float] = {}
        for engine in self._engines:
            for rank, (uuid, _score) in enumerate(engine.search(query, self._depth), 1):
                fused[uuid] = fused.get(uuid, 0.0) + 1.0 / (self.RRF_K + rank)
        return sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:k]
