"""Command-line entry point. Sub-commands are added milestone by milestone."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from oabench import __version__
from oabench.portal.http import CachedClient, PortalError, RateLimiter, ResponseCache
from oabench.portal.search import SEARCH_URL, iter_hits
from oabench.retrieval.engines import Engine
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

    golden = commands.add_parser("golden", help="manage the golden set of questions")
    golden.add_argument("--file", type=Path, default=Path("data/golden/questions.jsonl"))
    golden.add_argument("--snapshot", type=Path, default=Path("data/snapshot"))
    actions = golden.add_subparsers(dest="action", required=True)
    actions.add_parser("stats", help="counts by status, level, source and split")
    actions.add_parser("review", help="validate proposed questions one by one")
    add = actions.add_parser("add", help="add a question of your own")
    add.add_argument("question")
    add.add_argument("--level", required=True, choices=["easy", "medium", "hard", "no_answer"])
    actions.add_parser("split", help="assign validated questions to dev or test (never reshuffles)")
    pool = actions.add_parser(
        "pool", help="merge the top results of every engine for each question"
    )
    # The four base engines are pooled at depth 10. A system evaluated later (hybrid,
    # agent) only adds its own top 5: `golden pool --engines hybrid --depth 5`.
    pool.add_argument("--engines", default="bm25,portal,portal_keywords,embeddings")
    pool.add_argument("--depth", type=int, default=10)
    actions.add_parser("judge", help="grade pooled candidates one by one")

    evaluate = commands.add_parser("eval", help="score the engines on the judged questions")
    evaluate.add_argument("--golden", type=Path, default=Path("data/golden"))
    evaluate.add_argument("--snapshot", type=Path, default=Path("data/snapshot"))
    evaluate.add_argument("--engines", default=",".join(ENGINE_NAMES))
    evaluate.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    evaluate.add_argument("--lenient", action="store_true", help="count partially relevant too")
    return parser


ENGINE_NAMES = ("bm25", "portal", "portal_keywords", "embeddings", "hybrid")


def make_engines(names: str, snapshot: Path, cache_dir: Path = Path(".cache")) -> list[Engine]:
    from oabench.catalog import Catalog
    from oabench.retrieval.engines import (
        BM25Engine,
        EmbeddingEngine,
        FastEmbedEmbedder,
        HybridEngine,
        PortalEngine,
    )
    from oabench.snapshot.build import load_snapshot

    datasets = load_snapshot(snapshot)
    built: dict[str, Engine] = {}

    def get(name: str) -> Engine:
        if name in built:
            return built[name]
        engine: Engine
        if name == "bm25":
            engine = BM25Engine(Catalog(datasets))
        elif name in ("portal", "portal_keywords"):
            client = CachedClient(
                ResponseCache(cache_dir / "http.sqlite"), RateLimiter(MIN_INTERVAL_S), ttl_s=None
            )
            known = {d.uuid for d in datasets}
            engine = PortalEngine(client, known, keywords=name == "portal_keywords")
        elif name == "embeddings":
            cache = cache_dir / "embeddings.npz"
            engine = EmbeddingEngine(datasets, FastEmbedEmbedder(), cache=cache)
        elif name == "hybrid":
            engine = HybridEngine([get("bm25"), get("embeddings")])
        else:
            raise SystemExit(f"unknown engine '{name}' (choose among {', '.join(ENGINE_NAMES)})")
        built[name] = engine
        return engine

    return [get(name) for name in [n.strip() for n in names.split(",") if n.strip()]]


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


def run_golden(args: argparse.Namespace) -> int:
    from oabench.eval.golden import assign_split, load_questions, save_questions, summary
    from oabench.eval.review import add_question, review

    if args.action == "review":
        from oabench.catalog import Catalog
        from oabench.snapshot.build import DATASETS_FILE, load_snapshot

        has_snapshot = (args.snapshot / DATASETS_FILE).exists()
        catalog = Catalog(load_snapshot(args.snapshot)) if has_snapshot else None
        review(args.file, catalog=catalog)
    elif args.action == "add":
        question = add_question(args.file, args.question, args.level)
        print(f"added [{question.id}] {question.question}")
    elif args.action == "split":
        save_questions(args.file, assign_split(load_questions(args.file)))
    elif args.action in ("pool", "judge"):
        from oabench.eval.pool import (
            JUDGMENTS_FILE,
            POOL_FILE,
            build_pool,
            judge,
            load_judgments,
            load_pool,
            pending,
            save_pool,
        )

        pool_path = args.file.parent / POOL_FILE
        judgments_path = args.file.parent / JUDGMENTS_FILE
        if args.action == "pool":
            engines = make_engines(args.engines, args.snapshot)
            pool = build_pool(
                load_questions(args.file), engines, depth=args.depth, existing=load_pool(pool_path)
            )
            save_pool(pool_path, pool)
        else:
            from oabench.catalog import Catalog
            from oabench.snapshot.build import load_snapshot

            catalog = Catalog(load_snapshot(args.snapshot))
            judge(pool_path, judgments_path, load_questions(args.file), catalog)
        pool = load_pool(pool_path)
        left = len(pending(pool, load_judgments(judgments_path)))
        print(f"pool   : {len(pool)} candidates, {left} still to judge")
    for section, counts in summary(load_questions(args.file)).items():
        print(f"{section:7}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


def run_eval(args: argparse.Namespace) -> int:
    from oabench.eval.abstention import evaluate_abstention, format_abstention
    from oabench.eval.golden import QUESTIONS_FILE, load_questions
    from oabench.eval.pool import JUDGMENTS_FILE, load_judgments, qrels
    from oabench.eval.report import evaluate_engine, format_comparisons, format_table

    everything = load_questions(args.golden / QUESTIONS_FILE)
    dev = [q for q in everything if q.split == "dev"]
    questions = [q for q in everything if args.split == "all" or q.split == args.split]
    grades = qrels(load_judgments(args.golden / JUDGMENTS_FILE))
    min_grade = 1 if args.lenient else 2
    engines = make_engines(args.engines, args.snapshot)
    reports = [evaluate_engine(e, questions, grades, min_grade=min_grade) for e in engines]
    relevance = "lenient (grade >= 1)" if args.lenient else "strict (grade 2)"
    print(f"split={args.split}  relevance={relevance}")
    print(format_table(reports))
    print(format_comparisons(reports, "bm25"))
    # The fused score of the hybrid engine is rank-based, not a confidence.
    scored = [engine for engine in engines if engine.name != "hybrid"]
    print(
        format_abstention(
            [evaluate_abstention(e, dev, questions, grades, min_grade=min_grade) for e in scored]
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "snapshot":
        return run_snapshot(args)
    if args.command == "mcp":
        return run_mcp(args)
    if args.command == "golden":
        return run_golden(args)
    if args.command == "eval":
        return run_eval(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
