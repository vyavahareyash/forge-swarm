"""
Unit tests for WorktreeManager and ephemeral worktree isolation (ADR 0002).
"""
import subprocess
from pathlib import Path
import pytest

from src.worktree import WorktreeError, WorktreeManager
from tests.fixtures.sample_repo import ensure_sample_repo


@pytest.fixture
def test_repo(tmp_path):
    repo = ensure_sample_repo(target_dir=tmp_path / "sample_repo", reset=True)
    return repo


def test_worktree_create_and_remove(test_repo, tmp_path):
    wm = WorktreeManager(workspace_root=str(tmp_path / "workspaces"))
    issue_id = "test-1"

    wt_path = wm.create_worktree(test_repo, issue_id=issue_id, branch_name="fix/test-1")
    assert wt_path.exists()
    assert (wt_path / "math_lib" / "calculator.py").exists()

    # Check git branch in worktree
    res = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=str(wt_path),
        capture_output=True,
        text=True,
    )
    assert res.stdout.strip() == "fix/test-1"

    # Remove worktree
    wm.remove_worktree(test_repo, wt_path)
    assert not wt_path.exists()


def test_worktree_diff_and_commit(test_repo, tmp_path):
    wm = WorktreeManager(workspace_root=str(tmp_path / "workspaces"))
    wt_path = wm.create_worktree(test_repo, issue_id="test-diff")

    calc_file = wt_path / "math_lib" / "calculator.py"
    calc_file.write_text("# modification\n" + calc_file.read_text())

    diff = wm.get_diff(wt_path)
    assert "+# modification" in diff

    commit_hash = wm.commit_changes(wt_path, "test: add modification")
    assert len(commit_hash) >= 7

    # Cleanup
    wm.remove_worktree(test_repo, wt_path)


def test_isolated_worktree_context_manager(test_repo, tmp_path):
    wm = WorktreeManager(workspace_root=str(tmp_path / "workspaces"))
    target_path = None

    with wm.isolated_worktree(test_repo, issue_id="ctx-test", cleanup=True) as wt:
        target_path = wt
        assert wt.exists()
        assert (wt / "pyproject.toml").exists()

    assert not target_path.exists()
