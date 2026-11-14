from __future__ import annotations

import math

import pytest

from oabench.catalog import ISO_TOPICS, Catalog
from oabench.retrieval.bm25 import BM25Index
from oabench.retrieval.text import fold, tokenize


def test_fold_removes_accents_and_case() -> None:
    assert fold("Écoles Élémentaires") == "ecoles elementaires"


def test_tokenize_drops_stopwords_and_folds_plurals() -> None:
    assert tokenize("Où sont les écoles de la ville ?") == ["sont", "ecole", "ville"]
    assert tokenize("réseaux") == tokenize("réseau")


def test_tokenize_keeps_years_intact() -> None:
    assert tokenize("occupation du sol 2018") == ["occupation", "sol", "2018"]


def test_bm25_matches_the_formula_on_a_hand_computed_case() -> None:
    index = BM25Index([["a", "b"], ["a", "a", "c", "d"]], k1=1.5, b=0.75)
    # Term "b": in 1 of 2 documents; document 0 has length 2, average length 3.
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    norm = 1 - 0.75 + 0.75 * 2 / 3
    expected = idf * 1 * 2.5 / (1 + 1.5 * norm)
    assert index.scores(["b"]) == {0: pytest.approx(expected)}


def test_bm25_ranks_rarer_and_more_frequent_terms_higher() -> None:
    index = BM25Index([["velo", "ville"], ["ville", "ville"], ["eau"]])
    assert [doc for doc, _ in index.search(["velo", "ville"], 3)] == [0, 1]


def test_bm25_unknown_term_and_empty_index_return_nothing() -> None:
    assert BM25Index([["a"]]).search(["zzz"], 5) == []
    assert BM25Index([]).search(["a"], 5) == []


def test_bm25_respects_allowed_documents_and_limit() -> None:
    index = BM25Index([["a"], ["a"], ["a"]])
    assert [doc for doc, _ in index.search(["a"], 5, allowed={1, 2})] == [1, 2]
    assert len(index.search(["a"], 1)) == 1


def test_search_finds_by_title_ignoring_accents_and_plural(catalog: Catalog) -> None:
    hits, total = catalog.search("ecole primaire")
    assert hits[0].dataset.uuid == "schools"
    assert total == 1


def test_search_title_match_beats_abstract_match(catalog: Catalog) -> None:
    hits, _ = catalog.search("stationnement vélo")
    assert hits[0].dataset.uuid == "trap"


def test_search_filters_by_topic_and_organization(catalog: Catalog) -> None:
    hits, _ = catalog.search("mobilité", topic="transportation", organization="mulhouse")
    assert [h.dataset.uuid for h in hits] == ["charging"]


def test_search_without_any_match_is_empty(catalog: Catalog) -> None:
    assert catalog.search("prix immobilier Reims") == ([], 0)


def test_topic_counts_cover_the_19_iso_categories(catalog: Catalog) -> None:
    counts = catalog.topic_counts()
    assert len(ISO_TOPICS) == 19
    assert list(counts) == list(ISO_TOPICS)
    assert counts["transportation"] == 2 and counts["oceans"] == 0


def test_organizations_are_sorted_by_size_and_filterable(catalog: Catalog) -> None:
    assert catalog.organizations()[0] == ("Ville de Strasbourg", 2)
    assert [name for name, _ in catalog.organizations("STRASBOURG")] == [
        "Ville de Strasbourg",
        "Eurométropole de Strasbourg",
    ]
