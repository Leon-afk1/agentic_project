"""Okapi BM25 over an in-memory inverted index.

Written by hand (about 60 lines) rather than imported: the corpus is small
(under 7,000 records) and every term of the formula can be explained.

score(q, d) = sum over query terms t of
    idf(t) * tf(t, d) * (k1 + 1) / (tf(t, d) + k1 * (1 - b + b * len(d) / avg_len))
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Collection, Sequence


class BM25Index:
    def __init__(self, documents: Sequence[Sequence[str]], *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._lengths = [len(doc) for doc in documents]
        self._avg_len = (sum(self._lengths) / len(documents)) if documents else 0.0
        self._postings: dict[str, list[tuple[int, int]]] = {}
        for doc_id, doc in enumerate(documents):
            for term, tf in Counter(doc).items():
                self._postings.setdefault(term, []).append((doc_id, tf))
        n = len(documents)
        # The "+ 1" inside the log keeps idf positive for very common terms.
        self._idf = {
            term: math.log(1 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
            for term, postings in self._postings.items()
        }

    def __len__(self) -> int:
        return len(self._lengths)

    def scores(self, query: Sequence[str]) -> dict[int, float]:
        """Score of every document sharing at least one term with the query."""
        result: dict[int, float] = {}
        for term in dict.fromkeys(query):
            idf = self._idf.get(term)
            if idf is None:
                continue
            for doc_id, tf in self._postings[term]:
                norm = 1 - self.b + self.b * self._lengths[doc_id] / self._avg_len
                gain = idf * tf * (self.k1 + 1) / (tf + self.k1 * norm)
                result[doc_id] = result.get(doc_id, 0.0) + gain
        return result

    def search(
        self, query: Sequence[str], k: int, *, allowed: Collection[int] | None = None
    ) -> list[tuple[int, float]]:
        """Top-k (doc_id, score), best first. Ties are broken by doc_id for determinism."""
        scored = self.scores(query)
        if allowed is not None:
            scored = {doc_id: s for doc_id, s in scored.items() if doc_id in allowed}
        return sorted(scored.items(), key=lambda item: (-item[1], item[0]))[:k]
