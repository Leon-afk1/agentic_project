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
| Portal client, MCP server, golden set, baselines, agent, evaluation | not started |

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

## Ground rules

- **Read-only.** Nothing is ever written to the portal.
- **Polite client.** Local cache, at most one request per second, a User-Agent naming the project.
- **No personal data.** Contact fields of catalog records are dropped at extraction time.
- **Untrusted text.** Dataset descriptions are treated as untrusted input (prompt injection).

## Data and licences

Code: MIT (see `LICENSE`). Catalog metadata belongs to its producers and is published under the
licence stated in each record (mostly Licence Ouverte / Etalab and ODbL). The catalog itself is not
redistributed in this repository.
