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
| Read-only MCP server, 4 tools, BM25 search over the snapshot (stdio) | done, tested with the SDK client; not yet tried in Claude Desktop |
| Golden set | 57 questions drafted, none validated yet; relevance judgments not started |
| Baselines, agent, evaluation | not started |

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
