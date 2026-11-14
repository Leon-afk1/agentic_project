"""Turn a raw catalog record into a small, clean, typed `Dataset`.

Catalog text is untrusted input written by hundreds of organisations: it may
contain HTML, e-mail addresses, or instructions aimed at a language model.
Cleaning happens here, once, before anything is indexed or shown to a model.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel

DATASET_TYPES = frozenset({"dataset", "series", "nonGeographicDataset"})
EMAIL_PLACEHOLDER = "[email removed]"

# Also catches the obfuscated forms "name[@]host", "name(at)host" and "name [at] host".
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+\s?(?:@|\[@\]|\(at\)|\[at\])\s?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
    re.IGNORECASE,
)
_SPACES_RE = re.compile(r"[ \t\r\f\v\xa0]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")
_BLOCK_TAGS = frozenset({"p", "br", "div", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4"})
# Keyword values that some harvesters emit instead of leaving the field empty.
_EMPTY_VALUES = frozenset({"", "null", "none", "n/a"})


class Column(BaseModel):
    name: str
    type: str = ""
    definition: str = ""


class Dataset(BaseModel):
    uuid: str
    title: str
    abstract: str = ""
    keywords: list[str] = []
    topics: list[str] = []
    organizations: list[str] = []
    resource_types: list[str] = []
    formats: list[str] = []
    link_urls: list[str] = []
    link_protocols: list[str] = []
    bbox: tuple[float, float, float, float] | None = None  # west, south, east, north
    resource_dates: dict[str, str] = {}
    temporal_extent: list[dict[str, str]] = []
    change_date: str = ""
    licenses: list[str] = []
    lineage: str = ""
    update_frequency: str = ""
    standard: str = ""
    source_id: str = ""
    harvested: bool = False
    columns: list[Column] = []


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def clean_text(value: str) -> str:
    """Strip HTML, mask e-mail addresses, normalise whitespace."""
    if "<" in value:
        parser = _TextExtractor()
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts)
    value = html.unescape(value)
    value = _EMAIL_RE.sub(EMAIL_PLACEHOLDER, value)
    value = _SPACES_RE.sub(" ", value)
    lines = [line.strip() for line in value.split("\n")]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _label(value: Any) -> str:
    """Multilingual index objects look like {"default": ..., "langfre": ...}."""
    if isinstance(value, dict):
        value = value.get("langfre") or value.get("default") or ""
    return clean_text(str(value)) if value is not None else ""


def _labels(value: Any) -> list[str]:
    seen: dict[str, None] = {}
    for item in _as_list(value):
        text = _label(item)
        if text.lower() not in _EMPTY_VALUES:
            seen.setdefault(text)
    return list(seen)


def _strings(value: Any) -> list[str]:
    return [str(item) for item in _as_list(value) if item not in (None, "")]


def _bbox(geom: Any) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if (
            isinstance(node, list)
            and len(node) >= 2
            and all(isinstance(n, int | float) and not isinstance(n, bool) for n in node[:2])
        ):
            xs.append(float(node[0]))
            ys.append(float(node[1]))
        elif isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            walk(node.get("coordinates"))

    walk(geom)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _columns(feature_types: Any) -> list[Column]:
    columns: dict[str, Column] = {}
    for feature_type in _as_list(feature_types):
        if not isinstance(feature_type, dict):
            continue
        for attribute in _as_list(feature_type.get("attributeTable")):
            if not isinstance(attribute, dict):
                continue
            name = clean_text(str(attribute.get("name") or ""))
            if name and name not in columns:
                columns[name] = Column(
                    name=name,
                    type=clean_text(str(attribute.get("type") or "")),
                    definition=clean_text(str(attribute.get("definition") or "")),
                )
    return list(columns.values())


def is_dataset(hit: dict[str, Any]) -> bool:
    types = _strings(hit.get("_source", {}).get("resourceType"))
    return bool(DATASET_TYPES.intersection(types))


def clean_record(hit: dict[str, Any]) -> Dataset:
    """Build a `Dataset` from one raw search hit. Missing fields become empty."""
    src: dict[str, Any] = hit.get("_source", {})
    topics = [
        str(topic["key"])
        for topic in _as_list(src.get("cl_topic"))
        if isinstance(topic, dict) and topic.get("key")
    ]
    dates: dict[str, str] = {}
    for entry in _as_list(src.get("resourceDate")):
        if isinstance(entry, dict) and entry.get("type") and entry.get("date"):
            dates.setdefault(str(entry["type"]), str(entry["date"]))
    extent = [
        {bound: str(entry[bound]) for bound in ("gte", "lte") if entry.get(bound)}
        for entry in _as_list(src.get("resourceTemporalExtentDateRange"))
        if isinstance(entry, dict)
    ]
    licenses = _labels(src.get("licenseObject"))
    for extra in _labels(src.get("MD_LegalConstraintsOtherConstraintsObject")):
        if extra not in licenses:
            licenses.append(extra)
    frequency = _as_list(src.get("cl_maintenanceAndUpdateFrequency"))
    return Dataset(
        uuid=str(src.get("uuid") or hit.get("_id") or ""),
        title=_label(src.get("resourceTitleObject")),
        abstract=_label(src.get("resourceAbstractObject")),
        keywords=_labels(src.get("tag")),
        topics=list(dict.fromkeys(topics)),
        organizations=_labels(src.get("OrgForResourceObject")),
        resource_types=_strings(src.get("resourceType")),
        formats=list(dict.fromkeys(_strings(src.get("format")))),
        link_urls=_strings(src.get("linkUrl")),
        link_protocols=list(dict.fromkeys(_strings(src.get("linkProtocol")))),
        bbox=_bbox(src.get("geom")),
        resource_dates=dates,
        temporal_extent=[e for e in extent if e],
        change_date=str(src.get("changeDate") or ""),
        licenses=licenses,
        lineage=" ".join(_labels(src.get("lineageObject"))),
        update_frequency=_label(frequency[0]) if frequency else "",
        standard=str(src.get("documentStandard") or ""),
        source_id=str(src.get("sourceCatalogue") or ""),
        harvested=str(src.get("isHarvested")).lower() == "true",
        columns=_columns(src.get("featureTypes")),
    )
