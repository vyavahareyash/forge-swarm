"""
Slack Multi-Persona Bridge for Autonomous Multi-Agent Swarm.

Uses single Slack Bot OAuth token with `chat:write.customize` scope
to post messages appearing as distinct agent teammates within a shared thread.
"""
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

AGENT_PERSONAS: Dict[str, Dict[str, str]] = {
    "orchestrator": {
        "username": "Atlas (Lead Architect)",
        "icon_emoji": ":compass:",
        "role": "Lead Architect & Task Decomposition",
    },
    "coder": {
        "username": "Forge (SWE Sub-Agent)",
        "icon_emoji": ":hammer_and_wrench:",
        "role": "Software Engineering & Implementation",
    },
    "qa": {
        "username": "Sentinel (QA Automation)",
        "icon_emoji": ":shield:",
        "role": "QA Verification & Test Execution",
    },
    "warden": {
        "username": "Quota Warden",
        "icon_emoji": ":stopwatch:",
        "role": "Token Budget & Policy Gatekeeper",
    },
}


def get_persona(role: str) -> Dict[str, str]:
    """Retrieve persona config by role key (orchestrator, coder, qa, warden)."""
    normalized = role.lower().strip()
    if "lead" in normalized or "atlas" in normalized or "orch" in normalized:
        return AGENT_PERSONAS["orchestrator"]
    if "coder" in normalized or "forge" in normalized or "swe" in normalized:
        return AGENT_PERSONAS["coder"]
    if "qa" in normalized or "sentinel" in normalized or "test" in normalized:
        return AGENT_PERSONAS["qa"]
    if "warden" in normalized or "budget" in normalized or "quota" in normalized or "policy" in normalized:
        return AGENT_PERSONAS["warden"]
    return AGENT_PERSONAS.get(normalized, AGENT_PERSONAS["orchestrator"])


async def post_agent_message(
    client: Any,
    channel: str,
    role: str,
    text: str,
    thread_ts: Optional[str] = None,
    blocks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Post a message to Slack with custom persona (username and emoji avatar).

    Args:
        client: AsyncWebClient (Slack SDK) or mock client.
        channel: Target channel ID (e.g. C0123456789).
        role: Persona role ('orchestrator', 'coder', 'qa', 'warden').
        text: Fallback markdown text.
        thread_ts: Thread timestamp to post inside a thread.
        blocks: Optional Slack Block Kit blocks.

    Returns:
        Slack API response dictionary.
    """
    persona = get_persona(role)
    payload: Dict[str, Any] = {
        "channel": channel,
        "text": text,
        "username": persona["username"],
        "icon_emoji": persona["icon_emoji"],
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if blocks:
        payload["blocks"] = blocks

    if hasattr(client, "chat_postMessage"):
        # Could be async or sync client
        call = client.chat_postMessage(**payload)
        if hasattr(call, "__await__"):
            return await call
        return call
    raise ValueError("Provided Slack client does not implement chat_postMessage")
