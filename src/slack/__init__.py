"""
Slack Socket Mode and Multi-Persona War-Room integration for npc-inc.
"""
from src.slack.approvals import (
    build_budget_exhaustion_blocks,
    build_draft_pr_blocks,
    build_policy_gate_blocks,
)
from src.slack.bot import SlackWarRoomBot
from src.slack.personas import AGENT_PERSONAS, get_persona, post_agent_message

__all__ = [
    "AGENT_PERSONAS",
    "get_persona",
    "post_agent_message",
    "build_budget_exhaustion_blocks",
    "build_policy_gate_blocks",
    "build_draft_pr_blocks",
    "SlackWarRoomBot",
]
