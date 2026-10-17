import os
from typing import Any


def build_budget_exhaustion_blocks(
    task_id: str,
    current_tokens: int,
    budget_limit: int,
    turn_count: int,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Constructs Slack Block Kit UI for Quota Warden budget exhaustion alert.
    Allows engineers to grant +50k tokens or abort the task safely.
    """
    target_user = user_id or os.getenv("SLACK_USER_ID")

    percent_used = int((current_tokens / budget_limit) * 100) if budget_limit else 100
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🛑 Task Budget Ceiling Reached",
                "emoji": True,
            },
        },
    ]

    if target_user:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🔔 <@{target_user}> *Human review required:* Task `{task_id}` has hit its budget ceiling.",
                },
            }
        )

    blocks.extend(
        [
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Task ID:*\n`{task_id}`",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Turns Completed:*\n{turn_count} turns",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Token Spend:*\n`{current_tokens:,} / {budget_limit:,}` ({percent_used}%)",
                    },
                    {
                        "type": "mrkdwn",
                        "text": "*Status:*\n`PAUSED_BUDGET` ⏸️",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "Execution has paused deterministically via `@post_turn` guard. "
                        "Choose an action to proceed:"
                    ),
                },
            },
            {
                "type": "actions",
                "block_id": f"budget_gate_{task_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "⚡ Grant +50k Tokens",
                            "emoji": True,
                        },
                        "style": "primary",
                        "action_id": "grant_budget_50k",
                        "value": task_id,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "🛑 Abort Task",
                            "emoji": True,
                        },
                        "style": "danger",
                        "action_id": "abort_task",
                        "value": task_id,
                    },
                ],
            },
        ]
    )
    return blocks


def build_policy_gate_blocks(
    task_id: str,
    command: str,
    reason: str,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Constructs Block Kit UI for PolicyEngine ask_user() gates (e.g. git push).
    """
    target_user = user_id or os.getenv("SLACK_USER_ID")
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "⚠️ Security Policy Gate: Approval Required",
                "emoji": True,
            },
        },
    ]

    if target_user:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🔔 <@{target_user}> *Human review required:* Safety policy requires explicit approval.",
                },
            }
        )

    blocks.extend(
        [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"An agent requested execution of a restricted command for task `{task_id}`:\n"
                        f"```{command}```\n"
                        f"*Reason:* {reason}"
                    ),
                },
            },
            {
                "type": "actions",
                "block_id": f"policy_gate_{task_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "✅ Approve Command",
                            "emoji": True,
                        },
                        "style": "primary",
                        "action_id": "approve_command",
                        "value": task_id,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "❌ Deny Command",
                            "emoji": True,
                        },
                        "style": "danger",
                        "action_id": "deny_command",
                        "value": task_id,
                    },
                ],
            },
        ]
    )
    return blocks


def build_draft_pr_blocks(
    task_id: str,
    issue_id: str,
    branch_name: str,
    diff_stats: str,
    test_summary: str,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Constructs Block Kit UI for end-of-task review and Draft PR dispatch.
    """
    target_user = user_id or os.getenv("SLACK_USER_ID")
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🎉 Issue Resolution Ready for Review",
                "emoji": True,
            },
        },
    ]

    if target_user:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🔔 <@{target_user}> *Review requested:* Swarm has verified fix. Review diff & authorize Draft PR delivery.",
                },
            }
        )

    blocks.extend(
        [
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Issue ID:*\n`{issue_id}`",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Branch:*\n`{branch_name}`",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*QA Verification Status:* ✅ All tests passing\n"
                        f"```{test_summary}```\n"
                        f"*Git Diff Summary:*\n"
                        f"```{diff_stats}```"
                    ),
                },
            },
            {
                "type": "actions",
                "block_id": f"pr_gate_{task_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "🚀 Approve & Create Draft PR",
                            "emoji": True,
                        },
                        "style": "primary",
                        "action_id": "approve_draft_pr",
                        "value": task_id,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "💬 Request Revisions",
                            "emoji": True,
                        },
                        "action_id": "request_revision",
                        "value": task_id,
                    },
                ],
            },
        ]
    )
    return blocks


def build_spec_acceptance_blocks(
    task_id: str,
    issue_id: str,
    title: str,
    summary: str,
    acceptance_criteria: list[str],
    scope_and_boundaries: list[str] | None = None,
    out_of_scope: list[str] | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Constructs Block Kit UI for Mira's intake acceptance criteria card.
    Provides [Approve & Start Implementation] and [Request Revisions] buttons.
    """
    target_user = user_id or os.getenv("SLACK_USER_ID")
    clean_title = title.replace("[Draft]", "").strip()

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🎯 Requirements & Acceptance Criteria Sign-Off",
                "emoji": True,
            },
        },
    ]

    if target_user:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"🔔 <@{target_user}> *Sign-off required:* Mira has finalized requirements for "
                        f"*{clean_title}*. Review and approve to begin development."
                    ),
                },
            }
        )

    blocks.extend(
        [
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Task ID:*\n`{task_id}`",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Issue:*\n`{issue_id}`",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Feature:*\n*{clean_title}*",
                    },
                    {
                        "type": "mrkdwn",
                        "text": "*Intake Phase:*\n`GRILLING_COMPLETE` 📋",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Summary:*\n{summary}",
                },
            },
        ]
    )

    # Acceptance Criteria
    criteria_text = "\n".join(f"• [ ] {c}" for c in acceptance_criteria)
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Acceptance Criteria:*\n{criteria_text}",
            },
        }
    )

    # Boundaries
    if scope_and_boundaries:
        scope_text = "\n".join(f"• {s}" for s in scope_and_boundaries)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Scope & Boundaries:*\n{scope_text}",
                },
            }
        )

    # Out of Scope
    if out_of_scope:
        oos_text = "\n".join(f"• {o}" for o in out_of_scope)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Out of Scope:*\n{oos_text}",
                },
            }
        )

    # Action buttons
    blocks.append(
        {
            "type": "actions",
            "block_id": f"spec_gate_{task_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "🚀 Approve & Start Implementation",
                        "emoji": True,
                    },
                    "style": "primary",
                    "action_id": "approve_spec",
                    "value": task_id,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "💬 Request Revisions",
                        "emoji": True,
                    },
                    "style": "danger",
                    "action_id": "request_spec_revision",
                    "value": task_id,
                },
            ],
        }
    )

    return blocks
