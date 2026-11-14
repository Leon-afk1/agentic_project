"""Paging over the GeoNetwork Elasticsearch search endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from oabench.portal.http import CachedClient, PortalError

SEARCH_URL = "https://www.datagrandest.fr/geonetwork/srv/api/search/records/_search"
PAGE_SIZE = 500

# Explicit allow-list of index fields. Contact fields (names, e-mails, phone
# numbers of the people behind a record) are deliberately absent: what is never
# downloaded cannot leak into an index, a prompt or a trace.
SOURCE_INCLUDES = [
    "uuid",
    "resourceTitleObject",
    "resourceAbstractObject",
    "tag",
    "cl_topic",
    "resourceType",
    "format",
    "linkProtocol",
    "linkUrl",
    "geom",
    "resourceTemporalExtentDateRange",
    "resourceDate",
    "changeDate",
    "OrgForResourceObject",
    "sourceCatalogue",
    "isHarvested",
    "licenseObject",
    "MD_LegalConstraintsOtherConstraintsObject",
    "lineageObject",
    "cl_maintenanceAndUpdateFrequency",
    "documentStandard",
    "featureTypes",
]


def page_query(offset: int, size: int = PAGE_SIZE) -> dict[str, Any]:
    # Sorting on uuid gives a stable order, so pages neither overlap nor skip.
    return {
        "from": offset,
        "size": size,
        "track_total_hits": True,
        "sort": [{"uuid": "asc"}],
        "query": {"query_string": {"query": "+isTemplate:n"}},
        "_source": {"includes": SOURCE_INCLUDES},
    }


def iter_hits(
    client: CachedClient,
    *,
    url: str = SEARCH_URL,
    page_size: int = PAGE_SIZE,
    max_pages: int | None = None,
    refresh: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield every raw record of the catalog, one page at a time."""
    offset = 0
    pages = 0
    while max_pages is None or pages < max_pages:
        data = client.post_json(url, page_query(offset, page_size), refresh=refresh)
        try:
            hits = data["hits"]["hits"]
            total = int(data["hits"]["total"]["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PortalError(f"unexpected search response at offset {offset}") from exc
        yield from hits
        pages += 1
        offset += page_size
        if not hits or offset >= total:
            break
