"""
Unit and integration tests for Slack In-Place Block Kit Mutation & Virtual Persona Router (Issue #3).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.slack.approvals import (
    build_budget_exhaustion_blocks,
    build_draft_pr_blocks,
    build_policy_gate_blocks,
)
from src.slack.bot import SlackWarRoomBot
from src.slack.mutations import mutate_blocks_for_resolution
from src.slack.personas import get_avatar_url, get_persona, post_agent_message
from src.slack.router import (
    ChannelMode,
    ChannelTopologyRouter,
    extract_persona_mention,
    resolve_target_persona,
)


def test_canonical_personas():
    """Verify all 6 canonical production personas and legacy aliases."""
    mira = get_persona("mira")
    assert mira["username"] == "Mira (Product Specifier)"
    assert mira["icon_emoji"] == ":dart:"

    arjun = get_persona("arjun")
    assert arjun["username"] == "Arjun (Lead Architect)"
    assert arjun["icon_emoji"] == ":compass:"

    kabir = get_persona("kabir")
    assert kabir["username"] == "Kabir (Senior SWE)"
    assert kabir["icon_emoji"] == ":hammer_and_wrench:"

    chetna = get_persona("chetna")
    assert chetna["username"] == "Chetna (QA Automation)"
    assert chetna["icon_emoji"] == ":microscope:"

    vivek = get_persona("vivek")
    assert vivek["username"] == "Vivek (Staff Reviewer)"
    assert vivek["icon_emoji"] == ":monocle_face:"

    dhruv = get_persona("dhruv")
    assert dhruv["username"] == "Dhruv (Quota Guard)"
    assert dhruv["icon_emoji"] == ":scales:"

    # Legacy aliases
    assert get_persona("atlas")["icon_emoji"] == ":compass:"
    assert get_persona("forge")["icon_emoji"] == ":hammer_and_wrench:"
    assert get_persona("sentinel")["icon_emoji"] == ":shield:"
    assert get_persona("warden")["icon_emoji"] == ":stopwatch:"


def test_mutate_blocks_for_resolution():
    """Verify interactive actions blocks mutate into immutable audit context blocks."""
    orig_blocks = build_budget_exhaustion_blocks(
        task_id="task-xyz",
        current_tokens=150_000,
        budget_limit=150_000,
        turn_count=15,
        user_id="U999",
    )
    # Check original has actions block
    assert any(b.get("type") == "actions" for b in orig_blocks)

    # Mutate
    mutated = mutate_blocks_for_resolution(
        blocks=orig_blocks,
        action_id="grant_budget_50k",
        user_id="U999",
        timestamp_utc="14:30:00 UTC",
    )

    # Result has NO actions block (zero zombie buttons)
    assert not any(b.get("type") == "actions" for b in mutated)

    # Has audit block
    audit = next(b for b in mutated if b.get("block_id") == "resolved_grant_budget_50k")
    assert audit["type"] == "context"
    text = audit["elements"][0]["text"]
    assert "Granted +50k Tokens" in text
    assert "<@U999>" in text
    assert "14:30:00 UTC" in text

    # Header and fields preserved
    assert mutated[0]["type"] == "header"


def test_mutate_blocks_policy_and_pr_gates():
    """Verify mutation on policy gates and draft PR gates."""
    policy_blocks = build_policy_gate_blocks(
        task_id="task-1",
        command="git push origin main",
        reason="Protected branch",
    )
    mutated_policy = mutate_blocks_for_resolution(
        blocks=policy_blocks,
        action_id="deny_command",
        user_id="U123",
        timestamp_utc="15:00:00 UTC",
    )
    assert not any(b.get("type") == "actions" for b in mutated_policy)
    audit = next(
        b for b in mutated_policy if b.get("block_id") == "resolved_deny_command"
    )
    assert "Command Denied" in audit["elements"][0]["text"]

    pr_blocks = build_draft_pr_blocks(
        task_id="task-2",
        issue_id="issue-2",
        branch_name="feat/test",
        diff_stats="+10 -2",
        test_summary="5 passed",
    )
    mutated_pr = mutate_blocks_for_resolution(
        blocks=pr_blocks,
        action_id="approve_draft_pr",
        user_id="U456",
        timestamp_utc="15:05:00 UTC",
    )
    assert not any(b.get("type") == "actions" for b in mutated_pr)
    audit_pr = next(
        b for b in mutated_pr if b.get("block_id") == "resolved_approve_draft_pr"
    )
    assert "Draft PR Approved & Dispatched" in audit_pr["elements"][0]["text"]


def test_extract_persona_mention():
    """Verify regex persona extraction with multiple cases, punctuations, and aliases."""
    assert extract_persona_mention("@Mira what are the edge cases?") == "mira"
    assert extract_persona_mention("Hey @Arjun: please review the blueprint") == "arjun"
    assert extract_persona_mention("Can @kabir start the implementation?") == "kabir"
    assert extract_persona_mention("@Chetna run regression suite now") == "chetna"
    assert extract_persona_mention("@VIVEK are we ready to commit?") == "vivek"
    assert extract_persona_mention("Check budget with @dhruv please") == "dhruv"

    # Legacy aliases
    assert extract_persona_mention("Ask @atlas for advice") == "arjun"
    assert extract_persona_mention("@forge build this") == "kabir"
    assert extract_persona_mention("@sentinel verify") == "chetna"

    # No mentions
    assert extract_persona_mention("No mention in this message") is None
    assert extract_persona_mention("") is None
    assert extract_persona_mention("someone@example.com") is None


def test_resolve_target_persona_fallback():
    """Verify mention override and lifecycle phase fallbacks."""
    # Explicit mention overrides active phase
    assert (
        resolve_target_persona("@Vivek review this diff", active_phase="intake")
        == "vivek"
    )
    assert resolve_target_persona("Hey @Mira clarify AC", active_phase="dev") == "mira"

    # Fallbacks based on active phase
    assert (
        resolve_target_persona("Normal steering message", active_phase="intake")
        == "mira"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="grilling")
        == "mira"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="triage")
        == "mira"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="planning")
        == "arjun"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="architecture")
        == "arjun"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="dev") == "kabir"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="running")
        == "kabir"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="tdd") == "kabir"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="qa") == "chetna"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="regression")
        == "chetna"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="review")
        == "vivek"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="pre_commit")
        == "vivek"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="paused_budget")
        == "dhruv"
    )

    # Default fallback
    assert (
        resolve_target_persona("Normal steering message", active_phase=None) == "arjun"
    )
    assert (
        resolve_target_persona("Normal steering message", active_phase="unknown")
        == "arjun"
    )


def test_channel_topology_router(monkeypatch):
    """Verify Compact vs Segregated channel topology modes."""
    # Compact mode
    compact_router = ChannelTopologyRouter(
        mode=ChannelMode.COMPACT,
        default_channel="C_OPS",
        triage_channel="C_TRIAGE",
        war_room_channel="C_WARROOM",
        releases_channel="C_RELEASES",
    )
    assert compact_router.get_channel("triage") == "C_OPS"
    assert compact_router.get_channel("planning") == "C_OPS"
    assert compact_router.get_channel("release") == "C_OPS"

    # Segregated mode
    segregated_router = ChannelTopologyRouter(
        mode=ChannelMode.SEGREGATED,
        default_channel="C_OPS",
        triage_channel="C_TRIAGE",
        war_room_channel="C_WARROOM",
        releases_channel="C_RELEASES",
    )
    assert segregated_router.get_channel("intake") == "C_TRIAGE"
    assert segregated_router.get_channel("triage") == "C_TRIAGE"
    assert segregated_router.get_channel("grilling") == "C_TRIAGE"
    assert segregated_router.get_channel("planning") == "C_WARROOM"
    assert segregated_router.get_channel("dev") == "C_WARROOM"
    assert segregated_router.get_channel("qa") == "C_WARROOM"
    assert segregated_router.get_channel("review") == "C_WARROOM"
    assert segregated_router.get_channel("release") == "C_RELEASES"
    assert segregated_router.get_channel("delivery") == "C_RELEASES"
    assert segregated_router.get_channel("pr") == "C_RELEASES"
    assert segregated_router.get_channel("unknown_phase") == "C_OPS"

    # from_env
    monkeypatch.setenv("SLACK_CHANNEL_MODE", "segregated")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C_ENV_OPS")
    monkeypatch.setenv("SLACK_CHANNEL_TRIAGE", "C_ENV_TRIAGE")
    monkeypatch.setenv("SLACK_CHANNEL_WAR_ROOM", "C_ENV_WARROOM")
    monkeypatch.setenv("SLACK_CHANNEL_RELEASES", "C_ENV_RELEASES")

    env_router = ChannelTopologyRouter.from_env()
    assert env_router.mode == ChannelMode.SEGREGATED
    assert env_router.get_channel("triage") == "C_ENV_TRIAGE"
    assert env_router.get_channel("dev") == "C_ENV_WARROOM"
    assert env_router.get_channel("pr") == "C_ENV_RELEASES"


@pytest.mark.asyncio
async def test_bot_mutate_message_in_place():
    """Verify SlackWarRoomBot.mutate_message_in_place calls client.chat_update."""
    bot = SlackWarRoomBot(bot_token=None, app_token=None, default_channel="C_TEST")
    mock_client = MagicMock()
    mock_client.chat_update = AsyncMock(return_value={"ok": True})

    orig_blocks = build_budget_exhaustion_blocks(
        task_id="task-100",
        current_tokens=150_000,
        budget_limit=150_000,
        turn_count=15,
    )
    body = {
        "message": {
            "ts": "1712345.0001",
            "blocks": orig_blocks,
            "text": "Budget ceiling reached",
        }
    }

    await bot.mutate_message_in_place(
        client=mock_client,
        channel="C_TEST",
        body=body,
        action_id="grant_budget_50k",
        user_id="U_ENGINEER",
    )

    mock_client.chat_update.assert_called_once()
    call_kwargs = mock_client.chat_update.call_args.kwargs
    assert call_kwargs["channel"] == "C_TEST"
    assert call_kwargs["ts"] == "1712345.0001"
    updated_blocks = call_kwargs["blocks"]
    assert not any(b.get("type") == "actions" for b in updated_blocks)
    assert any(b.get("block_id") == "resolved_grant_budget_50k" for b in updated_blocks)


def test_bot_get_channel_delegation():
    """Verify SlackWarRoomBot delegates get_channel to ChannelTopologyRouter."""
    bot = SlackWarRoomBot(
        bot_token=None, app_token=None, default_channel="C_BOT_DEFAULT"
    )
    assert bot.get_channel("triage") == "C_BOT_DEFAULT"
    assert bot.get_channel("dev") == "C_BOT_DEFAULT"


def test_get_avatar_url(monkeypatch):
    """Verify avatar URL resolution with custom and base URL env variables."""
    # When no base URL set
    monkeypatch.delenv("SLACK_AVATAR_BASE_URL", raising=False)
    monkeypatch.delenv("SLACK_AVATAR_MIRA_URL", raising=False)
    assert get_avatar_url("mira") is None

    # Base URL set
    monkeypatch.setenv("SLACK_AVATAR_BASE_URL", "https://cdn.example.com/avatars")
    assert get_avatar_url("mira") == "https://cdn.example.com/avatars/mira.png"
    assert get_avatar_url("Arjun") == "https://cdn.example.com/avatars/arjun.png"

    # Specific persona override
    monkeypatch.setenv(
        "SLACK_AVATAR_MIRA_URL", "https://cdn.example.com/custom/mira-animated.gif"
    )
    assert get_avatar_url("mira") == "https://cdn.example.com/custom/mira-animated.gif"


@pytest.mark.asyncio
async def test_post_agent_message_with_icon_url(monkeypatch):
    """Verify post_agent_message prefers icon_url over icon_emoji when provided or configured."""
    mock_client = MagicMock()
    mock_client.chat_postMessage = AsyncMock(return_value={"ok": True})

    # Explicit icon_url arg
    await post_agent_message(
        client=mock_client,
        channel="C_TEST",
        role="mira",
        text="Reviewing spec",
        icon_url="https://cdn.example.com/mira.gif",
    )
    call_kwargs = mock_client.chat_postMessage.call_args.kwargs
    assert call_kwargs["icon_url"] == "https://cdn.example.com/mira.gif"
    assert "icon_emoji" not in call_kwargs

    # Via env base URL
    monkeypatch.setenv("SLACK_AVATAR_BASE_URL", "https://cdn.example.com/avatars")
    mock_client.reset_mock()
    await post_agent_message(
        client=mock_client,
        channel="C_TEST",
        role="kabir",
        text="TDD green",
    )
    call_kwargs = mock_client.chat_postMessage.call_args.kwargs
    assert call_kwargs["icon_url"] == "https://cdn.example.com/avatars/kabir.png"
    assert "icon_emoji" not in call_kwargs
