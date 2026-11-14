"""Command-line entry point. Sub-commands are added milestone by milestone."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from oabench import __version__
from oabench.portal.http import CachedClient, PortalError, RateLimiter, ResponseCache
from oabench.portal.search import SEARCH_URL, iter_hits
from oabench.snapshot.build import build_snapshot, load_manifest, verify_snapshot

MIN_INTERVAL_S = 1.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oabench",
        description="Dataset-search benchmark on an open-data catalog (work in progress).",
    )
    parser.add_argument("--version", action="version", version=f"oabench {__version__}")
    commands = parser.add_subparsers(dest="command")

    snapshot = commands.add_parser(
        "snapshot", help="download the catalog (read-only) and write a frozen, cleaned extract"
    )
    snapshot.add_argument("--out", type=Path, default=Path("data/snapshot"))
    snapshot.add_argument("--cache", type=Path, default=Path(".cache/http.sqlite"))
    snapshot.add_argument("--max-pages", type=int, default=None, help="stop early (for a dry run)")
    snapshot.add_argument("--refresh", action="store_true", help="ignore cached responses")
    snapshot.add_argument(
        "--verify", action="store_true", help="only check an existing snapshot against its manifest"
    )

    mcp = commands.add_parser("mcp", help="serve the snapshot over MCP (stdio, read-only)")
    mcp.add_argument("--snapshot", type=Path, default=Path("data/snapshot"))
    return parser


def run_snapshot(args: argparse.Namespace) -> int:
    if args.verify:
        ok = verify_snapshot(args.out)
        manifest = load_manifest(args.out)
        print(f"{manifest.datasets_kept} datasets, created {manifest.created_at}")
        print("fingerprints match the manifest" if ok else "MISMATCH with the manifest")
        return 0 if ok else 1

    client = CachedClient(ResponseCache(args.cache), RateLimiter(MIN_INTERVAL_S))
    try:
        hits = iter_hits(client, max_pages=args.max_pages, refresh=args.refresh)
        manifest = build_snapshot(hits, args.out, source_url=SEARCH_URL)
    except PortalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    stats = client.stats
    print(f"records seen     : {manifest.records_seen}")
    print(f"datasets kept    : {manifest.datasets_kept}")
    print(f"network calls    : {stats.network_calls} (cache hits: {stats.cache_hits})")
    print(f"fingerprint hash : {manifest.fingerprint_sha256[:16]}")
    print(f"written to       : {args.out}")
    return 0


def run_mcp(args: argparse.Namespace) -> int:
    # Imported here so that other commands do not pay for loading the MCP SDK.
    from oabench.catalog import Catalog
    from oabench.mcp_server.server import build_server
    from oabench.snapshot.build import DATASETS_FILE, load_snapshot

    if not (args.snapshot / DATASETS_FILE).exists():
        print(
            f"error: no snapshot in {args.snapshot}. Run `oabench snapshot` first.", file=sys.stderr
        )
        return 1
    # stdout belongs to the MCP protocol from here on: nothing else may print to it.
    build_server(Catalog(load_snapshot(args.snapshot))).run("stdio")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "snapshot":
        return run_snapshot(args)
    if args.command == "mcp":
        return run_mcp(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
