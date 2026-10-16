"""Integration tests for End-to-End Pipeline execution."""
import os
import pytest
from pathlib import Path
from src.pipeline import E2EPipeline
from src.storage.db import get_task, init_db
from src.triggers.github_poller import GitHubIssue
from src.worktree import WorktreeManager
from tests.fixtures.sample_repo import ensure_sample_repo


@pytest.mark.asyncio
async def test_e2e_pipeline_mock_execution(tmp_path):
    # 1. Setup isolated sample repo and database
    sample_repo = ensure_sample_repo(target_dir=tmp_path / "sample_repo", reset=True)
    db_path = str(tmp_path / "state.db")
    init_db(db_path)

    workspaces_dir = tmp_path / "workspaces"
    wm = WorktreeManager(workspace_root=str(workspaces_dir))

    pipeline = E2EPipeline(
        target_repo_path=str(sample_repo),
        worktree_manager=wm,
        db_path=db_path,
        mode="standalone",
    )

    issue = GitHubIssue(
        number=101,
        title="Fix Fibonacci bug",
        body="calculate_fibonacci(1) returns 2 instead of 1 in calculator.py",
        labels=["agent-ready"],
        url="https://github.com/mock/repo/issues/101",
    )

    # 2. Execute pipeline with auto-approval
    result = await pipeline.execute_issue(issue=issue, auto_approve_pr=True)

    # 3. Verify results
    assert result.status == "COMPLETED"
    assert result.swarm_result is not None
    assert result.swarm_result.final_report.passed is True
    assert result.pr_url is not None
    assert "mock" in result.pr_url or "101" in result.pr_url

    # Verify task in SQLite
    task = get_task(result.task_id, db_path=db_path)
    assert task is not None
    assert task["status"] == "COMPLETED"
    assert task["current_tokens"] > 0

    # Verify worktree was cleanly pruned
    worktree_path = workspaces_dir / f"issue-{issue.issue_id}"
    assert not worktree_path.exists()
