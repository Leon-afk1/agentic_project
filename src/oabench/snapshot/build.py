"""Write and read the frozen snapshot.

A snapshot is three files:
- `datasets.jsonl`: one cleaned `Dataset` per line, sorted by uuid;
- `fingerprints.tsv`: uuid and last change date of every dataset;
- `manifest.json`: when and how it was built, plus a hash of the fingerprints.

The hash lets anyone check that two evaluations ran on the same corpus without
the corpus itself being committed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from oabench import __version__
from oabench.portal.http import USER_AGENT
from oabench.snapshot.records import Dataset, clean_record, is_dataset

DATASETS_FILE = "datasets.jsonl"
FINGERPRINTS_FILE = "fingerprints.tsv"
MANIFEST_FILE = "manifest.json"


class Manifest(BaseModel):
    created_at: str
    source_url: str
    records_seen: int
    datasets_kept: int
    skipped_without_uuid: int
    fingerprint_sha256: str
    oabench_version: str
    user_agent: str


def fingerprint_lines(datasets: Iterable[Dataset]) -> list[str]:
    return [f"{d.uuid}\t{d.change_date}" for d in datasets]


def fingerprint_hash(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def build_snapshot(
    hits: Iterable[dict[str, Any]],
    out_dir: Path,
    *,
    source_url: str,
    now: datetime | None = None,
) -> Manifest:
    seen = 0
    skipped = 0
    by_uuid: dict[str, Dataset] = {}
    for hit in hits:
        seen += 1
        if not is_dataset(hit):
            continue
        dataset = clean_record(hit)
        if not dataset.uuid:
            skipped += 1
            continue
        by_uuid[dataset.uuid] = dataset
    datasets = [by_uuid[uuid] for uuid in sorted(by_uuid)]
    lines = fingerprint_lines(datasets)
    manifest = Manifest(
        created_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        source_url=source_url,
        records_seen=seen,
        datasets_kept=len(datasets),
        skipped_without_uuid=skipped,
        fingerprint_sha256=fingerprint_hash(lines),
        oabench_version=__version__,
        user_agent=USER_AGENT,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / DATASETS_FILE).open("w", encoding="utf-8", newline="\n") as handle:
        for dataset in datasets:
            handle.write(dataset.model_dump_json(exclude_defaults=True) + "\n")
    (out_dir / FINGERPRINTS_FILE).write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
    (out_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest.model_dump(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def load_snapshot(snapshot_dir: Path) -> list[Dataset]:
    with (snapshot_dir / DATASETS_FILE).open(encoding="utf-8") as handle:
        return [Dataset.model_validate_json(line) for line in handle if line.strip()]


def load_manifest(snapshot_dir: Path) -> Manifest:
    return Manifest.model_validate_json((snapshot_dir / MANIFEST_FILE).read_text(encoding="utf-8"))


def verify_snapshot(snapshot_dir: Path) -> bool:
    """True when the datasets on disk match the hash recorded in the manifest."""
    datasets = load_snapshot(snapshot_dir)
    expected = load_manifest(snapshot_dir).fingerprint_sha256
    return fingerprint_hash(fingerprint_lines(datasets)) == expected
