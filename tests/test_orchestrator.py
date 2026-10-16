"""
Unit & Integration tests for Orchestrator multi-agent self-correction swarm loop.
"""
import pytest
from unittest.mock import AsyncMock, patch

from src.agents.orchestrator import CoderSubAgent, Orchestrator, QASubAgent
from src.schemas import ImplementationPlan, QATestReport
from src.worktree import WorktreeManager
from tests.fixtures.sample_repo import ensure_sample_repo


@pytest.fixture
def fixture_repo(tmp_path):
    repo = ensure_sample_repo(target_dir=tmp_path / "sample_repo", reset=True)
    return repo


@pytest.mark.asyncio
async def test_qa_subagent_detects_deliberate_bug(fixture_repo, tmp_path):
    wm = WorktreeManager(workspace_root=str(tmp_path / "workspaces"))
    wt = wm.create_worktree(fixture_repo, issue_id="qa-test")
    try:
        qa = QASubAgent(wt)
        report, tokens = await qa.run_tests()
        assert not report.passed
        assert report.failed_tests == 1
        assert report.total_tests == 3
        assert report.failure_summary is not None
        assert "assert 2 == 1" in report.failure_summary
    finally:
        wm.remove_worktree(fixture_repo, wt)


@pytest.mark.asyncio
async def test_orchestrator_successful_self_correction_loop(fixture_repo, tmp_path):
    wm = WorktreeManager(workspace_root=str(tmp_path / "workspaces"))
    db_file = tmp_path / "test_state.db"
    sessions = tmp_path / "sessions"

    orchestrator = Orchestrator(
        workspace_manager=wm,
        db_path=str(db_file),
        sessions_dir=str(sessions),
    )

    result = await orchestrator.run_task(
        issue_id="issue-test-success",
        issue_desc="Fix Fibonacci calculation error",
        repo_path=fixture_repo,
        budget_limit=150_000,
        max_retries=3,
        mode="standalone",
    )
    assert result.status == "SUCCESS"
    assert result.retries_used == 1
    assert result.final_report is not None
    assert result.final_report.passed
    assert result.tokens_spent > 0
    assert len(result.trajectory) >= 3

    # Check session file persisted
    session_file = sessions / f"{result.task_id}.json"
    assert session_file.exists()


@pytest.mark.asyncio
async def test_orchestrator_max_retries_enforced_on_persistent_failure(fixture_repo, tmp_path):
    wm = WorktreeManager(workspace_root=str(tmp_path / "workspaces"))
    db_file = tmp_path / "test_state.db"
    sessions = tmp_path / "sessions"

    orchestrator = Orchestrator(
        workspace_manager=wm,
        db_path=str(db_file),
        sessions_dir=str(sessions),
    )

    # Mock CoderSubAgent.apply_fix to do nothing, simulating unfixable defect
    with patch.object(CoderSubAgent, "apply_fix", new_callable=AsyncMock) as mock_fix:
        mock_fix.return_value = ("No fix applied", 1000)

        result = await orchestrator.run_task(
            issue_id="issue-test-fail",
            issue_desc="Unfixable bug simulation",
            repo_path=fixture_repo,
            budget_limit=150_000,
            max_retries=2,
            mode="standalone",
        )

        assert result.status == "FAILED"
        assert result.retries_used == 2
        assert not result.final_report.passed
