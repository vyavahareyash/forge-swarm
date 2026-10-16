"""
Fixture Generator for Dedicated Sample Target Repository.

Implements ADR 0005: Use Dedicated Fixture Sample Repository for Testing.
Initializes a clean Git repository with a reproducible bug and failing pytest suite.
"""

import os
import shutil
import subprocess
from pathlib import Path

FIXTURE_ROOT = Path(os.path.abspath("tests/fixtures/sample_repo"))

PYPROJECT_CONTENT = """[project]
name = "math_lib"
version = "0.1.0"
dependencies = []

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-p no:deepeval"
"""

CALCULATOR_BUG_CONTENT = """\"\"\"
Math library containing deliberate bug for Stage 2 Swarm verification.
\"\"\"

def calculate_fibonacci(n: int) -> int:
    \"\"\"Calculates the nth Fibonacci number.\"\"\"
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return 0
    if n == 1:
        # BUG: Deliberate defect: returns 2 instead of 1
        return 2

    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
"""

CALCULATOR_FIXED_CONTENT = """\"\"\"
Math library containing deliberate bug for Stage 2 Swarm verification.
\"\"\"

def calculate_fibonacci(n: int) -> int:
    \"\"\"Calculates the nth Fibonacci number.\"\"\"
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return 0
    if n == 1:
        return 1

    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
"""

TEST_CALCULATOR_CONTENT = """from math_lib.calculator import calculate_fibonacci
import pytest

def test_fibonacci_base():
    assert calculate_fibonacci(0) == 0
    assert calculate_fibonacci(1) == 1

def test_fibonacci_sequence():
    assert calculate_fibonacci(2) == 1
    assert calculate_fibonacci(3) == 2
    assert calculate_fibonacci(4) == 3
    assert calculate_fibonacci(5) == 5

def test_fibonacci_negative():
    with pytest.raises(ValueError):
        calculate_fibonacci(-1)
"""


def ensure_sample_repo(target_dir: Path | None = None, reset: bool = False) -> Path:
    """
    Initializes or resets the sample repository fixture.
    Creates a valid Git repo with a baseline commit on branch 'main'.
    """
    repo_path = Path(target_dir or FIXTURE_ROOT).resolve()

    if reset and repo_path.exists():
        shutil.rmtree(repo_path, ignore_errors=True)

    if (repo_path / ".git").exists() and not reset:
        return repo_path

    repo_path.mkdir(parents=True, exist_ok=True)
    math_lib_dir = repo_path / "math_lib"
    tests_dir = repo_path / "tests"
    math_lib_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    (repo_path / "pyproject.toml").write_text(PYPROJECT_CONTENT)
    (math_lib_dir / "__init__.py").write_text("")
    (math_lib_dir / "calculator.py").write_text(CALCULATOR_BUG_CONTENT)
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_calculator.py").write_text(TEST_CALCULATOR_CONTENT)

    # Initialize Git
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Runner"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@npc-inc.local"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit with math_lib defect"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


if __name__ == "__main__":
    path = ensure_sample_repo(reset=True)
    print(f"Sample repo initialized at {path}")
