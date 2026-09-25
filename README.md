The GitHub Pipeline Recovery Agent is an Agentic AI system that responds automatically to failed GitHub Actions workflows. When a pipeline failure occurs, the agent collects relevant logs and code changes, investigates the likely cause, selects appropriate diagnostic tools, and prepares a recommended fix. Where possible, it validates the proposed solution by running tests and creates a draft pull request for developer approval. The project demonstrates event-driven architecture, CI/CD integration, autonomous tool selection, software testing, AI evaluation, and human-in-the-loop control.

# Phase 1 Walkthrough — Repo, Demo Project & CI Scaffolding

A step-by-step summary of everything done in Phase 1, with the reasoning behind each step. Meant to be followed in order — each step depends on the one before it.
---

## 1. Check prerequisites
```bash
python3 --version   # need 3.12+
git --version
```
Confirms the tools we need are installed before we build anything on top of them.

## 2. Create two repos on GitHub
- `github-pipeline-recovery-agent` — the agent itself
- `demo-repo-recovery-target` — a small, boring repo the agent will later watch and "fix"

**Why two repos:** the agent and the thing it's fixing must be independent. If they lived in one repo, the agent could accidentally modify its own code instead of the target project's — a real safety mixup, not just a style choice.

## 3. Clone both locally
```bash
git clone https://github.com/<user>/github-pipeline-recovery-agent.git
git clone https://github.com/<user>/demo-repo-recovery-target.git
```

## 4. Build the folder skeleton (main repo)
```bash
cd github-pipeline-recovery-agent
mkdir -p app/{api,github,orchestration,agents,tools,sandbox,policy,models,storage,prompts}
mkdir -p tests/{unit,integration,security,end_to_end}
mkdir -p benchmark/{cases,scoring}
mkdir -p docs
mkdir -p .github/workflows
```
Each `app/` folder maps to one component of the system:

| Folder | Responsibility |
|---|---|
| `app/api` | webhook route, signature check, queueing |
| `app/github` | GitHub App auth + REST calls |
| `app/orchestration` | the LangGraph state machine |
| `app/agents` | diagnosis, planning, repair logic |
| `app/tools` | the approved tool catalog |
| `app/sandbox` | isolated patch validation |
| `app/policy` | permission limits, protected paths |
| `app/models` | Pydantic schemas |
| `app/storage` | audit/recovery record persistence |
| `app/prompts` | versioned prompt templates |

Building the skeleton up front means everyone on the team knows where new code belongs before any code exists.

## 5. Turn each folder into a Python package
```bash
touch app/__init__.py
for d in api github orchestration agents tools sandbox policy models storage prompts; do
  touch app/$d/__init__.py
done
for d in unit integration security end_to_end; do
  touch tests/$d/__init__.py
done
touch benchmark/cases/.gitkeep benchmark/scoring/.gitkeep
```
An `__init__.py` file (even empty) tells Python "this folder is an importable package." Without it, `from app.api.main import app` wouldn't work. `.gitkeep` is a convention — git doesn't track empty folders, so this dummy file keeps `benchmark/cases/` visible in the repo even before it has real content.

## 6. Writing `pyproject.toml`
This file declares the project's name, dependencies, and tool configs (linter, test runner) in one place.
```toml
[project]
name = "github-pipeline-recovery-agent"
version = "0.1.0"
description = "Event-driven agentic CI failure recovery system"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "httpx>=0.27",
    "langgraph>=0.2",
    "langchain-core>=0.3",
    "PyJWT>=2.9",
    "cryptography>=43.0",
    "sqlalchemy>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.6",
    "respx>=0.21",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```
- **`dependencies`** — what the app needs to *run*.
- **`optional-dependencies.dev`** — tools needed only to *develop* (test runner, linter), installed via `pip install -e ".[dev]"`.
- **`tool.ruff`** — linter config; `select` turns on error checks, unused-import detection, import sorting, modern-syntax hints, and common bug patterns.
- **`build-system`** — boilerplate required for `pip install -e .` to work at all.

## 7. Create and activate a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```
Keeps this project's dependencies isolated from every other Python project on the machine. Must be created **at the repo root**, next to `pyproject.toml` — running this command from the wrong folder is the most common setup mistake. Check for `(.venv)` at the start of your terminal prompt to confirm it's active.

## 8. Install the project in editable mode
```bash
pip install --upgrade pip
pip install -e ".[dev]"
```
`-e` (editable install) means Python imports `app/` directly from source, so code edits are picked up immediately with no reinstall step. `".[dev]"` installs both the core dependencies and the dev extras.

## 9. Write the FastAPI app
`app/api/main.py`:
```python
"""FastAPI application entrypoint.

Phase 1 only exposes a health endpoint. The webhook route, signature
verification, and event queueing land in Phase 3.
"""

from fastapi import FastAPI

app = FastAPI(title="GitHub Pipeline Recovery Agent")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
```
A health endpoint is the smallest possible slice that proves the whole chain works: FastAPI is installed, the package imports cleanly, and later, CI/deploy tooling has something to check.

## 10. Run it and check manually
```bash
uvicorn app.api.main:app --reload
```
In a second terminal:
```bash
curl http://localhost:8000/healthz
# {"status":"ok"}
```

## 11. Write an automated test
`tests/unit/test_health.py`:
```python
from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)


def test_healthz_returns_ok():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```
`TestClient` runs the app in-process — no real server, no network. This replaces manually curling the endpoint with something that runs automatically forever.

## 12. Run pytest
```bash
pytest
```
Expect `1 passed`. A `ModuleNotFoundError: No module named 'app'` almost always means the venv isn't activated, or `pytest` was run from the wrong directory (must be repo root).

## 13. Run ruff
```bash
ruff check .
```
Expect `All checks passed!`. If it flags something, it tells you the exact file, line, and rule code — fix it, don't ignore it.

## 14. Add `.gitignore`
```
__pycache__/
*.py[cod]
.venv/
*.db
.env
.pytest_cache/
.ruff_cache/
htmlcov/
.coverage
```
Stops the virtualenv, bytecode caches, and tool caches from ever being committed. Verify with `git status` — `.venv/` should never show up as untracked.

## 15. Commit, push, and confirm CI is green
```bash
git add .
git status   # confirm no .venv/, no __pycache__/
git commit -m "Phase 1: project scaffolding, health endpoint, CI"
git push
```
Then check the **Actions** tab on GitHub — a workflow run should start automatically. Watch it once end-to-end so you know what CI actually does under the hood.

---
---

###TODO 

## Still remaining to finish Phase 1
- [ ] Repeat steps 4–15 (adapted) for `demo-repo-recovery-target`: a small `calculator` module, matching pytest tests, and its own `ci.yml`
- [ ] Commit and push the demo repo
- [ ] Confirm **both** repos show a green CI checkmark on GitHub

Phase 1 isn't done until both repos are pushed with passing CI — that's the foundation everything in later phases (webhook intake, LangGraph, sandboxing) gets built on top of.
