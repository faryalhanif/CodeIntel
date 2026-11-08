# CodeIntel - Autonomous Software Engineering & Code Intelligence Platform

Point it at a Git repo or a ZIP. It indexes the code, maps dependencies, and runs specialised agents that
produce **evidence-backed** findings (bugs, security, missing tests, docs) plus a chat interface with file:line citations.

> Design rule: **deterministic tools find, the LLM explains.** Every finding is labelled
> `tool-confirmed`, `heuristic`, or `AI hypothesis`, and AI hypotheses are discarded unless their quoted code
> actually exists at that location in the file.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
                    # end-to-end on examples/vulnerable_app, no server needed
make api                       # http://localhost:8000/docs
make ui                        # http://localhost:8501  (in a second terminal)
pytest -q                      # 33 tests
```

Optional (the platform degrades gracefully without any of these):

| Want | Do |
|---|---|
| AI explanations, chat answers, AI review pass | `ollama pull qwen2.5-coder:7b` (set `CODEINTEL_OLLAMA_MODEL`) |
| Real semantic embeddings | `pip install sentence-transformers` (auto-detected) |
| Semgrep rules | `pip install semgrep`, set `CODEINTEL_SEMGREP_CONFIG=p/python` |
| Local folders instead of git/zip | `CODEINTEL_ALLOW_LOCAL_PATHS=true` (trusted dev only) |

Copy `.env.example` to `.env` for all settings.

## What runs where

```
Streamlit UI ──HTTP──> FastAPI ──> services ──> LangGraph workflow ──> agents ──> tools
                          │                          │                              ├ AST checks
                          │                          └ Supervisor (route/aggregate/ ├ ruff, bandit, semgrep
                          ▼                            validate evidence)           ├ secret scanner
                   SQLite (repos, analyses)                                         └ LLM (optional, Ollama)
                   data/index/<repo>/ (chunks, vectors, dependency graph)
```

| Agent | Job | Evidence sources |
|---|---|---|
| understanding | languages, layout, entry points, frameworks, endpoints, dependency graph (Mermaid) | parser, NetworkX |
| bug | swallowed exceptions, mutable defaults, unhandled-exception candidates, duplicates, pyflakes/bugbear | AST, ruff |
| security | secrets, SQLi/command-injection/unsafe-deserialization patterns, sensitive files, unpinned deps | AST, bandit, semgrep, regex |
| testing | untested public functions, pytest **templates** (syntax-checked, never written to the repo) | AST |
| documentation | module reference, API endpoint table, docstring gaps, optional AI docstrings | AST |
| supervisor | routes the task, merges duplicates across tools, notes severity conflicts, verifies evidence, builds report | - |

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/repositories` | `{"git_url": "https://github.com/..."}` (or `local_path` if enabled) |
| POST | `/repositories/upload` | multipart `.zip` |
| GET | `/repositories`, `/repositories/{id}` | list / status (`pending→ingesting→ready/failed`) |
| POST | `/repositories/{id}/analysis` | `{"task": "...", "agents": [optional]}` → `analysis_id` |
| GET | `/analyses/{id}` | status + full report |
| GET | `/repositories/{id}/overview` | architecture overview |
| POST | `/repositories/{id}/chat` | `{"question": "..."}` → answer + citations |
| DELETE | `/repositories/{id}` | remove repo + index |

Set `CODEINTEL_API_KEY` to require an `X-API-Key` header on every route.

## Project layout

```
src/codeintel/
  config.py  models.py  db.py
  ingestion/   sources (git/zip/local, safe) · scanner · parser · graph · chunker
  retrieval/   embeddings (ST or hashing) · store (hybrid search + graph expansion)
  llm/         Ollama client, JSON extraction, prompt-injection wrapper
  tools/       static_python (AST) · secrets · external (ruff/bandit/semgrep)
  agents/      base · understanding · bug · security · testing · docs · supervisor · llm_review
  workflow/    LangGraph graph (+ thread-pool fallback)
  services/    ingestion + analysis jobs
  api/         FastAPI app
frontend/app.py          Streamlit dashboard
examples/vulnerable_app  intentionally flawed demo target
tests/                   33 tests (ingestion, tools, agents, API, LLM stub)
docs/                    ARCHITECTURE.md · SECURITY.md · ROADMAP.md
```

## Honest limitations

- Deep analysis is **Python-first**. JS/TS/Java/Go get structure + retrieval + secret scanning via a regex parser (tree-sitter is the planned upgrade).
- "Unhandled exception" and the testing agent's coverage are **heuristics**, labelled as such. Real coverage needs sandboxed test execution (see ROADMAP).
- Background jobs run in-process (`BackgroundTasks`); fine for one node, swap for a queue to scale.
- Without an LLM you get findings + retrieval-ranked answers, not prose explanations.
