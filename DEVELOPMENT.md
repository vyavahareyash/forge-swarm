# Development Guide

Reference guide for setting up, linting, formatting, testing, and running Forge Swarm.

---

## 1. Environment Setup

### Install Dependencies
Using `uv`:
```bash
# Sync project and development dependencies (pytest, ruff, pre-commit)
uv sync --extra dev
```

### Activate Virtual Environment
```bash
# Deactivate any previously active virtualenv first if switching repos
deactivate 2>/dev/null || true

# Activate the local virtual environment
source .venv/bin/activate
```

---

## 2. Pre-Commit Hooks

Git hooks run formatters, linters, and safety checks on every `git commit`.

```bash
# Install git hook scripts into .git/hooks/
uv run pre-commit install
# (or if venv is active: pre-commit install)

# Run checks manually against all files in the repository
uv run pre-commit run --all-files

# Run checks only on staged files
uv run pre-commit run
```

---

## 3. Linting & Formatting (Ruff)

Ruff handles style formatting, code quality linting, and import sorting (`isort`).

```bash
# Check code for lint errors and import sorting issues
uv run ruff check .

# Automatically fix fixable lint issues and organize imports
uv run ruff check . --fix

# Preview formatting changes without writing
uv run ruff format . --check

# Format all files (applies black-compatible styling)
uv run ruff format .
```

---

## 4. Running Tests

```bash
# Run entire test suite
uv run pytest

# Run with verbose output and immediate stdout printing
uv run pytest -v -s

# Run a specific test file
uv run pytest tests/test_orchestrator.py

# Run tests matching a specific expression or name
uv run pytest -k "test_budget"
```

---

## 5. Local Execution & Services

### Slack War Room Bot
```bash
# Run Slack Bot (Socket Mode)
uv run python -m src.run_bot
```

### Streamlit Observability Dashboard
```bash
# Start Streamlit local web UI
uv run streamlit run poc/app.py
```

### GitHub Issue Poller
```bash
# Run GitHub trigger poller
uv run python -m src.triggers.github_poller
```

---

## 6. Git Workflow Quick Reference

```bash
# Check status
git status

# Stage changes
git add -u
# or stage everything including new files
git add .

# Commit (triggers pre-commit hooks automatically)
git commit -m "feat: description of change"

# Push to remote
git push origin <branch-name>
```
