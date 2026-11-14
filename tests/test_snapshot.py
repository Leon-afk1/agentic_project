"""Records below are synthetic: same shapes as the real index, invented content."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oabench.cli import main
from oabench.snapshot.build import (
    DATASETS_FILE,
    build_snapshot,
    load_manifest,
    load_snapshot,
    verify_snapshot,
)
from oabench.snapshot.records import EMAIL_PLACEHOLDER, clean_record, clean_text, is_dataset


def ml(text: str) -> dict[str, str]:
    return {"default": text, "langfre": text}


def hit(uuid: str, **source: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "uuid": uuid,
        "resourceType": ["dataset"],
        "resourceTitleObject": ml(f"Title {uuid}"),
        "changeDate": "2026-01-01T00:00:00Z",
    }
    return {"_id": uuid, "_source": {**base, **source}}


FULL = hit(
    "bike-lanes",
    resourceAbstractObject=ml(
        "<p>Cycle lanes of the <b>city</b>.</p><p>Contact: jane.doe@example.org</p>"
    ),
    tag=[ml("null"), ml("Mobilité"), ml("Vélo"), ml("Mobilité")],
    cl_topic=[{"key": "transportation", "default": "Transport"}],
    OrgForResourceObject=[ml("City A"), ml("Agency B")],
    format=["CSV", "CSV", "GeoJSON"],
    linkUrl="https://data.example.org/bike.csv",
    linkProtocol=["WWW:DOWNLOAD:text/csv"],
    geom=[
        {"type": "Polygon", "coordinates": [[[7.6, 48.5], [7.9, 48.5], [7.9, 48.7], [7.6, 48.5]]]}
    ],
    resourceDate=[
        {"type": "revision", "date": "2025-03-01T00:00:00.000Z"},
        {"type": "publication", "date": "2024-01-01T00:00:00.000Z"},
    ],
    licenseObject=[ml("Licence Ouverte v2.0")],
    MD_LegalConstraintsOtherConstraintsObject=[ml("Licence Ouverte v2.0"), ml("Cite the source")],
    lineageObject=ml("Surveyed in 2024."),
    cl_maintenanceAndUpdateFrequency=[{"key": "annually", "default": "Annuel"}],
    documentStandard="iso19115-3.2018",
    sourceCatalogue="src-1",
    isHarvested="true",
    featureTypes=[
        {
            "attributeTable": [
                {"name": "id", "type": "int", "definition": "Identifier"},
                {"name": "surface", "type": "text", "definition": ""},
                {"name": "id", "type": "int", "definition": "duplicate"},
            ]
        }
    ],
)


def test_clean_text_strips_html_and_keeps_paragraphs() -> None:
    assert clean_text("<p>One&nbsp;two</p><p>three   four</p>") == "One two\n\nthree four"


def test_clean_text_drops_script_content() -> None:
    assert clean_text("before<script>alert('x')</script>after") == "beforeafter"


def test_clean_text_masks_email_addresses() -> None:
    cleaned = clean_text("Write to jane.doe@example.org or <a href='mailto:x@y.fr'>x@y.fr</a>.")
    assert "@" not in cleaned
    assert cleaned.count(EMAIL_PLACEHOLDER) == 2


def test_clean_text_masks_obfuscated_email_addresses() -> None:
    cleaned = clean_text("a.b[@]host.fr, c.d(at)host.fr, e.f [at] host.fr; chars (- & + @ ! ?)")
    assert cleaned.count(EMAIL_PLACEHOLDER) == 3
    assert "host.fr" not in cleaned
    assert "(- & + @ ! ?)" in cleaned  # a lone @ is not an address


def test_clean_record_extracts_every_field() -> None:
    d = clean_record(FULL)
    assert d.uuid == "bike-lanes"
    assert d.title == "Title bike-lanes"
    assert d.abstract == f"Cycle lanes of the city.\n\nContact: {EMAIL_PLACEHOLDER}"
    assert d.keywords == ["Mobilité", "Vélo"]  # "null" dropped, duplicates removed
    assert d.topics == ["transportation"]
    assert d.organizations == ["City A", "Agency B"]
    assert d.formats == ["CSV", "GeoJSON"]
    assert d.link_urls == ["https://data.example.org/bike.csv"]  # a bare string becomes a list
    assert d.bbox == (7.6, 48.5, 7.9, 48.7)
    assert d.resource_dates == {
        "revision": "2025-03-01T00:00:00.000Z",
        "publication": "2024-01-01T00:00:00.000Z",
    }
    assert d.licenses == ["Licence Ouverte v2.0", "Cite the source"]
    assert d.lineage == "Surveyed in 2024."
    assert d.update_frequency == "Annuel"
    assert d.harvested is True
    assert [(c.name, c.type) for c in d.columns] == [("id", "int"), ("surface", "text")]


def test_clean_record_tolerates_a_bare_record() -> None:
    d = clean_record(hit("bare"))
    assert d.abstract == ""
    assert d.keywords == [] and d.topics == [] and d.columns == []
    assert d.bbox is None
    assert d.harvested is False


def test_organization_may_be_a_single_object() -> None:
    assert clean_record(hit("x", OrgForResourceObject=ml("Solo"))).organizations == ["Solo"]


def test_is_dataset_filters_maps_and_services() -> None:
    assert is_dataset(hit("a"))
    assert is_dataset(hit("b", resourceType=["series"]))
    assert not is_dataset(hit("c", resourceType=["staticMap"]))
    assert not is_dataset(hit("d", resourceType=["service"]))
    assert not is_dataset({"_source": {}})


def build(tmp_path: Path, hits: list[dict[str, Any]]) -> Path:
    out = tmp_path / "snap"
    build_snapshot(
        hits, out, source_url="https://portal.test", now=datetime(2026, 11, 12, tzinfo=UTC)
    )
    return out


def test_snapshot_keeps_datasets_only_sorted_by_uuid(tmp_path: Path) -> None:
    out = build(tmp_path, [hit("b"), hit("m", resourceType=["staticMap"]), hit("a"), FULL])
    manifest = load_manifest(out)
    assert (manifest.records_seen, manifest.datasets_kept) == (4, 3)
    assert manifest.created_at == "2026-11-12T00:00:00+00:00"
    assert [d.uuid for d in load_snapshot(out)] == ["a", "b", "bike-lanes"]


def test_snapshot_round_trips_a_full_record(tmp_path: Path) -> None:
    out = build(tmp_path, [FULL])
    assert load_snapshot(out) == [clean_record(FULL)]


def test_snapshot_contains_no_email_address(tmp_path: Path) -> None:
    out = build(tmp_path, [FULL])
    assert "@" not in (out / DATASETS_FILE).read_text(encoding="utf-8")


def test_fingerprint_is_stable_and_detects_changes(tmp_path: Path) -> None:
    first = load_manifest(build(tmp_path / "1", [hit("a"), hit("b")]))
    same = load_manifest(build(tmp_path / "2", [hit("b"), hit("a")]))
    changed = load_manifest(
        build(tmp_path / "3", [hit("a"), hit("b", changeDate="2026-02-02T00:00:00Z")])
    )
    assert first.fingerprint_sha256 == same.fingerprint_sha256
    assert first.fingerprint_sha256 != changed.fingerprint_sha256


def test_verify_detects_a_tampered_snapshot(tmp_path: Path) -> None:
    out = build(tmp_path, [hit("a"), hit("b")])
    assert verify_snapshot(out)
    path = out / DATASETS_FILE
    path.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    assert not verify_snapshot(out)


def test_cli_verify_reports_status(tmp_path: Path) -> None:
    out = build(tmp_path, [hit("a")])
    assert main(["snapshot", "--out", str(out), "--verify"]) == 0
