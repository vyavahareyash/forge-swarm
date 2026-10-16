"""
Slack Multi-Persona Bridge for Autonomous Multi-Agent Swarm.

Uses single Slack Bot OAuth token with `chat:write.customize` scope
to post messages appearing as distinct agent teammates within a shared thread.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Base CDN / GitHub URL for avatar images if configured
DEFAULT_AVATAR_BASE_URL = os.getenv(
    "SLACK_AVATAR_BASE_URL",
    "https://raw.githubusercontent.com/vyavahareyash/forge-swarm/main/docs/assets/avatars",
)


def get_avatar_url(persona_key: str) -> str | None:
    """Returns avatar image URL if configured or constructed from base URL."""
    env_var = f"SLACK_AVATAR_{persona_key.upper()}_URL"
    custom_url = os.getenv(env_var)
    if custom_url:
        return custom_url

    # Check if base URL is explicitly configured
    base = os.getenv("SLACK_AVATAR_BASE_URL")
    if base:
        normalized = persona_key.lower().strip()
        return f"{base.rstrip('/')}/{normalized}.png"
    return None


AGENT_PERSONAS: dict[str, dict[str, Any]] = {
    # Canonical Production Team Personas
    "mira": {
        "username": "Mira (Product Specifier)",
        "icon_emoji": ":dart:",
        "icon_url": None,
        "role": "Product Specifier & Requirements Grilling",
    },
    "arjun": {
        "username": "Arjun (Lead Architect)",
        "icon_emoji": ":compass:",
        "icon_url": None,
        "role": "Lead Architect & System Design",
    },
    "kabir": {
        "username": "Kabir (Senior SWE)",
        "icon_emoji": ":hammer_and_wrench:",
        "icon_url": None,
        "role": "Senior SWE & TDD Implementation",
    },
    "chetna": {
        "username": "Chetna (QA Automation)",
        "icon_emoji": ":microscope:",
        "icon_url": None,
        "role": "QA Automation & Regression Testing",
    },
    "vivek": {
        "username": "Vivek (Staff Reviewer)",
        "icon_emoji": ":monocle_face:",
        "icon_url": None,
        "role": "Staff Reviewer & Dual-Axis Verification",
    },
    "dhruv": {
        "username": "Dhruv (Quota Guard)",
        "icon_emoji": ":scales:",
        "icon_url": None,
        "role": "Quota Governance & Safety Gatekeeper",
    },
    # Legacy Swarm Personas (Backward Compatibility)
    "orchestrator": {
        "username": "Atlas (Lead Architect)",
        "icon_emoji": ":compass:",
        "icon_url": None,
        "role": "Lead Architect & Task Decomposition",
    },
    "coder": {
        "username": "Forge (SWE Sub-Agent)",
        "icon_emoji": ":hammer_and_wrench:",
        "icon_url": None,
        "role": "Software Engineering & Implementation",
    },
    "qa": {
        "username": "Sentinel (QA Automation)",
        "icon_emoji": ":shield:",
        "icon_url": None,
        "role": "QA Verification & Test Execution",
    },
    "warden": {
        "username": "Quota Warden",
        "icon_emoji": ":stopwatch:",
        "icon_url": None,
        "role": "Token Budget & Policy Gatekeeper",
    },
}


def get_persona(role: str) -> dict[str, Any]:
    """Retrieve persona config by canonical key or legacy alias."""
    normalized = role.lower().strip()
    if normalized in AGENT_PERSONAS:
        return AGENT_PERSONAS[normalized]

    # Canonical matching
    if "mira" in normalized or "spec" in normalized or "grill" in normalized:
        return AGENT_PERSONAS["mira"]
    if "arjun" in normalized or "architect" in normalized:
        return AGENT_PERSONAS["arjun"]
    if "kabir" in normalized:
        return AGENT_PERSONAS["kabir"]
    if "chetna" in normalized or "regression" in normalized:
        return AGENT_PERSONAS["chetna"]
    if "vivek" in normalized or "review" in normalized:
        return AGENT_PERSONAS["vivek"]
    if "dhruv" in normalized or "guard" in normalized:
        return AGENT_PERSONAS["dhruv"]

    # Legacy matching
    if "atlas" in normalized or "orch" in normalized or "lead" in normalized:
        return AGENT_PERSONAS["orchestrator"]
    if "coder" in normalized or "forge" in normalized or "swe" in normalized:
        return AGENT_PERSONAS["coder"]
    if "qa" in normalized or "sentinel" in normalized or "test" in normalized:
        return AGENT_PERSONAS["qa"]
    if (
        "warden" in normalized
        or "budget" in normalized
        or "quota" in normalized
        or "policy" in normalized
    ):
        return AGENT_PERSONAS["warden"]

    return AGENT_PERSONAS["orchestrator"]


async def post_agent_message(
    client: Any,
    channel: str,
    role: str,
    text: str,
    thread_ts: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
    icon_url: str | None = None,
) -> dict[str, Any]:
    """
    Post a message to Slack with custom persona (username and avatar icon).
    Prefers icon_url (animated GIF / PNG image) over icon_emoji when available.

    Args:
        client: AsyncWebClient (Slack SDK) or mock client.
        channel: Target channel ID (e.g. C0123456789).
        role: Persona role ('mira', 'arjun', 'kabir', etc.).
        text: Fallback markdown text.
        thread_ts: Thread timestamp to post inside a thread.
        blocks: Optional Slack Block Kit blocks.
        icon_url: Optional explicit image URL overriding persona default.

    Returns:
        Slack API response dictionary.
    """
    persona = get_persona(role)
    payload: dict[str, Any] = {
        "channel": channel,
        "text": text,
        "username": persona["username"],
    }

    # Prefer icon_url over icon_emoji for animated / custom avatar support
    target_icon_url = icon_url or persona.get("icon_url") or get_avatar_url(role)
    if target_icon_url:
        payload["icon_url"] = target_icon_url
    elif persona.get("icon_emoji"):
        payload["icon_emoji"] = persona["icon_emoji"]

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
