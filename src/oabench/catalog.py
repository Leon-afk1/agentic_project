"""In-memory view of a snapshot: lookup, filtered search, themes, organisations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from oabench.retrieval.bm25 import BM25Index
from oabench.retrieval.text import fold, tokenize
from oabench.snapshot.records import Dataset

# The 19 ISO 19115 topic categories, with the French labels of the standard.
ISO_TOPICS: dict[str, str] = {
    "farming": "Agriculture",
    "biota": "Biote",
    "boundaries": "Limites",
    "climatologyMeteorologyAtmosphere": "Climatologie, météorologie, atmosphère",
    "economy": "Économie",
    "elevation": "Altitude",
    "environment": "Environnement",
    "geoscientificInformation": "Informations géoscientifiques",
    "health": "Santé",
    "imageryBaseMapsEarthCover": "Imagerie, cartes de base, occupation des terres",
    "intelligenceMilitary": "Renseignement, secteur militaire",
    "inlandWaters": "Eaux intérieures",
    "location": "Localisation",
    "oceans": "Océans",
    "planningCadastre": "Planification, cadastre",
    "society": "Société",
    "structure": "Structure",
    "transportation": "Transport",
    "utilitiesCommunication": "Services d'utilité publique, communication",
}

# Field weights: a term in the title counts three times, in a keyword twice.
TITLE_WEIGHT = 3
KEYWORD_WEIGHT = 2


@dataclass(frozen=True)
class Hit:
    dataset: Dataset
    score: float


def index_tokens(dataset: Dataset) -> list[str]:
    return (
        tokenize(dataset.title) * TITLE_WEIGHT
        + tokenize(" ".join(dataset.keywords)) * KEYWORD_WEIGHT
        + tokenize(dataset.abstract)
        + tokenize(" ".join(dataset.organizations))
    )


class Catalog:
    def __init__(self, datasets: Sequence[Dataset]) -> None:
        self.datasets = list(datasets)
        self._by_uuid = {d.uuid: i for i, d in enumerate(self.datasets)}
        self._index = BM25Index([index_tokens(d) for d in self.datasets])
        self._folded_orgs = [[fold(o) for o in d.organizations] for d in self.datasets]

    def __len__(self) -> int:
        return len(self.datasets)

    def get(self, uuid: str) -> Dataset | None:
        position = self._by_uuid.get(uuid)
        return None if position is None else self.datasets[position]

    def search(
        self,
        query: str,
        *,
        topic: str | None = None,
        organization: str | None = None,
        limit: int = 5,
    ) -> tuple[list[Hit], int]:
        """Return the best hits and the total number of matching datasets."""
        allowed: set[int] | None = None
        if topic is not None or organization is not None:
            needle = fold(organization) if organization else None
            allowed = {
                i
                for i, d in enumerate(self.datasets)
                if (topic is None or topic in d.topics)
                and (needle is None or any(needle in org for org in self._folded_orgs[i]))
            }
        ranked = self._index.search(tokenize(query), len(self.datasets), allowed=allowed)
        hits = [Hit(self.datasets[i], score) for i, score in ranked[:limit]]
        return hits, len(ranked)

    def topic_counts(self) -> dict[str, int]:
        counts = Counter(t for d in self.datasets for t in d.topics)
        return {key: counts.get(key, 0) for key in ISO_TOPICS}

    def organizations(self, query: str | None = None) -> list[tuple[str, int]]:
        """(name, dataset count), most datasets first, optionally filtered by substring."""
        counts = Counter(o for d in self.datasets for o in d.organizations)
        needle = fold(query) if query else None
        rows = [(n, c) for n, c in counts.items() if needle is None or needle in fold(n)]
        return sorted(rows, key=lambda row: (-row[1], row[0]))
