"""
In-Place Slack Block Kit Mutation Engine.

Replaces interactive button blocks with immutable, timestamped audit blocks
upon user invocation to eliminate zombie buttons and prevent duplicate actions.
"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

ACTION_METADATA: dict[str, dict[str, str]] = {
    "grant_budget_50k": {
        "label": "Granted +50k Tokens (+5 Turns)",
        "icon": "⚡",
    },
    "abort_task": {
        "label": "Task Aborted",
        "icon": "🛑",
    },
    "approve_command": {
        "label": "Command Approved",
        "icon": "✅",
    },
    "deny_command": {
        "label": "Command Denied",
        "icon": "❌",
    },
    "approve_draft_pr": {
        "label": "Draft PR Approved & Dispatched",
        "icon": "🚀",
    },
    "request_revision": {
        "label": "Revision Requested",
        "icon": "💬",
    },
    "approve_spec": {
        "label": "Spec & Acceptance Criteria Approved",
        "icon": "✅",
    },
    "request_spec_revision": {
        "label": "Spec Revision Requested",
        "icon": "💬",
    },
    "approve_plan": {
        "label": "Architectural Plan Approved",
        "icon": "✅",
    },
    "approve_merge_pr": {
        "label": "PR Merge Approved",
        "icon": "🚢",
    },
}


def build_audit_block(
    action_id: str,
    user_id: str,
    resolution_text: str | None = None,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    """Builds an immutable Slack context block documenting user action."""
    meta = ACTION_METADATA.get(action_id, {"label": action_id, "icon": "✅"})
    label = resolution_text or meta["label"]
    icon = meta["icon"]

    ts_str = timestamp_utc or datetime.now(UTC).strftime("%H:%M:%S UTC")
    user_ref = f"<@{user_id}>" if not user_id.startswith("<@") else user_id

    return {
        "type": "context",
        "block_id": f"resolved_{action_id}",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"{icon} *{label}* by {user_ref} at `{ts_str}`",
            }
        ],
    }


def mutate_blocks_for_resolution(
    blocks: list[dict[str, Any]],
    action_id: str,
    user_id: str,
    resolution_text: str | None = None,
    timestamp_utc: str | None = None,
    block_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Mutates message blocks in place by replacing the interactive actions block
    with an immutable timestamped audit block.

    Args:
        blocks: Original Block Kit block list from Slack payload.
        action_id: The action ID triggered by user interaction.
        user_id: Slack user ID or name who triggered the action.
        resolution_text: Optional custom label describing resolution.
        timestamp_utc: Optional custom UTC timestamp string.
        block_id: Optional specific block_id to target for mutation.

    Returns:
        New block list with actions block replaced by audit block.
    """
    updated_blocks = deepcopy(blocks)
    audit_block = build_audit_block(
        action_id=action_id,
        user_id=user_id,
        resolution_text=resolution_text,
        timestamp_utc=timestamp_utc,
    )

    replaced = False
    for i, blk in enumerate(updated_blocks):
        if blk.get("type") == "actions":
            # Match by explicit block_id if provided
            if block_id and blk.get("block_id") == block_id:
                updated_blocks[i] = audit_block
                replaced = True
                break

            # Match by checking if action_id is inside elements
            element_action_ids = [
                elem.get("action_id") for elem in blk.get("elements", [])
            ]
            if action_id in element_action_ids:
                updated_blocks[i] = audit_block
                replaced = True
                break

    # If not specifically matched by action_id, replace first action block found
    if not replaced:
        for i, blk in enumerate(updated_blocks):
            if blk.get("type") == "actions":
                updated_blocks[i] = audit_block
                replaced = True
                break

    # If no actions block existed, append audit block
    if not replaced:
        updated_blocks.append(audit_block)

    return updated_blocks
