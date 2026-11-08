# Repository Guidelines

## Project Structure

CRA is a Python FastAPI application for Troy, an autonomous complaint resolution agent. Backend routes and app startup live in `app/main.py`; resolution orchestration is in `app/simulation/`; database tools and CSV persistence are in `app/database.py`; triage, providers, auth, prompts, and utilities have focused subpackages under `app/`. The browser client and its static assets are under `public/`. Test coverage is in `tests/`, and sample account and transaction data are in `data/`. Read `PROJECT.md` for milestone goals and interface contracts, and `TEST_INFRA.md` for test strategy.

## Development Commands

- `python -m venv .venv && source .venv/bin/activate` creates and activates a local environment (Windows activation: `.venv\\Scripts\\activate`).
- `pip install -r requirements.txt` installs runtime and test dependencies.
- `python run.py` starts the development server, normally at `http://localhost:3000`.
- `pytest` runs the complete test suite; use `pytest tests/test_database_tools.py -q` to focus on CSV tools.

## Code Style and Naming

Follow the existing Python style: four-space indentation, `snake_case` functions and modules, `PascalCase` classes, and explicit type hints for public helpers. Keep responsibilities in the existing `app/` packages. Frontend JavaScript, CSS, and HTML follow the existing file conventions in `public/`; avoid unrelated UI changes because frontend compatibility is an explicit project constraint.

## Tests and Data Safety

Use pytest and name new tests `test_*.py` with descriptive `test_*` functions. Prefer the temporary database isolation fixture in `tests/conftest.py` for tests that mutate CSV state. Verify edge cases and API/SSE compatibility when changing resolution flows. Never use the checked-in `data/*.csv` as a scratchpad or commit credentials; configure secrets locally through `.env` (based on `.env.example`).

## Commits and Pull Requests

Recent history uses short imperative Conventional Commit subjects such as `feat: ...`, `fix: ...`, and `chore: ...`; keep each commit focused. A pull request should explain the user-visible behavior and implementation, link the relevant issue or milestone when available, list verification commands and results, and include screenshots for frontend changes. Call out any change to contracts documented in `PROJECT.md`.

## Agent Workflow

Before editing, compare the requested outcome with `PROJECT.md`, `ORIGINAL_REQUEST.md`, and the relevant tests. Preserve documented API, SSE, and data-remediation contracts unless the task explicitly changes them. Make the smallest coherent change, run the relevant pytest coverage, inspect the diff, and report any unmet acceptance criteria clearly.
