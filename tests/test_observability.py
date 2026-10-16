"""
Comprehensive Test Suite for Streamlit Observability Dashboard & Telemetry Loader.
Verifies Issue #8 acceptance criteria:
1. Standalone entrypoints and imports.
2. Task directory querying with search and status filters (RUNNING, COMPLETED, FAILED, PAUSED_BUDGET).
3. Plan inspector data modeling and living spec extraction.
4. Multi-agent turn timeline & waterfall formatting.
5. Token burn analytics across personas (Atlas, Forge, Sentinel, Vivek, Mira, Dhruv).
6. QA report & git diff extraction.
7. Active running task handling and SQLite WAL mode.
"""

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.observability.data_loader import (
    TaskDetailView,
    compute_token_analytics,
    extract_qa_and_diff,
    load_task_detail,
)
from src.schemas import (
    ImplementationPlan,
    LivingSpec,
    QATestReport,
    SubTask,
    ToolInvocation,
    ToolResultRecord,
    TurnRecord,
)
from src.storage.db import (
    create_task,
    get_connection,
    init_db,
    list_tasks,
    record_turn_tokens,
    update_task_status,
)
from src.storage.session_manager import SessionManager


@pytest.fixture
def obs_storage():
    """Provides isolated temporary database and sessions directory."""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_obs_"))
    db_path = str(temp_dir / "state.db")
    sessions_dir = str(temp_dir / "sessions")
    init_db(db_path)

    yield {"dir": temp_dir, "db_path": db_path, "sessions_dir": sessions_dir}

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_sqlite_wal_mode_enabled(obs_storage):
    """Verify SQLite connection enables WAL journal mode and busy timeout."""
    db_path = obs_storage["db_path"]
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("PRAGMA journal_mode;")
        row = cursor.fetchone()
        assert row[0].lower() == "wal"

        cursor = conn.execute("PRAGMA busy_timeout;")
        timeout_row = cursor.fetchone()
        assert timeout_row[0] >= 5000
    finally:
        conn.close()


def test_list_tasks_filtering(obs_storage):
    """Verify list_tasks with status filters and keyword search."""
    db_path = obs_storage["db_path"]

    create_task("task-101", issue_id="issue-101", db_path=db_path)
    update_task_status("task-101", "RUNNING", db_path=db_path)

    create_task("task-102", issue_id="issue-102", db_path=db_path)
    update_task_status("task-102", "COMPLETED", db_path=db_path)

    create_task("task-103", issue_id="issue-203", db_path=db_path)
    update_task_status("task-103", "FAILED", db_path=db_path)

    create_task("task-104", issue_id="issue-204", db_path=db_path)
    update_task_status("task-104", "PAUSED_BUDGET", db_path=db_path)

    # 1. Fetch all
    all_tasks = list_tasks(db_path=db_path)
    assert len(all_tasks) == 4

    # 2. Filter by status
    running = list_tasks(status="RUNNING", db_path=db_path)
    assert len(running) == 1
    assert running[0]["task_id"] == "task-101"

    completed = list_tasks(status="COMPLETED", db_path=db_path)
    assert len(completed) == 1
    assert completed[0]["task_id"] == "task-102"

    paused = list_tasks(status="PAUSED_BUDGET", db_path=db_path)
    assert len(paused) == 1
    assert paused[0]["task_id"] == "task-104"

    # 3. Search query
    searched = list_tasks(search="203", db_path=db_path)
    assert len(searched) == 1
    assert searched[0]["task_id"] == "task-103"

    searched_task = list_tasks(search="task-101", db_path=db_path)
    assert len(searched_task) == 1
    assert searched_task[0]["task_id"] == "task-101"

    # 4. Limit
    limited = list_tasks(limit=2, db_path=db_path)
    assert len(limited) == 2


def test_token_burn_analytics_across_personas(obs_storage):
    """Verify token burn computation across multiple personas (Atlas, Forge, Sentinel, Vivek, Mira, Dhruv)."""
    db_path = obs_storage["db_path"]
    task_id = "task-burn-test"
    create_task(task_id, issue_id="issue-burn", budget_limit=10_000, db_path=db_path)

    record_turn_tokens(task_id, 1, 400, 400, 200, 1000, db_path=db_path)
    record_turn_tokens(task_id, 2, 800, 800, 400, 2000, db_path=db_path)
    record_turn_tokens(task_id, 3, 200, 200, 100, 500, db_path=db_path)

    turns = [
        TurnRecord(
            turn_index=1,
            persona="Mira (Product Specifier)",
            role="specifier",
            action="Clarified Acceptance Criteria",
            tokens_spent=1000,
            timestamp=datetime.now(UTC).isoformat(),
        ),
        TurnRecord(
            turn_index=2,
            persona="Atlas (Lead Architect)",
            role="orchestrator",
            action="Created Blueprint",
            tokens_spent=2000,
            timestamp=datetime.now(UTC).isoformat(),
        ),
        TurnRecord(
            turn_index=3,
            persona="Sentinel (QA Automation)",
            role="qa",
            action="Ran Baseline Tests",
            tokens_spent=500,
            timestamp=datetime.now(UTC).isoformat(),
        ),
    ]

    task_rec = {"task_id": task_id, "budget_limit": 10_000, "turn_limit": 15}
    turn_hist = [
        {
            "turn_index": 1,
            "prompt_tokens": 400,
            "candidates_tokens": 400,
            "thoughts_tokens": 200,
            "total_tokens": 1000,
            "cumulative_tokens": 1000,
        },
        {
            "turn_index": 2,
            "prompt_tokens": 800,
            "candidates_tokens": 800,
            "thoughts_tokens": 400,
            "total_tokens": 2000,
            "cumulative_tokens": 3000,
        },
        {
            "turn_index": 3,
            "prompt_tokens": 200,
            "candidates_tokens": 200,
            "thoughts_tokens": 100,
            "total_tokens": 500,
            "cumulative_tokens": 3500,
        },
    ]

    analytics = compute_token_analytics(task_rec, turn_hist, turns)
    assert analytics.total_tokens == 3500
    assert analytics.budget_limit == 10_000
    assert analytics.percentage_budget_used == 35.0
    assert not analytics.is_budget_exceeded
    assert len(analytics.burn_records) == 3

    # Check persona allocations
    personas = {p.persona: p.tokens for p in analytics.persona_allocations}
    assert "Atlas (Architect)" in personas
    assert personas["Atlas (Architect)"] == 2000
    assert "Mira (Specifier)" in personas
    assert personas["Mira (Specifier)"] == 1000
    assert "Sentinel (QA)" in personas
    assert personas["Sentinel (QA)"] == 500


def test_token_burn_budget_exceeded():
    """Verify is_budget_exceeded trips when cumulative spend exceeds ceiling."""
    task_rec = {"task_id": "task-exceeded", "budget_limit": 5000, "turn_limit": 10}
    turn_hist = [
        {
            "turn_index": 1,
            "prompt_tokens": 3000,
            "candidates_tokens": 2000,
            "thoughts_tokens": 500,
            "total_tokens": 5500,
            "cumulative_tokens": 5500,
        }
    ]
    analytics = compute_token_analytics(task_rec, turn_hist, [])
    assert analytics.total_tokens == 5500
    assert analytics.is_budget_exceeded is True
    assert analytics.percentage_budget_used == 110.0


def test_extract_qa_and_diff_from_trajectory():
    """Verify QA report and git diff extraction from SessionTrajectory and TurnRecord fallbacks."""
    report = QATestReport(
        passed=True,
        command_executed="pytest tests/",
        total_tests=5,
        failed_tests=0,
    )
    diff = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"

    # 1. From trajectory fields directly
    session_mgr = SessionManager()
    traj = session_mgr.init_session("task-qa-test", "issue-1")
    traj.final_report = report
    traj.git_diff = diff

    extracted_report, extracted_diff = extract_qa_and_diff(traj, [])
    assert extracted_report is not None
    assert extracted_report.passed is True
    assert extracted_report.total_tests == 5
    assert extracted_diff == diff

    # 2. Fallback to turn details
    turns = [
        TurnRecord(
            turn_index=1,
            persona="Sentinel (QA Automation)",
            role="qa",
            action="Ran Verification",
            tokens_spent=300,
            timestamp=datetime.now(UTC).isoformat(),
            details={
                "passed": False,
                "failed_tests": 2,
                "total_tests": 4,
                "command_executed": "pytest tests/test_core.py",
            },
        ),
        TurnRecord(
            turn_index=2,
            persona="Forge (SWE Sub-Agent)",
            role="coder",
            action="Applied Patch",
            tokens_spent=800,
            timestamp=datetime.now(UTC).isoformat(),
            details={"git_diff": "diff --git a/src.py b/src.py\n+fixed"},
        ),
    ]
    extracted_report_fallback, extracted_diff_fallback = extract_qa_and_diff(
        None, turns
    )
    assert extracted_report_fallback is not None
    assert extracted_report_fallback.passed is False
    assert extracted_report_fallback.failed_tests == 2
    assert "fixed" in extracted_diff_fallback


def test_load_task_detail_end_to_end(obs_storage):
    """Verify full TaskDetailView reconstitution including plan, spec, turns, and telemetry."""
    db_path = obs_storage["db_path"]
    sessions_dir = obs_storage["sessions_dir"]
    session_mgr = SessionManager(db_path=db_path, sessions_dir=sessions_dir)

    task_id = "task-full-e2e"
    create_task(task_id, issue_id="issue-555", budget_limit=50_000, db_path=db_path)

    plan = ImplementationPlan(
        issue_number=555,
        summary="Refactor auth token expiration logic",
        branch_name="fix/issue-555",
        subtasks=[
            SubTask(
                id="sub-1",
                title="Patch auth check in session.py",
                target_files=["src/auth/session.py"],
                acceptance_criteria="Boundary condition handles expired delta cleanly",
            )
        ],
        estimated_token_budget=20_000,
    )
    spec = LivingSpec(
        task_id=task_id,
        version=1,
        requirements=["Never log out active users with valid timestamp"],
        architecture_decisions=["Use inclusive bounds"],
        review_state={"approved": True},
        open_questions=[],
        updated_at=datetime.now(UTC).isoformat(),
    )
    session_mgr.init_session(
        task_id=task_id, issue_id="issue-555", living_spec=spec, plan=plan
    )

    turn = TurnRecord(
        turn_index=1,
        persona="Atlas (Lead Architect)",
        role="orchestrator",
        action="Synthesized plan",
        thinking_level="HIGH",
        prompt="Analyze defect in session validity check",
        thoughts="The boundary check uses strictly less than instead of less than or equal to",
        tool_calls=[
            ToolInvocation(
                tool_name="view_file", arguments={"path": "src/auth/session.py"}
            )
        ],
        tool_results=[
            ToolResultRecord(tool_name="view_file", output="def check_validity(): ...")
        ],
        response_text="Plan drafted",
        tokens_spent=2500,
        timestamp=datetime.now(UTC).isoformat(),
    )
    session_mgr.append_turn(task_id, turn)
    record_turn_tokens(task_id, 1, 1000, 1000, 500, 2500, db_path=db_path)

    detail: TaskDetailView = load_task_detail(
        task_id, db_path=db_path, sessions_dir=sessions_dir
    )

    assert detail.task_id == task_id
    assert detail.plan is not None
    assert detail.plan.summary == "Refactor auth token expiration logic"
    assert len(detail.plan.subtasks) == 1
    assert detail.living_spec is not None
    assert len(detail.living_spec.requirements) == 1
    assert len(detail.turns) == 1
    assert detail.turns[0].persona == "Atlas (Lead Architect)"
    assert detail.turns[0].thinking_level == "HIGH"
    assert len(detail.turns[0].tool_calls) == 1
    assert detail.analytics.total_tokens == 2500


def test_active_running_task_graceful_handling(obs_storage):
    """Verify actively running task without session file on disk is handled without crashing."""
    db_path = obs_storage["db_path"]
    sessions_dir = obs_storage["sessions_dir"]

    task_id = "task-actively-running"
    create_task(task_id, issue_id="issue-active", db_path=db_path)

    detail = load_task_detail(task_id, db_path=db_path, sessions_dir=sessions_dir)
    assert detail.task_id == task_id
    assert detail.task_record is not None
    assert detail.task_record["status"] == "RUNNING"
    assert detail.trajectory is None
    assert detail.plan is None
    assert detail.turns == []
    assert detail.analytics.total_tokens == 0


def test_trace_viewer_module_import():
    """Verify trace_viewer.py CLI entrypoint can be imported and paths resolved."""
    import tools.trace_viewer as tv

    assert tv.APP_PATH.exists()
    assert tv.APP_PATH.name == "app.py"
