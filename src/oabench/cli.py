"""Command-line entry point. Sub-commands are added milestone by milestone."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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
    agreement = actions.add_parser("agreement", help="compare two judgment files (kappa)")
    agreement.add_argument("first")
    agreement.add_argument("second")

    ask = commands.add_parser("ask", help="ask the agent a question about the catalog")
    ask.add_argument("question")
    ask.add_argument("--model", default=None)
    ask.add_argument("--snapshot", type=Path, default=Path("data/snapshot"))
    ask.add_argument("--runs", type=Path, default=Path("runs"))
    ask.add_argument("--no-cache", action="store_true", help="always call the model")
    ask.add_argument("--quiet", action="store_true", help="print the answer only")
    ask.add_argument(
        "--allow-test", action="store_true", help="allow a question of the frozen test split"
    )

    trace = commands.add_parser("trace", help="inspect agent traces")
    trace_actions = trace.add_subparsers(dest="action", required=True)
    show = trace_actions.add_parser("show", help="print a trace file as readable text")
    show.add_argument("file", type=Path)

    llm = commands.add_parser("llm", help="language-model provider utilities")
    llm_actions = llm.add_subparsers(dest="action", required=True)
    check = llm_actions.add_parser("check", help="verify the API key and tool calling")
    check.add_argument("--model", default=None)

    evaluate = commands.add_parser("eval", help="score the engines on the judged questions")
    evaluate.add_argument("--golden", type=Path, default=Path("data/golden"))
    evaluate.add_argument("--snapshot", type=Path, default=Path("data/snapshot"))
    evaluate.add_argument("--engines", default=",".join(ENGINE_NAMES))
    evaluate.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    evaluate.add_argument("--lenient", action="store_true", help="count partially relevant too")
    evaluate.add_argument(
        "--judgments",
        default="judgments.jsonl",
        help="judgment file inside the golden directory (human by default)",
    )
    evaluate.add_argument("--agent", action="store_true", help="also run and score the agent")
    evaluate.add_argument("--model", default=None, help="agent model")
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
    elif args.action == "agreement":
        from oabench.eval.agreement import compare, format_agreement
        from oabench.eval.pool import load_judgments

        folder = args.file.parent
        result = compare(load_judgments(folder / args.first), load_judgments(folder / args.second))
        print(format_agreement(result, args.first, args.second))
        return 0
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


def run_ask(args: argparse.Namespace) -> int:
    import asyncio
    import logging
    import re
    import time
    import warnings

    warnings.filterwarnings("ignore", category=SyntaxWarning)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request otherwise
    from oabench.agent.runner import ask, build_llm
    from oabench.catalog import Catalog

    # Trying a test question by hand, then adjusting the agent, would leak the test set.
    from oabench.eval.golden import load_questions
    from oabench.llm.config import MissingKeyError, load_env, require_key
    from oabench.llm.gemini import DEFAULT_MODEL
    from oabench.snapshot.build import load_snapshot
    from oabench.tracing.tracer import Tracer, render_trace

    test_questions = {
        q.question.strip().lower()
        for q in load_questions(Path("data/golden/questions.jsonl"))
        if q.split == "test"
    }
    if args.question.strip().lower() in test_questions and not args.allow_test:
        print(
            "error: this question belongs to the frozen test split. Use a dev question while "
            "tuning, or pass --allow-test if you know what you are doing.",
            file=sys.stderr,
        )
        return 1

    load_env()
    try:
        key = require_key()
    except MissingKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    model = args.model or DEFAULT_MODEL
    slug = re.sub(r"[^a-z0-9]+", "-", args.question.lower())[:40].strip("-")
    path = args.runs / f"{time.strftime('%Y%m%d-%H%M%S')}_{slug}.jsonl"
    tracer = Tracer(path)
    llm = build_llm(key, model, cache=None if args.no_cache else Path(".cache/llm.sqlite"))
    result = asyncio.run(
        ask(args.question, llm, Catalog(load_snapshot(args.snapshot)), tracer=tracer)
    )
    if args.quiet:
        print(result.answer.message if result.answer else f"(stopped: {result.stop_reason})")
    else:
        print(render_trace(tracer.events))
    print(f"trace: {path}")
    return 0


def run_trace(args: argparse.Namespace) -> int:
    from oabench.tracing.tracer import load_trace, render_trace

    print(render_trace(load_trace(args.file)))
    return 0


def run_llm(args: argparse.Namespace) -> int:
    import warnings

    # The provider SDK triggers a harmless SyntaxWarning when first compiled.
    warnings.filterwarnings("ignore", category=SyntaxWarning)

    from oabench.llm.check import run_check
    from oabench.llm.config import MissingKeyError, describe_key, load_env, require_key
    from oabench.llm.gemini import DEFAULT_MODEL, GeminiClient, cost_usd
    from oabench.llm.types import LLMError

    load_env()
    print(describe_key())  # presence and length only, never the value
    model = args.model or DEFAULT_MODEL
    try:
        result = run_check(GeminiClient(require_key(), model))
    except (MissingKeyError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    cost = cost_usd(model, result.input_tokens, result.output_tokens)
    print(f"model          : {model}")
    print(f"tool called    : {result.tool_called} (arguments correct: {result.arguments_ok})")
    print(f"final answer   : {result.final_text.strip()[:80]!r}")
    print(f"tokens         : {result.input_tokens} in, {result.output_tokens} out")
    print(f"list-price cost: {'unknown model price' if cost is None else f'{cost:.6f} USD'}")
    print(f"time           : {result.seconds:.1f} s for 2 requests")
    print("OK" if result.ok else "FAILED")
    return 0 if result.ok else 1


def run_agent_eval(args: argparse.Namespace, questions: Any, grades: Any, min_grade: int) -> str:
    import asyncio
    import logging
    import warnings

    warnings.filterwarnings("ignore", category=SyntaxWarning)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from oabench.agent.runner import ask, build_llm
    from oabench.catalog import Catalog
    from oabench.eval.agent_eval import format_agent_report, run_questions, unjudged_pairs
    from oabench.llm.config import load_env, require_key
    from oabench.llm.gemini import DEFAULT_MODEL
    from oabench.snapshot.build import load_snapshot
    from oabench.tracing.tracer import Tracer

    load_env()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    model = args.model or DEFAULT_MODEL
    llm = build_llm(require_key(), model, cache=Path(".cache/llm.sqlite"))
    catalog = Catalog(load_snapshot(args.snapshot))
    out = Path("results") / f"agent_{args.split}_{model}.jsonl"
    traces = Path("runs") / f"eval_{args.split}_{model}"
    validated = [q for q in questions if q.status == "validated"]

    async def ask_one(question: Any) -> Any:
        return await ask(
            question.question, llm, catalog, tracer=Tracer(traces / f"{question.id}.jsonl")
        )

    def progress(run: Any) -> None:
        print(
            f"  {run.question_id}: {run.stop_reason}, {run.steps} calls, {len(run.ranking)} cited",
            flush=True,
        )

    runs = asyncio.run(run_questions(validated, model, ask_one, out, on_done=progress))
    missing = unjudged_pairs(runs, grades)
    report = format_agent_report(runs, validated, grades, min_grade=min_grade)
    lines = [f"--- agent ({model}) ---", report]
    if missing:
        lines.append(
            f"{len(missing)} cited record(s) are not judged yet: "
            "scores are under-estimated until they are."
        )
    lines.append(f"results: {out}")
    return chr(10).join(lines)


def run_eval(args: argparse.Namespace) -> int:
    from oabench.eval.abstention import evaluate_abstention, format_abstention
    from oabench.eval.golden import QUESTIONS_FILE, load_questions
    from oabench.eval.pool import load_judgments, qrels
    from oabench.eval.report import evaluate_engine, format_comparisons, format_table

    everything = load_questions(args.golden / QUESTIONS_FILE)
    dev = [q for q in everything if q.split == "dev"]
    questions = [q for q in everything if args.split == "all" or q.split == args.split]
    grades = qrels(load_judgments(args.golden / args.judgments))
    print(f"judgments: {args.judgments} ({sum(len(g) for g in grades.values())} graded pairs)")
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
    if args.agent:
        print(run_agent_eval(args, questions, grades, min_grade))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    # French text must survive a Windows console that defaults to a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "snapshot":
        return run_snapshot(args)
    if args.command == "mcp":
        return run_mcp(args)
    if args.command == "golden":
        return run_golden(args)
    if args.command == "ask":
        return run_ask(args)
    if args.command == "trace":
        return run_trace(args)
    if args.command == "llm":
        return run_llm(args)
    if args.command == "eval":
        return run_eval(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
