"""
Unit and integration tests for Slack Multi-Persona bridge, Block Kit approvals, and Bot handlers.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.slack.approvals import (
    build_budget_exhaustion_blocks,
    build_draft_pr_blocks,
    build_policy_gate_blocks,
)
from src.slack.bot import SlackWarRoomBot
from src.slack.personas import get_persona, post_agent_message
from src.storage.db import (
    create_task,
    get_slack_events,
    get_task_by_thread_ts,
    init_db,
    record_slack_event,
    update_task_thread_ts,
)


def test_personas_mapping_and_retrieval():
    assert get_persona("orchestrator")["username"] == "Atlas (Lead Architect)"
    assert get_persona("atlas")["icon_emoji"] == ":compass:"
    assert get_persona("coder")["username"] == "Forge (SWE Sub-Agent)"
    assert get_persona("forge")["icon_emoji"] == ":hammer_and_wrench:"
    assert get_persona("qa")["username"] == "Sentinel (QA Automation)"
    assert get_persona("sentinel")["icon_emoji"] == ":shield:"
    assert get_persona("warden")["username"] == "Quota Warden"
    assert get_persona("unknown_role")["username"] == "Atlas (Lead Architect)"


@pytest.mark.asyncio
async def test_post_agent_message_custom_profile():
    mock_client = MagicMock()
    mock_client.chat_postMessage = AsyncMock(
        return_value={"ok": True, "ts": "12345.6789"}
    )

    res = await post_agent_message(
        client=mock_client,
        channel="C12345",
        role="coder",
        text="Fixing defect in calculator.py",
        thread_ts="12345.0000",
    )

    assert res["ok"] is True
    mock_client.chat_postMessage.assert_called_once_with(
        channel="C12345",
        text="Fixing defect in calculator.py",
        username="Forge (SWE Sub-Agent)",
        icon_emoji=":hammer_and_wrench:",
        thread_ts="12345.0000",
    )


def test_block_kit_builders():
    # Budget exhaustion blocks
    budget_blocks = build_budget_exhaustion_blocks(
        task_id="task-123",
        current_tokens=155_000,
        budget_limit=150_000,
        turn_count=15,
    )
    assert len(budget_blocks) >= 4
    action_block = next(b for b in budget_blocks if b["type"] == "actions")
    action_ids = [btn["action_id"] for btn in action_block["elements"]]
    assert "grant_budget_50k" in action_ids
    assert "abort_task" in action_ids

    # Policy gate blocks
    policy_blocks = build_policy_gate_blocks(
        task_id="task-123",
        command="git push origin fix/123",
        reason="Remote repository push requires human approval",
    )
    assert len(policy_blocks) >= 3
    action_block = next(b for b in policy_blocks if b["type"] == "actions")
    action_ids = [btn["action_id"] for btn in action_block["elements"]]
    assert "approve_command" in action_ids
    assert "deny_command" in action_ids

    # Draft PR blocks
    pr_blocks = build_draft_pr_blocks(
        task_id="task-123",
        issue_id="issue-42",
        branch_name="fix/issue-42",
        diff_stats="1 file changed, 1 insertion(+), 1 deletion(-)",
        test_summary="3 passed in 0.05s",
    )
    assert len(pr_blocks) >= 4
    action_block = next(b for b in pr_blocks if b["type"] == "actions")
    action_ids = [btn["action_id"] for btn in action_block["elements"]]
    assert "approve_draft_pr" in action_ids
    assert "request_revision" in action_ids


def test_db_thread_ts_mapping(tmp_path):
    db_file = str(tmp_path / "test_slack_state.db")
    init_db(db_file)

    task = create_task(
        task_id="task-slack-test",
        issue_id="issue-99",
        slack_thread_ts="1712345678.000100",
        budget_limit=100_000,
        db_path=db_file,
    )
    assert task["task_id"] == "task-slack-test"

    found = get_task_by_thread_ts("1712345678.000100", db_path=db_file)
    assert found is not None
    assert found["task_id"] == "task-slack-test"
    assert found["budget_limit"] == 100_000

    # Update thread_ts
    update_task_thread_ts("task-slack-test", "1712345678.000999", db_path=db_file)
    assert get_task_by_thread_ts("1712345678.000100", db_path=db_file) is None
    assert (
        get_task_by_thread_ts("1712345678.000999", db_path=db_file)["task_id"]
        == "task-slack-test"
    )

    # Audit events
    record_slack_event(
        event_type="test_click",
        payload={"btn": "ok"},
        task_id="task-slack-test",
        user_id="U12345",
        db_path=db_file,
    )
    events = get_slack_events(task_id="task-slack-test", db_path=db_file)
    assert len(events) == 1
    assert events[0]["event_type"] == "test_click"
    assert "ok" in events[0]["payload"]


@pytest.mark.asyncio
async def test_bot_async_gate_resolution():
    bot = SlackWarRoomBot(bot_token=None, app_token=None)
    gate_event = bot.register_gate("gate-test-1")
    assert not gate_event.is_set()

    # Launch waiting task
    async def waiter():
        return await bot.wait_for_decision("gate-test-1", timeout=2.0)

    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)

    bot.resolve_gate("gate-test-1", {"action": "grant_budget_50k", "approved": True})
    decision = await waiter_task

    assert decision is not None
    assert decision["approved"] is True
    assert decision["action"] == "grant_budget_50k"


@pytest.mark.asyncio
async def test_bot_steering_queue():
    bot = SlackWarRoomBot(bot_token=None, app_token=None)
    task_id = "task-steer-test"

    # Initially empty
    assert await bot.get_steering_instruction(task_id) is None

    # Simulate incoming message enqueued by listener
    bot.steering_queues[task_id] = asyncio.Queue()
    await bot.steering_queues[task_id].put(
        "Please also check edge cases for negative inputs."
    )

    instruction = await bot.get_steering_instruction(task_id)
    assert instruction == "Please also check edge cases for negative inputs."
    assert await bot.get_steering_instruction(task_id) is None
