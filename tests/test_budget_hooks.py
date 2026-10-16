"""Unit tests for budget enforcement hooks, SQLite state, and safety policies."""
import os
import tempfile

import pytest

from src.agents.hooks import BudgetGuard, TurnUsage
from src.agents.policies import (
    Decision,
    PolicyEngine,
    create_agent_policies,
    is_dangerous_command,
    is_git_push,
    is_outside_workspace,
)
from src.storage.db import (
    create_task,
    get_task,
    init_db,
)


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.mark.asyncio
async def test_turn_usage_parsing():
    # Test from dict
    d = {
        "prompt_token_count": 1200,
        "candidates_token_count": 350,
        "thoughts_token_count": 500,
        "total_token_count": 2050,
    }
    usage = TurnUsage.from_sdk_metadata(d)
    assert usage.prompt_tokens == 1200
    assert usage.candidates_tokens == 350
    assert usage.thoughts_tokens == 500
    assert usage.total_tokens == 2050

    # Test from mock object with attributes
    class MockUsage:
        prompt_token_count = 100
        candidates_token_count = 50
        thoughts_token_count = 20
        total_token_count = 170

    usage_obj = TurnUsage.from_sdk_metadata(MockUsage())
    assert usage_obj.total_tokens == 170


@pytest.mark.asyncio
async def test_budget_metering_and_accumulation(temp_db):
    task_id = "test-accumulate"
    create_task(task_id=task_id, budget_limit=10_000, turn_limit=10, db_path=temp_db)

    guard = BudgetGuard(task_id=task_id, budget_limit=10_000, turn_limit=10, db_path=temp_db)

    # Turn 1: 3000 tokens
    res1 = await guard.handle_post_turn(
        turn_index=1,
        usage_data={"total_token_count": 3000, "prompt_token_count": 2500, "candidates_token_count": 500},
    )
    assert res1["cumulative_tokens"] == 3000
    assert not res1["budget_exceeded"]
    assert not guard.is_paused
    assert guard.proceed_event.is_set()

    # Turn 2: 4000 tokens (cumulative 7000)
    res2 = await guard.handle_post_turn(
        turn_index=2,
        usage_data={"total_token_count": 4000, "prompt_token_count": 3000, "candidates_token_count": 1000},
    )
    assert res2["cumulative_tokens"] == 7000
    assert not res2["budget_exceeded"]
    assert not guard.is_paused


@pytest.mark.asyncio
async def test_budget_ceiling_halt_gate(temp_db):
    task_id = "test-halt"
    create_task(task_id=task_id, budget_limit=5_000, turn_limit=10, db_path=temp_db)

    callback_called = False

    def on_exceeded(guard_ref, record):
        nonlocal callback_called
        callback_called = True

    guard = BudgetGuard(
        task_id=task_id,
        budget_limit=5_000,
        turn_limit=10,
        db_path=temp_db,
        on_budget_exceeded=on_exceeded,
    )

    # Turn 1: 3000 tokens (under 5000)
    await guard.handle_post_turn(turn_index=1, usage_data={"total_tokens": 3000})
    assert not guard.is_paused
    assert not callback_called

    # Turn 2: 2500 tokens (cumulative 5500 >= 5000 ceiling)
    res2 = await guard.handle_post_turn(turn_index=2, usage_data={"total_tokens": 2500})
    assert res2["cumulative_tokens"] == 5500
    assert res2["budget_exceeded"]
    assert guard.is_paused
    assert not guard.proceed_event.is_set()
    assert callback_called

    # Verify task status in SQLite is updated to PAUSED_BUDGET
    db_task = get_task(task_id, db_path=temp_db)
    assert db_task["status"] == "PAUSED_BUDGET"

    # Verify wait_if_paused times out while paused
    can_proceed = await guard.wait_if_paused(timeout=0.05)
    assert not can_proceed

    # Extend budget by 50,000 tokens
    guard.extend_budget(additional_tokens=50_000)
    assert not guard.is_paused
    assert guard.proceed_event.is_set()

    # Verify wait_if_paused resolves immediately
    can_proceed_after = await guard.wait_if_paused(timeout=0.05)
    assert can_proceed_after

    db_task_resumed = get_task(task_id, db_path=temp_db)
    assert db_task_resumed["status"] == "RUNNING"
    assert db_task_resumed["budget_limit"] == 55_000


@pytest.mark.asyncio
async def test_turn_limit_halt(temp_db):
    task_id = "test-turns"
    create_task(task_id=task_id, budget_limit=100_000, turn_limit=2, db_path=temp_db)

    guard = BudgetGuard(task_id=task_id, budget_limit=100_000, turn_limit=2, db_path=temp_db)

    await guard.handle_post_turn(turn_index=1, usage_data={"total_tokens": 500})
    assert not guard.is_paused

    # Turn 2 reaches turn_limit
    await guard.handle_post_turn(turn_index=2, usage_data={"total_tokens": 500})
    assert guard.is_paused
    db_task = get_task(task_id, db_path=temp_db)
    assert db_task["status"] == "PAUSED_BUDGET"


def test_safety_policies_predicates():
    # Test dangerous commands
    assert is_dangerous_command({"CommandLine": "rm -rf /"})
    assert is_dangerous_command({"CommandLine": "git reset --hard HEAD~1"})
    assert is_dangerous_command({"CommandLine": "git clean -fdx"})
    assert not is_dangerous_command({"CommandLine": "pytest tests/"})
    assert not is_dangerous_command({"CommandLine": "git status"})

    # Test git push
    assert is_git_push({"CommandLine": "git push origin main"})
    assert not is_git_push({"CommandLine": "git pull origin main"})

    # Test workspace boundary
    workspace_dir = "/tmp/fake_workspace"
    check_boundary = is_outside_workspace(workspace_dir)

    assert not check_boundary({"AbsolutePath": "/tmp/fake_workspace/file.py"})
    assert check_boundary({"AbsolutePath": "/etc/passwd"})
    assert check_boundary({"AbsolutePath": "/tmp/other_dir/secret.txt"})


def test_policy_engine_evaluation():
    rules = create_agent_policies(workspace_path="/tmp/workspace")
    engine = PolicyEngine(rules)

    # Shell dangerous command -> DENY
    assert engine.evaluate("run_command", {"CommandLine": "rm -rf /tmp"}) == Decision.DENY

    # git push -> ASK_USER
    assert engine.evaluate("run_command", {"CommandLine": "git push origin fix/123"}) == Decision.ASK_USER

    # Safe command -> ALLOW
    assert engine.evaluate("run_command", {"CommandLine": "pytest -v"}) == Decision.ALLOW

    # File write outside workspace -> DENY
    assert engine.evaluate("write_to_file", {"TargetFile": "/etc/hosts"}) == Decision.DENY

    # File write inside workspace -> ALLOW
    assert engine.evaluate("write_to_file", {"TargetFile": "/tmp/workspace/main.py"}) == Decision.ALLOW
