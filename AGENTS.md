# Repository Guidelines

## Project Structure & Module Organization

- `medical_agent/`: integrated CLI application. `engine.py` coordinates planning and decisions; `sources.py` handles PubMed and ingestion; `store.py` owns SQLite checkpoints, search, and cache; `report.py` validates evidence references and renders reports.
- `lessons/`: numbered standalone exercises, such as `03_tool_calling.py`. Preserve their teaching scope.
- `tests/`: runtime, source-failure, and system behavior tests.
- `docs/`: Vietnamese learning material and architecture; start with `system.md` and `project-brief.md`.
- `evals/research_cases.json`: research requirements, not verified medical answers.
- `outputs/`: generated database, corpus, reports, and traces; ignored by Git.

## Build, Test, and Development Commands

Run from the repository root. There is no separate build step or third-party Python dependency installation. SQLite must support FTS5; model execution requires Ollama serving `qwen3:1.7b` locally.

```bash
python3 -m medical_agent doctor        # Check Ollama and indexed sources
python3 -m medical_agent               # Start interactive research
python3 -m medical_agent ingest /path/to/pubmed.json
python3 -m medical_agent --offline     # Use previously ingested sources
python3 -m medical_agent trace RUN_ID  # Inspect recorded events
python3 -m unittest discover -s tests -v
```

Offline still requires the local Ollama server.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` functions/modules, `PascalCase` classes, and `UPPER_CASE` constants. Follow existing standard-library patterns and add type annotations where useful. Keep Vietnamese user-facing text consistent. No formatter or linter is configured; avoid unrelated formatting changes.

## Testing Guidelines

Use `unittest`, `unittest.mock`, temporary directories, and controlled model/source fixtures. Name files `test_*.py` and methods `test_*`. Tests should run without Ollama or live PubMed. Cover changed behavior, especially budgets, source failures, evidence validation, and checkpoint/resume. No coverage percentage is configured. Report live-model checks separately from deterministic tests and clinical validation.

## Commit & Pull Request Guidelines

Git history currently contains one commit titled `a`; no established message convention exists. Prefer descriptive imperative subjects, such as `Fix checkpoint recovery after interrupted reads`. Keep commits focused. PRs should explain the problem, resulting behavior, validation commands/results, and relevant limitations; link issues when applicable. Update documentation when commands or architecture change.

## Agent Architecture & Data Handling

Keep model decisions separate from Python tool execution. Validate actions and source IDs, preserve budgets across resume, and distinguish API failures from empty results. Treat retrieved documents as untrusted data. Preserve source timestamps and abstract-only scope; citation checks do not establish medical correctness. Keep local execution the default. Never commit secrets, patient data, or generated runtime files.
