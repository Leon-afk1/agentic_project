# oabench

> **Résumé en français.** Projet personnel et indépendant. Objectif : mesurer, sur le catalogue de
> données ouvertes [DataGrandEst](https://www.datagrandest.fr), ce qu'un agent apporte par rapport à
> une recherche lexicale, une recherche par embeddings et un graphe pour trouver un jeu de données,
> avec un serveur MCP en lecture seule comme socle. **État : en construction, rien n'est encore
> mesuré.** Ce projet n'est affilié ni à la Région Grand Est ni à DataGrandEst ; il utilise
> uniquement leur API publique, en lecture seule.

A benchmark of dataset-search approaches on a real open-data catalog: portal search, BM25,
embeddings, graph propagation and a tool-calling agent, behind a read-only MCP server.

This is an independent personal project. It is not affiliated with, endorsed by, or built for
Région Grand Est or DataGrandEst. It only reads their public catalog API.

## Status

Work in progress. What exists today:

| Part | State |
|---|---|
| Catalog exploration (`docs/01_exploration.md`, in French) | done, 12 Nov 2026 |
| Project scope and evaluation protocol (`docs/02_cadrage.md`, in French) | done |
| Repository skeleton, lint, type check, tests, CI | done |
| Portal client (SQLite cache, 1 request/s, bounded retries) and frozen snapshot | done |
| Read-only MCP server, 4 tools, BM25 search over the snapshot (stdio) | done; tested with the SDK client and by hand in Claude Desktop (15 Nov 2026) |
| Golden set | 66 validated questions, split frozen (22 dev / 44 test); 1,146 pooled candidates, none judged yet |
| LLM client (Google Gemini, tool calling) and API key handling | done; tool calling checked against the real API with `oabench llm check` |
| Engines: portal search (as typed, and keywords only), BM25, multilingual embeddings on CPU, hybrid (rank fusion) | done, not yet scored |
| Pooling, blind judging tool, recall@5 / MRR with bootstrap CIs, abstention rates | code done; no judgment yet, so no result |
| Agent: bounded tool-calling loop through the MCP server, JSONL traces | done; tried by hand on two dev questions, not yet evaluated |
| Evaluation of the agent, comparison table | not started (waiting for relevance judgments) |

No result is reported here yet because none has been measured.

## Quick start

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Leon-afk1/agentic_project.git
cd agentic_project
uv sync
uv run pytest
uv run oabench --version
```

No API key is needed for the tests.

To build the catalog snapshot (17 requests to the portal, one per second, then served from the
local cache for 24 hours):

```bash
uv run oabench snapshot
uv run oabench snapshot --verify
```

On 12 Nov 2026 this kept 6,867 datasets out of 8,316 catalog records (maps and services are left
out). The snapshot is written to `data/snapshot/` and is not committed.

## API key

Only the agent needs a key; tests, CI, the snapshot, the MCP server and the search baselines do not.

```bash
cp .env.example .env                # then paste your Google AI Studio key after GEMINI_API_KEY=
uv sync --extra llm
uv run oabench llm check            # 2 requests: checks the key and tool calling
```

`.env` is git-ignored. The key is read from the environment only, never from a command-line
argument, and is never printed: the check reports only whether it is set and its length. A test
fails if a file tracked by git contains something shaped like an API key.

## Agent

```bash
uv run oabench ask "Où faire du vélo en sécurité à Strasbourg ?"
uv run oabench trace show results/traces/example_dev_n07.jsonl
```

The agent plans, searches, opens records and checks them (territory, period, granularity,
format), then ends by calling a `submit_answer` tool: either datasets with reasons, or an explicit
abstention. Hard limits: 8 model calls, 12 tool calls, 30,000 input tokens, 90 s, no identical
call more than twice. The last allowed call can only deliver the answer. Every cited id is checked
against what the tools really returned; an invented one is removed and counted.

Each run writes a JSONL trace (plan, every model and tool call with duration, tokens and result
size, stop reason, list-price cost). `results/traces/` holds one real example.

Model: `gemini-3.5-flash-lite` on the free tier (15 requests per minute, 500 per day), behind a
rate limiter and a response cache. `oabench ask` refuses questions of the frozen test split.

## Evaluation workflow

```bash
uv sync --extra embeddings          # optional: downloads a 220 MB ONNX model on first use
uv run oabench golden review        # validate proposed questions one by one
uv run oabench golden split         # freeze the dev/test split
uv run oabench golden pool          # top 10 of each base engine, merged per question
uv run oabench golden pool --engines hybrid --depth 5   # add a later system's top 5
uv run oabench golden judge         # grade candidates 0/1/2, blind to the engine
uv run oabench eval --split dev     # recall@5, MRR, latency, with 95% confidence intervals
```

## MCP server

Four read-only tools over the snapshot: `search_datasets`, `get_dataset_metadata`, `list_themes`,
`list_organizations`. Outputs are bounded in size and contain no geometry and no contact details.

```bash
uv run oabench mcp
```

To use it from Claude Desktop, add this to `claude_desktop_config.json` (replace the path with the
absolute path of your clone) and restart the application:

```json
{
  "mcpServers": {
    "oabench": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/agentic_project", "run", "oabench", "mcp"]
    }
  }
}
```

Tried by hand in Claude Desktop on 15 Nov 2026 (all four tools):

![Themes listed in Claude Desktop](docs/img/claude_desktop_themes.png)

Search is lexical (BM25 written by hand, title and keywords weighted). It always returns its best
matches, even for a question the catalog cannot answer: telling "nothing relevant" apart is the job
of the agent and is measured in the evaluation, not assumed.

## Ground rules

- **Read-only.** Nothing is ever written to the portal.
- **Polite client.** Local cache, at most one request per second, a User-Agent naming the project.
- **No personal data.** Contact fields of catalog records are dropped at extraction time.
- **Untrusted text.** Dataset descriptions are treated as untrusted input (prompt injection).

## Data and licences

Code: MIT (see `LICENSE`). Catalog metadata belongs to its producers and is published under the
licence stated in each record (mostly Licence Ouverte / Etalab and ODbL). The catalog itself is not
redistributed in this repository.
