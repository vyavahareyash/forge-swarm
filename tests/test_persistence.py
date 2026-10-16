"""
Comprehensive Test Suite for Hybrid State & Trajectory Persistence with Living Spec.

Verifies:
1. SQLite living_specs table schema, CRUD operations, and versioning.
2. TurnRecord serialization and deserialization with prompts, thoughts, and tool events.
3. Atomic disk writing and incremental session trajectory appending.
4. Conversation rehydration by task_id and slack_thread_ts.
5. Living Spec prompt context injection with token sliding window pruning.
6. Backward compatibility with legacy session JSON.
7. End-to-end Orchestrator persistence loop.
"""
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.agents.orchestrator import Orchestrator
from src.schemas import (
    LivingSpec,
    ToolInvocation,
    ToolResultRecord,
    TurnRecord,
)
from src.storage.db import (
    create_task,
    get_connection,
    get_living_spec,
    init_db,
    save_living_spec,
    update_living_spec,
)
from src.storage.session_manager import SessionManager


@pytest.fixture
def temp_storage():
    """Provides isolated temporary database and sessions directory."""
    temp_dir = Path(tempfile.mkdtemp(prefix="test_storage_"))
    db_path = str(temp_dir / "test_state.db")
    sessions_dir = str(temp_dir / "sessions")
    init_db(db_path)

    yield {"dir": temp_dir, "db_path": db_path, "sessions_dir": sessions_dir}

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_living_specs_table_schema(temp_storage):
    """Verify living_specs table exists in SQLite schema with foreign key."""
    db_path = temp_storage["db_path"]
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='living_specs';"
        )
        assert cursor.fetchone() is not None

        # Check table columns
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(living_specs);").fetchall()]
        assert "task_id" in cols
        assert "version" in cols
        assert "requirements" in cols
        assert "architecture_decisions" in cols
        assert "review_state" in cols
        assert "open_questions" in cols
        assert "updated_at" in cols
    finally:
        conn.close()


def test_living_spec_crud_and_versioning(temp_storage):
    """Verify saving, retrieving, and updating LivingSpec in SQLite."""
    db_path = temp_storage["db_path"]
    task_id = "task-test-spec-1"
    create_task(task_id=task_id, issue_id="issue-1", db_path=db_path)

    spec = LivingSpec(
        task_id=task_id,
        version=1,
        requirements=["Acceptance criteria 1", "Acceptance criteria 2"],
        architecture_decisions=["Use SQLite WAL mode", "Adopt TurnRecord model"],
        review_state={"status": "IN_PROGRESS", "retries": 0},
        open_questions=["Should cache be in memory?"],
        updated_at=datetime.now(UTC).isoformat(),
    )

    saved = save_living_spec(spec, db_path=db_path)
    assert saved["task_id"] == task_id
    assert saved["version"] == 1

    loaded = get_living_spec(task_id, db_path=db_path)
    assert loaded is not None
    assert loaded.task_id == task_id
    assert loaded.version == 1
    assert loaded.requirements == ["Acceptance criteria 1", "Acceptance criteria 2"]
    assert loaded.architecture_decisions == ["Use SQLite WAL mode", "Adopt TurnRecord model"]
    assert loaded.review_state["status"] == "IN_PROGRESS"
    assert loaded.open_questions == ["Should cache be in memory?"]

    # Test update_living_spec increments version
    updated = update_living_spec(
        task_id=task_id,
        requirements=["Updated criteria 1"],
        review_state={"status": "REVIEW_PASSED"},
        db_path=db_path,
    )
    assert updated is not None
    assert updated.version == 2
    assert updated.requirements == ["Updated criteria 1"]
    assert updated.architecture_decisions == ["Use SQLite WAL mode", "Adopt TurnRecord model"]
    assert updated.review_state["status"] == "REVIEW_PASSED"

    # Reload from DB to ensure persisted
    reloaded = get_living_spec(task_id, db_path=db_path)
    assert reloaded.version == 2
    assert reloaded.requirements == ["Updated criteria 1"]


def test_living_spec_cascade_delete(temp_storage):
    """Verify deleting a task cascades to its living_spec."""
    db_path = temp_storage["db_path"]
    task_id = "task-cascade-test"
    create_task(task_id=task_id, issue_id="issue-cascade", db_path=db_path)

    spec = LivingSpec(
        task_id=task_id,
        version=1,
        requirements=["Requirement"],
        architecture_decisions=[],
        review_state={},
        open_questions=[],
        updated_at=datetime.now(UTC).isoformat(),
    )
    save_living_spec(spec, db_path=db_path)
    assert get_living_spec(task_id, db_path=db_path) is not None

    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?;", (task_id,))
    finally:
        conn.close()

    assert get_living_spec(task_id, db_path=db_path) is None


def test_session_manager_init_and_atomic_write(temp_storage):
    """Verify SessionManager initializes session and writes atomically."""
    sm = SessionManager(
        db_path=temp_storage["db_path"],
        sessions_dir=temp_storage["sessions_dir"],
    )

    task_id = "task-atomic-1"
    create_task(task_id=task_id, issue_id="issue-atomic", db_path=temp_storage["db_path"])

    spec = LivingSpec(
        task_id=task_id,
        version=1,
        requirements=["Atomic requirement"],
        architecture_decisions=["Atomic file replacement"],
        review_state={},
        open_questions=[],
        updated_at=datetime.now(UTC).isoformat(),
    )

    session = sm.init_session(
        task_id=task_id,
        issue_id="issue-atomic",
        living_spec=spec,
    )

    assert session.task_id == task_id
    assert session.status == "RUNNING"
    assert session.turns == []

    session_file = Path(temp_storage["sessions_dir"]) / f"{task_id}.json"
    assert session_file.exists()

    # Verify no dangling temp files
    temp_files = list(Path(temp_storage["sessions_dir"]).glob(".tmp_*"))
    assert len(temp_files) == 0

    # Verify JSON content matches schema
    loaded = sm.load_session(task_id)
    assert loaded is not None
    assert loaded.task_id == task_id
    assert loaded.living_spec.requirements == ["Atomic requirement"]


def test_append_turn_incremental_and_typed_records(temp_storage):
    """Verify incremental turn persistence with typed prompts, thoughts, and tools."""
    sm = SessionManager(
        db_path=temp_storage["db_path"],
        sessions_dir=temp_storage["sessions_dir"],
    )

    task_id = "task-turns-1"
    create_task(task_id=task_id, issue_id="issue-turns", db_path=temp_storage["db_path"])
    sm.init_session(task_id=task_id, issue_id="issue-turns")

    turn1 = TurnRecord(
        turn_index=1,
        persona="Atlas (Lead Architect)",
        role="orchestrator",
        action="Formulated System Blueprint",
        thinking_level="HIGH",
        prompt="Design high-level architecture for issue-turns",
        thoughts="The system needs modular persistence and atomic write operations.",
        tool_calls=[
            ToolInvocation(
                tool_name="view_file",
                arguments={"path": "src/storage/db.py"},
                tool_call_id="call-1",
            )
        ],
        tool_results=[
            ToolResultRecord(
                tool_name="view_file",
                output="File contents loaded successfully",
                status="success",
                tool_call_id="call-1",
            )
        ],
        response_text="Completed architecture plan with 3 subtasks.",
        tokens_spent=1200,
        timestamp=datetime.now(UTC).isoformat(),
        details={"plan_summary": "Architecture plan"},
    )

    updated_session = sm.append_turn(task_id, turn1)
    assert len(updated_session.turns) == 1
    assert updated_session.tokens_spent == 1200

    turn2 = TurnRecord(
        turn_index=2,
        persona="Forge (SWE Sub-Agent)",
        role="coder",
        action="Applied Code Patch",
        thinking_level="MEDIUM",
        prompt="Implement persistence layer",
        thoughts="Writing SessionManager with atomic replace.",
        tool_calls=[],
        tool_results=[],
        response_text="Created src/storage/session_manager.py",
        tokens_spent=1800,
        timestamp=datetime.now(UTC).isoformat(),
        details={"files_modified": ["src/storage/session_manager.py"]},
    )

    final_session = sm.append_turn(task_id, turn2, status="SUCCESS", retries_used=1)
    assert len(final_session.turns) == 2
    assert final_session.tokens_spent == 3000
    assert final_session.status == "SUCCESS"
    assert final_session.retries_used == 1

    # Reload from disk to verify persistence
    reloaded = sm.load_session(task_id)
    assert len(reloaded.turns) == 2
    assert reloaded.turns[0].prompt == "Design high-level architecture for issue-turns"
    assert reloaded.turns[0].tool_calls[0].tool_name == "view_file"
    assert reloaded.turns[1].persona == "Forge (SWE Sub-Agent)"


def test_rehydration_by_task_id_and_thread_ts(temp_storage):
    """Verify rehydrating conversation context by task_id and by slack_thread_ts."""
    sm = SessionManager(
        db_path=temp_storage["db_path"],
        sessions_dir=temp_storage["sessions_dir"],
    )

    task_id = "task-rehydrate-1"
    thread_ts = "1792139999.000100"
    create_task(
        task_id=task_id,
        issue_id="issue-rehydrate",
        slack_thread_ts=thread_ts,
        db_path=temp_storage["db_path"],
    )

    spec = LivingSpec(
        task_id=task_id,
        version=1,
        requirements=["Rehydration requirement"],
        architecture_decisions=["Thread-aware lookup"],
        review_state={"phase": "ACTIVE"},
        open_questions=[],
        updated_at=datetime.now(UTC).isoformat(),
    )
    sm.init_session(task_id=task_id, issue_id="issue-rehydrate", living_spec=spec)

    sm.append_turn(
        task_id,
        TurnRecord(
            turn_index=1,
            persona="Atlas (Lead Architect)",
            role="orchestrator",
            action="Started Task",
            thinking_level="HIGH",
            tokens_spent=500,
            timestamp=datetime.now(UTC).isoformat(),
        ),
    )

    # 1. Rehydrate by task_id
    ctx1 = sm.rehydrate(task_id)
    assert ctx1 is not None
    assert ctx1.task["task_id"] == task_id
    assert ctx1.living_spec.requirements == ["Rehydration requirement"]
    assert len(ctx1.recent_turns) == 1

    # 2. Rehydrate by thread_ts
    ctx2 = sm.rehydrate(thread_ts)
    assert ctx2 is not None
    assert ctx2.task["task_id"] == task_id
    assert ctx2.living_spec.version == 1
    assert len(ctx2.recent_turns) == 1


def test_format_prompt_context_with_sliding_window(temp_storage):
    """Verify Living Spec injection and token-aware sliding window truncation."""
    sm = SessionManager(
        db_path=temp_storage["db_path"],
        sessions_dir=temp_storage["sessions_dir"],
    )

    task_id = "task-prompt-ctx-1"
    create_task(task_id=task_id, issue_id="issue-prompt-ctx", db_path=temp_storage["db_path"])

    spec = LivingSpec(
        task_id=task_id,
        version=3,
        requirements=["Always enforce budget limits", "Isolate in git worktree"],
        architecture_decisions=["Adopt SQLite WAL mode"],
        review_state={"review_passed": True},
        open_questions=["None"],
        updated_at="2026-10-16T12:00:00Z",
    )
    sm.init_session(task_id=task_id, issue_id="issue-prompt-ctx", living_spec=spec)

    # Append 5 turns with 1000 tokens each = 5000 tokens
    for i in range(1, 6):
        sm.append_turn(
            task_id,
            TurnRecord(
                turn_index=i,
                persona=f"Agent-{i}",
                role="coder",
                action=f"Executed Turn {i}",
                thinking_level="MEDIUM",
                prompt=f"Prompt for turn {i}",
                response_text=f"Response for turn {i}",
                tokens_spent=1000,
                timestamp="2026-10-16T12:00:00Z",
            ),
        )

    # Rehydrate with max_history_tokens=2500 -> should retain only last 2 turns
    ctx = sm.rehydrate(task_id, max_history_tokens=2500)
    assert len(ctx.recent_turns) == 2
    assert ctx.recent_turns[0].turn_index == 4
    assert ctx.recent_turns[1].turn_index == 5

    # Check formatted prompt contains <LIVING_SPEC> and <CONVERSATION_HISTORY>
    base_prompt = "Now implement unit test for factorial."
    formatted = sm.format_prompt_context(ctx, base_prompt)

    assert "<LIVING_SPEC>" in formatted
    assert "</LIVING_SPEC>" in formatted
    assert "Always enforce budget limits" in formatted
    assert "Adopt SQLite WAL mode" in formatted

    assert "<CONVERSATION_HISTORY>" in formatted
    assert "</CONVERSATION_HISTORY>" in formatted
    assert "[Turn 4] Agent-4" in formatted
    assert "[Turn 5] Agent-5" in formatted
    assert "[Turn 1] Agent-1" not in formatted  # Pruned by sliding window

    assert formatted.endswith(base_prompt)


def test_backward_compatibility_with_legacy_json(temp_storage):
    """Verify loading historical SwarmExecutionResult JSON format without errors."""
    sm = SessionManager(
        db_path=temp_storage["db_path"],
        sessions_dir=temp_storage["sessions_dir"],
    )

    task_id = "task-legacy-001"
    legacy_file = Path(temp_storage["sessions_dir"]) / f"{task_id}.json"

    legacy_payload = {
        "task_id": task_id,
        "issue_id": "issue-legacy",
        "status": "SUCCESS",
        "retries_used": 1,
        "git_diff": "+ test diff",
        "trajectory": [
            {
                "persona": "Atlas (Lead Architect)",
                "role": "orchestrator",
                "action": "Generated ImplementationPlan",
                "thinking_level": "HIGH",
                "tokens_spent": 2100,
                "details": {"plan_summary": "Legacy plan"},
            },
            {
                "persona": "Sentinel (QA Automation)",
                "role": "qa",
                "action": "Executed Baseline Test Suite",
                "thinking_level": "MINIMAL",
                "tokens_spent": 350,
                "details": {"passed": True},
            },
        ],
        "tokens_spent": 2450,
    }

    legacy_file.write_text(json.dumps(legacy_payload, indent=2))

    # Load legacy session
    loaded = sm.load_session(task_id)
    assert loaded is not None
    assert loaded.task_id == task_id
    assert loaded.status == "SUCCESS"
    assert len(loaded.turns) == 2
    assert loaded.turns[0].persona == "Atlas (Lead Architect)"
    assert loaded.turns[0].turn_index == 1
    assert loaded.turns[1].tokens_spent == 350


@pytest.mark.asyncio
async def test_orchestrator_persistence_integration(temp_storage):
    """Verify Orchestrator creates living spec and persists incremental turns during task execution."""
    from src.worktree import WorktreeManager
    from tests.fixtures.sample_repo import ensure_sample_repo

    repo_path = ensure_sample_repo(target_dir=temp_storage["dir"] / "sample_repo", reset=True)
    wm = WorktreeManager(workspace_root=str(temp_storage["dir"] / "workspaces"))

    orch = Orchestrator(
        workspace_manager=wm,
        db_path=temp_storage["db_path"],
        sessions_dir=temp_storage["sessions_dir"],
    )

    issue_id = "issue-orch-persist-1"
    desc = "Fix Fibonacci bug in calculator.py"

    result = await orch.run_task(
        issue_id=issue_id,
        issue_desc=desc,
        repo_path=repo_path,
        mode="standalone",
        cleanup_worktree=True,
    )

    assert result.status == "SUCCESS"
    task_id = result.task_id

    # Check SQLite living spec
    spec = get_living_spec(task_id, db_path=temp_storage["db_path"])
    assert spec is not None
    assert spec.task_id == task_id
    assert desc in spec.requirements[0]
    assert len(spec.architecture_decisions) > 0
    assert spec.review_state["status"] == "SUCCESS"
    assert spec.review_state["passed"] is True

    # Check disk session trajectory
    session_file = Path(temp_storage["sessions_dir"]) / f"{task_id}.json"
    assert session_file.exists()

    session = orch.session_manager.load_session(task_id)
    assert session is not None
    assert session.status == "SUCCESS"
    assert len(session.turns) >= 3  # Atlas plan, Sentinel initial test, Forge fix, Sentinel retest
    assert session.living_spec is not None

    # Check rehydration from session
    rehydrated = orch.session_manager.rehydrate(task_id)
    assert rehydrated is not None
    assert rehydrated.living_spec.task_id == task_id
    assert len(rehydrated.recent_turns) == len(session.turns)

