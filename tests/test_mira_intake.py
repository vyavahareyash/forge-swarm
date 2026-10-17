"""
Unit and integration tests for Mira Intake & Requirements Grilling Flow (#4).

Verifies:
1. GitHub issue polling for triage:agent and status:ready-for-dev.
2. Slack /agent solve slash command triggering draft issue & War-Room thread.
3. Mira interactive multi-turn grilling conversation and LivingSpec updates.
4. Block Kit acceptance criteria card rendering.
5. User sign-off via [Approve & Start Implementation]:
   - In-place mutation into audit log.
   - GitHub issue body update with clean markdown spec.
   - Label transition to status:ready-for-dev and removal of [Draft] title prefix.
   - Task status transition to READY_FOR_DEV.
"""

import json
import subprocess
from unittest.mock import AsyncMock

import pytest

from src.agents.mira import MiraIntakeAgent
from src.schemas import LivingSpec
from src.slack.approvals import build_spec_acceptance_blocks
from src.slack.bot import SlackWarRoomBot
from src.storage.db import (
    create_task,
    get_living_spec,
    get_task,
    get_task_by_thread_ts,
    init_db,
    save_living_spec,
)
from src.triggers.github_poller import GitHubPoller


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_mira.db")
    init_db(db_file)
    return db_file


# -----------------------------------------------------------------------------
# 1. GitHub Poller: Triage & Ready-for-Dev Label Management
# -----------------------------------------------------------------------------


def test_github_poller_triage_and_ready_for_dev(temp_db):
    executed_commands = []

    def mock_runner(cmd, **kwargs):
        executed_commands.append(cmd)
        cmd_str = " ".join(cmd)
        if "issue list" in cmd_str:
            if "triage:agent" in cmd_str:
                data = [
                    {
                        "number": 101,
                        "title": "Intake task for cache eviction",
                        "body": "Need cache eviction policy",
                        "labels": [{"name": "triage:agent"}],
                        "url": "https://github.com/org/repo/issues/101",
                    }
                ]
                return subprocess.CompletedProcess(
                    cmd, returncode=0, stdout=json.dumps(data), stderr=""
                )
            elif "needs-spec" in cmd_str:
                data = [
                    {
                        "number": 102,
                        "title": "Need spec for telemetry",
                        "body": "Clarify metrics",
                        "labels": [{"name": "needs-spec"}],
                        "url": "https://github.com/org/repo/issues/102",
                    }
                ]
                return subprocess.CompletedProcess(
                    cmd, returncode=0, stdout=json.dumps(data), stderr=""
                )
            elif "status:ready-for-dev" in cmd_str:
                data = [
                    {
                        "number": 103,
                        "title": "Spec approved ready for dev",
                        "body": "Approved spec",
                        "labels": [{"name": "status:ready-for-dev"}],
                        "url": "https://github.com/org/repo/issues/103",
                    }
                ]
                return subprocess.CompletedProcess(
                    cmd, returncode=0, stdout=json.dumps(data), stderr=""
                )
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="[]", stderr=""
            )
        elif "issue create" in cmd_str:
            return subprocess.CompletedProcess(
                cmd,
                returncode=0,
                stdout="https://github.com/org/repo/issues/104\n",
                stderr="",
            )
        elif "issue edit" in cmd_str:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    poller = GitHubPoller(
        repo="org/repo",
        db_path=temp_db,
        command_runner=mock_runner,
    )

    # 1. Fetch triage issues
    triage_issues = poller.fetch_triage_issues()
    assert len(triage_issues) == 2
    assert {i.number for i in triage_issues} == {101, 102}

    # 2. Fetch ready issues (both agent-ready and status:ready-for-dev)
    ready_issues = poller.fetch_ready_issues()
    assert any(i.number == 103 for i in ready_issues)

    # 3. Create draft issue
    draft = poller.create_draft_issue("New Feature Request", "User prompt details")
    assert draft.number == 104
    assert draft.title == "[Draft] New Feature Request"
    assert any(
        "issue" in cmd and "create" in cmd and "[Draft] New Feature Request" in cmd
        for cmd in executed_commands
    )

    # 4. Mark ready for dev
    poller.mark_ready_for_dev(104)
    assert any(
        "issue" in cmd
        and "edit" in cmd
        and "status:ready-for-dev" in cmd
        and "triage:agent" in cmd
        for cmd in executed_commands
    )

    # 5. Update issue spec
    poller.update_issue_spec(
        104, title="New Feature Request", body="## Updated Spec Body"
    )
    assert any(
        "issue" in cmd
        and "edit" in cmd
        and "New Feature Request" in cmd
        and "## Updated Spec Body" in cmd
        for cmd in executed_commands
    )


# -----------------------------------------------------------------------------
# 2. Mira Grilling Engine & LivingSpec Updates
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mira_grilling_engine(temp_db):
    agent = MiraIntakeAgent(db_path=temp_db)

    # 1. Initial Grilling Questions
    text, questions = await agent.generate_initial_grilling(
        title="[Draft] Cache Eviction Policy",
        description="We need an LRU cache eviction mechanism for our in-memory token store.",
    )
    assert "Mira" in text
    assert len(questions) >= 2
    assert "❓ **Q1**" in text
    assert "➡️" in text

    # 2. Multi-turn interaction
    task_id = "task-issue-105-12345"
    create_task(task_id, issue_id="issue-105", status="INTAKE", db_path=temp_db)
    initial_spec = LivingSpec(
        task_id=task_id,
        version=1,
        requirements=["Need LRU cache"],
        updated_at="2026-10-17T00:00:00Z",
    )
    save_living_spec(initial_spec, db_path=temp_db)

    # Human reply 1: providing specific guidance
    reply1, is_complete1, spec_data1 = await agent.process_reply(
        task_id=task_id,
        issue_id="issue-105",
        title="Cache Eviction Policy",
        user_reply="Yes, fail fast if the cache size is negative or zero.",
        current_spec=initial_spec,
    )
    assert not is_complete1
    assert spec_data1 is None
    assert "❓ **Q2**" in reply1 or "❓" in reply1

    # Check updated spec in DB
    updated_spec = get_living_spec(task_id, db_path=temp_db)
    assert len(updated_spec.requirements) == 2

    # Human reply 2: confirming readiness
    reply2, is_complete2, spec_data2 = await agent.process_reply(
        task_id=task_id,
        issue_id="issue-105",
        title="Cache Eviction Policy",
        user_reply="Looks good, ready to implement",
        current_spec=updated_spec,
    )
    assert is_complete2
    assert spec_data2 is not None
    assert spec_data2.title == "Cache Eviction Policy"
    assert len(spec_data2.acceptance_criteria) >= 3
    assert len(spec_data2.scope_and_boundaries) >= 2

    # 3. Format GitHub Issue Body
    issue_body = agent.format_github_issue_body(
        spec_data2, war_room_channel="dev-triage", thread_ts="1234.5678"
    )
    assert "## Summary" in issue_body
    assert "## Scope & Boundaries" in issue_body
    assert "## Acceptance Criteria" in issue_body
    assert "- [ ]" in issue_body
    assert "Slack War-Room (`#dev-triage` thread: `1234.5678`)" in issue_body


# -----------------------------------------------------------------------------
# 3. Block Kit Acceptance Card UI
# -----------------------------------------------------------------------------


def test_build_spec_acceptance_blocks():
    blocks = build_spec_acceptance_blocks(
        task_id="task-106",
        issue_id="issue-106",
        title="[Draft] Secure Token Vault",
        summary="Automated vault for tokens.",
        acceptance_criteria=[
            "Encrypt tokens using AES-GCM-256",
            "Zero plaintext tokens in session logs",
        ],
        scope_and_boundaries=["Confined to src/security/"],
        out_of_scope=["Modifying database drivers"],
        user_id="U12345",
    )

    assert any(b.get("type") == "header" for b in blocks)
    assert any("<@U12345>" in str(b) for b in blocks)

    # Check actions block
    actions_block = next(b for b in blocks if b.get("type") == "actions")
    assert actions_block["block_id"] == "spec_gate_task-106"
    elem_actions = [e["action_id"] for e in actions_block["elements"]]
    assert "approve_spec" in elem_actions
    assert "request_spec_revision" in elem_actions


# -----------------------------------------------------------------------------
# 4. Slack Bot: /agent Slash Command & War-Room Thread Spawning
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_slash_command_agent_solve(temp_db):
    bot = SlackWarRoomBot(
        bot_token="xoxb-mock",
        app_token="xapp-mock",
        default_channel="C_TRIAGE",
        db_path=temp_db,
    )

    mock_client = AsyncMock()
    mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1720000000.123"}
    mock_ack = AsyncMock()

    body = {
        "text": "solve Implement exponential backoff for GitHub API retries",
        "user_id": "U_CTL",
        "user_name": "lead_engineer",
        "channel_id": "C_TRIAGE",
    }

    # Simulate /agent command
    command_handler = next(
        listener.ack_function
        for listener in bot.app._async_listeners
        if getattr(listener, "ack_function", None)
        and listener.ack_function.__name__ == "handle_agent_command"
    )
    await command_handler(mock_ack, body, mock_client)

    # 1. Ack called
    mock_ack.assert_awaited_once()

    # 2. Root postMessage dispatched
    assert mock_client.chat_postMessage.await_count >= 2
    root_call = mock_client.chat_postMessage.call_args_list[0][1]
    assert root_call["channel"] == "C_TRIAGE"
    assert "New Task Intake:" in root_call["text"]

    # 3. Mira initial grilling posted in thread
    assert mock_client.chat_postMessage.await_count >= 2
    second_call = mock_client.chat_postMessage.call_args_list[1][1]
    assert second_call["thread_ts"] == "1720000000.123"
    assert second_call["username"] == "Mira (Product Specifier)"
    assert "❓ **Q1**" in second_call["text"]

    # 4. Task in DB
    task = get_task_by_thread_ts("1720000000.123", db_path=temp_db)
    assert task is not None
    assert task["status"] == "INTAKE"


# -----------------------------------------------------------------------------
# 5. Slack Bot: Interactive Grilling Conversation Progression
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_thread_grilling_progression(temp_db):
    bot = SlackWarRoomBot(
        bot_token="xoxb-mock",
        app_token="xapp-mock",
        default_channel="C_TRIAGE",
        db_path=temp_db,
    )

    task_id = "task-issue-107-12345"
    thread_ts = "1720000001.000"
    create_task(
        task_id,
        issue_id="issue-107",
        slack_thread_ts=thread_ts,
        status="INTAKE",
        db_path=temp_db,
    )
    save_living_spec(
        LivingSpec(
            task_id=task_id,
            version=1,
            requirements=["Backoff logic"],
            updated_at="2026-10-17T00:00:00Z",
        ),
        db_path=temp_db,
    )

    mock_client = AsyncMock()
    mock_client.reactions_add.return_value = {"ok": True}
    mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1720000001.100"}

    msg_handler = next(
        listener.ack_function
        for listener in bot.app._async_listeners
        if getattr(listener, "ack_function", None)
        and listener.ack_function.__name__ == "handle_thread_message"
    )

    # Turn 1: Guidance reply
    event_turn1 = {
        "channel": "C_TRIAGE",
        "thread_ts": thread_ts,
        "text": "Max 5 retries with jitter",
        "user": "U_CTL",
    }
    await msg_handler(event_turn1, mock_client)

    assert mock_client.chat_postMessage.await_count == 1
    mira_call1 = mock_client.chat_postMessage.call_args[1]
    assert mira_call1["username"] == "Mira (Product Specifier)"
    assert "Max 5 retries with jitter" in mira_call1["text"]

    # Turn 2: Completion confirmation
    mock_client.chat_postMessage.reset_mock()
    event_turn2 = {
        "channel": "C_TRIAGE",
        "thread_ts": thread_ts,
        "text": "Looks good, ready to approve",
        "user": "U_CTL",
    }
    await msg_handler(event_turn2, mock_client)

    assert mock_client.chat_postMessage.await_count == 1
    mira_call2 = mock_client.chat_postMessage.call_args[1]
    assert "Requirements Aligned!" in mira_call2["text"]
    assert "blocks" in mira_call2
    # Verify [Approve & Start Implementation] button is present
    actions = next(b for b in mira_call2["blocks"] if b.get("type") == "actions")
    assert any(elem["action_id"] == "approve_spec" for elem in actions["elements"])


# -----------------------------------------------------------------------------
# 6. Slack Bot: [Approve & Start Implementation] Action & In-Place Mutation
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_approve_spec_action(temp_db):
    executed_commands = []

    def mock_runner(cmd, **kwargs):
        executed_commands.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    mock_poller = GitHubPoller(
        repo="org/repo", db_path=temp_db, command_runner=mock_runner
    )

    bot = SlackWarRoomBot(
        bot_token="xoxb-mock",
        app_token="xapp-mock",
        default_channel="C_TRIAGE",
        db_path=temp_db,
        poller=mock_poller,
    )

    task_id = "task-issue-108-12345"
    thread_ts = "1720000002.000"
    create_task(
        task_id,
        issue_id="issue-108",
        slack_thread_ts=thread_ts,
        status="GRILLING",
        db_path=temp_db,
    )
    save_living_spec(
        LivingSpec(
            task_id=task_id,
            version=2,
            requirements=["Retry with jitter", "Fail fast on 401 Unauthorized"],
            updated_at="2026-10-17T00:00:00Z",
        ),
        db_path=temp_db,
    )

    mock_client = AsyncMock()
    mock_client.chat_update.return_value = {"ok": True}
    mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1720000002.200"}
    mock_ack = AsyncMock()

    body = {
        "actions": [{"action_id": "approve_spec", "value": task_id}],
        "user": {"id": "U_CTL", "username": "lead_engineer"},
        "channel": {"id": "C_TRIAGE"},
        "container": {"thread_ts": thread_ts, "message_ts": "1720000002.100"},
        "message": {
            "ts": "1720000002.100",
            "blocks": [
                {
                    "type": "actions",
                    "block_id": f"spec_gate_{task_id}",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "approve_spec",
                            "value": task_id,
                        }
                    ],
                }
            ],
        },
    }

    approve_handler = next(
        listener.ack_function
        for listener in bot.app._async_listeners
        if getattr(listener, "ack_function", None)
        and listener.ack_function.__name__ == "handle_approve_spec"
    )
    await approve_handler(mock_ack, body, mock_client)

    # 1. Acked
    mock_ack.assert_awaited_once()

    # 2. In-place mutation called via chat_update
    mock_client.chat_update.assert_awaited_once()
    mutated_blocks = mock_client.chat_update.call_args[1]["blocks"]
    assert any(
        b.get("type") == "context" and "Approved" in str(b) for b in mutated_blocks
    )
    assert not any(b.get("type") == "actions" for b in mutated_blocks)

    # 3. GitHub issue updated
    assert any(
        "issue" in cmd
        and "edit" in cmd
        and "108" in cmd
        and "## Summary" in " ".join(cmd)
        for cmd in executed_commands
    )
    assert any(
        "issue" in cmd
        and "edit" in cmd
        and "status:ready-for-dev" in cmd
        and "triage:agent" in cmd
        for cmd in executed_commands
    )

    # 4. SQLite task status updated to READY_FOR_DEV
    task = get_task(task_id, db_path=temp_db)
    assert task["status"] == "READY_FOR_DEV"

    # 5. Confirmation message posted in War-Room thread (Mira handoff + Arjun takeover)
    assert mock_client.chat_postMessage.await_count >= 1
    call_texts = [
        call[1]["text"] for call in mock_client.chat_postMessage.call_args_list
    ]
    assert any("Requirements Finalized & Approved" in t for t in call_texts)
    assert any("Arjun (Lead Architect)" in t for t in call_texts)


# -----------------------------------------------------------------------------
# 7. Slack Bot: [Request Revisions] Action & Gate Resolution
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_request_spec_revision_action(temp_db):
    bot = SlackWarRoomBot(
        bot_token="xoxb-mock",
        app_token="xapp-mock",
        default_channel="C_TRIAGE",
        db_path=temp_db,
    )

    task_id = "task-issue-109-12345"
    thread_ts = "1720000003.000"
    create_task(
        task_id,
        issue_id="issue-109",
        slack_thread_ts=thread_ts,
        status="GRILLING",
        db_path=temp_db,
    )

    mock_client = AsyncMock()
    mock_client.chat_update.return_value = {"ok": True}
    mock_client.chat_postMessage.return_value = {"ok": True, "ts": "1720000003.200"}
    mock_ack = AsyncMock()

    body = {
        "actions": [{"action_id": "request_spec_revision", "value": task_id}],
        "user": {"id": "U_CTL", "username": "lead_engineer"},
        "channel": {"id": "C_TRIAGE"},
        "container": {"thread_ts": thread_ts, "message_ts": "1720000003.100"},
        "message": {
            "ts": "1720000003.100",
            "blocks": [
                {
                    "type": "actions",
                    "block_id": f"spec_gate_{task_id}",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "request_spec_revision",
                            "value": task_id,
                        }
                    ],
                }
            ],
        },
    }

    revision_handler = next(
        listener.ack_function
        for listener in bot.app._async_listeners
        if getattr(listener, "ack_function", None)
        and listener.ack_function.__name__ == "handle_request_spec_revision"
    )
    await revision_handler(mock_ack, body, mock_client)

    mock_ack.assert_awaited_once()
    mock_client.chat_update.assert_awaited_once()
    mock_client.chat_postMessage.assert_awaited_once()
    confirm_text = mock_client.chat_postMessage.call_args[1]["text"]
    assert "Spec Revision Requested" in confirm_text
