"""
Slack Persona Mention & Channel Topology Router.

Handles:
1. Regex extraction of virtual persona mentions (@Mira, @Arjun, etc.)
2. Fallback to phase-leading persona when no mention is provided
3. Dual-mode channel routing (compact single-channel vs segregated multi-channel)
"""

import os
import re
from enum import StrEnum

# Regex pattern matching any recognized persona mention
PERSONA_MENTION_PATTERN = re.compile(
    r"@\b(mira|arjun|kabir|chetna|vivek|dhruv|atlas|forge|sentinel|warden)\b",
    re.IGNORECASE,
)

# Persona alias normalization mapping
PERSONA_NORMALIZATION: dict[str, str] = {
    "mira": "mira",
    "arjun": "arjun",
    "kabir": "kabir",
    "chetna": "chetna",
    "vivek": "vivek",
    "dhruv": "dhruv",
    "atlas": "arjun",
    "forge": "kabir",
    "sentinel": "chetna",
    "warden": "dhruv",
}

# Lifecycle phase to leading persona mapping
PHASE_LEAD_PERSONA: dict[str, str] = {
    "intake": "mira",
    "triage": "mira",
    "grilling": "mira",
    "spec": "mira",
    "needs-spec": "mira",
    "planning": "arjun",
    "architecture": "arjun",
    "design": "arjun",
    "ready-for-dev": "arjun",
    "dev": "kabir",
    "coding": "kabir",
    "implementation": "kabir",
    "tdd": "kabir",
    "running": "kabir",
    "qa": "chetna",
    "testing": "chetna",
    "regression": "chetna",
    "verification": "chetna",
    "review": "vivek",
    "pre_commit": "vivek",
    "pre-commit": "vivek",
    "code-review": "vivek",
    "budget": "dhruv",
    "quota": "dhruv",
    "paused_budget": "dhruv",
    "policy": "dhruv",
}


def extract_persona_mention(text: str) -> str | None:
    """
    Extracts virtual persona mention from user text via regex.

    Args:
        text: Raw user message text.

    Returns:
        Canonical persona key ('mira', 'arjun', 'kabir', etc.) if found, else None.
    """
    if not text:
        return None

    match = PERSONA_MENTION_PATTERN.search(text)
    if match:
        name = match.group(1).lower()
        return PERSONA_NORMALIZATION.get(name, name)
    return None


def resolve_target_persona(text: str, active_phase: str | None = None) -> str:
    """
    Resolves which agent persona should respond to the message.

    Rules:
    1. If message explicitly mentions @Persona, route to that persona.
    2. Otherwise, route to the leading persona for the active lifecycle phase.
    3. If active phase is undefined or unknown, default to Arjun (Lead Architect).
    """
    mentioned = extract_persona_mention(text)
    if mentioned:
        return mentioned

    if active_phase:
        normalized_phase = active_phase.lower().strip()
        if normalized_phase in PHASE_LEAD_PERSONA:
            return PHASE_LEAD_PERSONA[normalized_phase]

        # Substring matching for compound status names (e.g. status:ready-for-dev)
        for phase_key, persona in PHASE_LEAD_PERSONA.items():
            if phase_key in normalized_phase:
                return persona

    return "arjun"


class ChannelMode(StrEnum):
    COMPACT = "compact"
    SEGREGATED = "segregated"


class ChannelTopologyRouter:
    """
    Routes Slack notifications and discussions to channels based on configured mode.

    Modes:
    - COMPACT (Default / Low Volume): All phases route to a single ops channel.
    - SEGREGATED (High Volume / Production):
        - #dev-triage: Intake, grilling, acceptance criteria.
        - #dev-war-room: Architecture, TDD implementation, QA tests, review.
        - #dev-releases: PR delivery, verification proof, merge gates.
    """

    def __init__(
        self,
        mode: ChannelMode | str = ChannelMode.COMPACT,
        default_channel: str | None = None,
        triage_channel: str | None = None,
        war_room_channel: str | None = None,
        releases_channel: str | None = None,
    ):
        self.mode = ChannelMode(str(mode).lower())
        self.default_channel = default_channel or "agent-ops"
        self.triage_channel = triage_channel or self.default_channel
        self.war_room_channel = war_room_channel or self.default_channel
        self.releases_channel = releases_channel or self.default_channel

    @classmethod
    def from_env(cls) -> "ChannelTopologyRouter":
        """Constructs router from environment variables."""
        mode_str = os.getenv("SLACK_CHANNEL_MODE", "compact")
        default = os.getenv("SLACK_CHANNEL_ID") or os.getenv(
            "SLACK_CHANNEL_AGENT_OPS", "agent-ops"
        )
        triage = os.getenv("SLACK_CHANNEL_TRIAGE", default)
        war_room = os.getenv("SLACK_CHANNEL_WAR_ROOM", default)
        releases = os.getenv("SLACK_CHANNEL_RELEASES", default)

        return cls(
            mode=mode_str,
            default_channel=default,
            triage_channel=triage,
            war_room_channel=war_room,
            releases_channel=releases,
        )

    def get_channel(self, phase: str | None = None) -> str:
        """
        Returns the appropriate channel ID / name for the given lifecycle phase.
        """
        if self.mode == ChannelMode.COMPACT or not phase:
            return self.default_channel

        normalized = phase.lower().strip()
        if any(
            k in normalized for k in ("intake", "triage", "grill", "spec", "needs-spec")
        ):
            return self.triage_channel

        if any(
            k in normalized
            for k in (
                "release",
                "delivery",
                "pr",
                "merge",
                "gate3",
            )
        ):
            return self.releases_channel

        if any(
            k in normalized
            for k in (
                "plan",
                "arch",
                "dev",
                "code",
                "tdd",
                "qa",
                "test",
                "review",
                "war_room",
                "war-room",
                "running",
            )
        ):
            return self.war_room_channel

        return self.default_channel
