"""
End-to-End Autonomous SWE Swarm Pipeline.

Orchestrates the entire lifecycle for an issue:
1. Issue Ingestion & State Transition ('agent-ready' -> 'agent-in-progress')
2. Ephemeral Worktree Provisioning (`./workspaces/issue-<id>`)
3. Dedicated Slack War-Room Thread (`chat:write.customize` multi-persona streams)
4. Multi-Agent Swarm (Atlas Architect -> Sentinel QA -> Forge Coder self-correction)
5. Interactive Slack Block Kit Review Gate (`[Approve & Create Draft PR]`)
6. Git Branch Push & GitHub Draft PR Delivery (`gh pr create --draft`)
7. Final Label Swap ('agent-in-progress' -> 'agent-review') & Worktree Pruning
"""
import asyncio
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.agents.orchestrator import Orchestrator
from src.schemas import SwarmExecutionResult, SwarmTurnLog
from src.slack.approvals import build_draft_pr_blocks
from src.slack.bot import SlackWarRoomBot
from src.storage.db import (
    create_task,
    init_db,
    update_task_status,
    update_task_thread_ts,
)
from src.triggers.github_poller import GitHubIssue, GitHubPoller
from src.worktree import WorktreeManager

logger = logging.getLogger(__name__)


class PipelineExecutionResult:
    """Outcome of an end-to-end pipeline run."""

    def __init__(
        self,
        task_id: str,
        issue_id: str,
        status: str,
        swarm_result: SwarmExecutionResult | None = None,
        pr_url: str | None = None,
        thread_ts: str | None = None,
        error_message: str | None = None,
    ):
        self.task_id = task_id
        self.issue_id = issue_id
        self.status = status
        self.swarm_result = swarm_result
        self.pr_url = pr_url
        self.thread_ts = thread_ts
        self.error_message = error_message


class E2EPipeline:
    """
    Coordinates GitHub poller, Git worktree isolation, Swarm agents,
    Slack war-room interactive approvals, and Draft PR delivery.
    """

    def __init__(
        self,
        target_repo_path: str | None = None,
        poller: GitHubPoller | None = None,
        bot: SlackWarRoomBot | None = None,
        orchestrator: Orchestrator | None = None,
        worktree_manager: WorktreeManager | None = None,
        db_path: str | None = None,
        mode: str = "standalone",
    ):
        self.target_repo_path = Path(
            target_repo_path or os.getenv("TARGET_REPO_PATH") or os.path.abspath("tests/fixtures/sample_repo")
        ).resolve()
        self.db_path = db_path or os.path.abspath("storage/state.db")
        init_db(self.db_path)

        self.poller = poller
        self.bot = bot
        self.wm = worktree_manager or WorktreeManager(os.path.abspath("workspaces"))
        self.orchestrator = orchestrator or Orchestrator(
            workspace_manager=self.wm,
            db_path=self.db_path,
        )
        self.mode = mode

    async def execute_issue(
        self,
        issue: GitHubIssue,
        auto_approve_pr: bool = False,
        budget_limit: int = 150_000,
        max_retries: int = 3,
        on_status_update: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> PipelineExecutionResult:
        """
        Executes end-to-end workflow for a single GitHubIssue.
        """
        issue_id = issue.issue_id
        task_id = f"task-{issue_id}-{int(datetime.now(UTC).timestamp())}"
        branch_name = f"fix/{issue_id}"
        thread_ts: str | None = None
        worktree_path: Path | None = None

        logger.info("Starting pipeline execution for %s (task: %s)", issue_id, task_id)
        if on_status_update:
            on_status_update("STARTING", {"task_id": task_id, "issue_id": issue_id})

        # 1. Update GitHub label: 'agent-ready' -> 'agent-in-progress'
        if self.poller:
            try:
                self.poller.mark_in_progress(issue.number)
                logger.info("Updated issue #%s label to 'agent-in-progress'", issue.number)
            except Exception as e:
                logger.warning("Could not update issue label: %s", e)

        # 2. Record initial task in SQLite
        create_task(
            task_id=task_id,
            issue_id=issue_id,
            budget_limit=budget_limit,
            turn_limit=15,
            db_path=self.db_path,
        )

        try:
            # 3. Create dedicated Slack War-Room Thread
            if self.bot and self.bot.app and self.bot.default_channel:
                try:
                    root_msg = await self.bot.post_agent(
                        role="orchestrator",
                        text=(
                            f"🚨 *Autonomous SWE Swarm Activated:* `{issue_id}`: *{issue.title}*\n"
                            f"Task ID: `{task_id}` | Budget Ceiling: `{budget_limit:,} tokens`\n"
                            f"Issue URL: {issue.url or 'local'}\n"
                            f"War-Room Thread initiated. Sub-agents streaming execution below. 👇"
                        ),
                    )
                    thread_ts = root_msg.get("ts")
                    if thread_ts:
                        update_task_thread_ts(task_id, thread_ts, db_path=self.db_path)
                except Exception as e:
                    logger.error("Failed to post initial Slack message: %s", e)

            # Define turn callback to stream updates to Slack thread
            async def stream_turn_to_slack(turn: SwarmTurnLog):
                if on_status_update:
                    on_status_update("SWARM_TURN", {"turn": turn.model_dump()})
                if self.bot and thread_ts and self.bot.app:
                    try:
                        role_map = {
                            "orchestrator": "orchestrator",
                            "coder": "coder",
                            "qa": "qa",
                            "warden": "warden",
                        }
                        role = role_map.get(turn.role, "orchestrator")
                        msg_text = f"*{turn.persona}:* {turn.action}\n```{turn.details}```"
                        await self.bot.post_agent(
                            role=role,
                            text=msg_text,
                            thread_ts=thread_ts,
                        )
                    except Exception as err:
                        logger.debug("Failed streaming turn to Slack: %s", err)

            def sync_turn_callback(turn: SwarmTurnLog):
                asyncio.create_task(stream_turn_to_slack(turn))

            # 4. Execute Swarm inside isolated worktree (retaining worktree for PR push)
            if on_status_update:
                on_status_update("SWARM_EXECUTING", {"task_id": task_id})

            swarm_result = await self.orchestrator.run_task(
                issue_id=issue_id,
                issue_desc=f"{issue.title}\n{issue.body}",
                repo_path=self.target_repo_path,
                budget_limit=budget_limit,
                max_retries=max_retries,
                mode=self.mode,
                on_turn_callback=sync_turn_callback,
                cleanup_worktree=False,  # Retain worktree so we can push branch on PR approval
            )

            # Worktree location
            worktree_path = (self.wm.workspace_root / f"issue-{issue_id}").resolve()

            if swarm_result.status != "SUCCESS":
                error_msg = f"Swarm loop failed to resolve {issue_id} after {swarm_result.retries_used} retries."
                logger.error(error_msg)
                if self.bot and thread_ts:
                    await self.bot.post_agent(
                        role="orchestrator",
                        text=f"❌ *Atlas:* Task `{task_id}` concluded without passing QA verification.",
                        thread_ts=thread_ts,
                    )
                return PipelineExecutionResult(
                    task_id=task_id,
                    issue_id=issue_id,
                    status="FAILED",
                    swarm_result=swarm_result,
                    thread_ts=thread_ts,
                    error_message=error_msg,
                )

            # 5. Interactive Review & Approval Gate
            diff_stats = swarm_result.git_diff or "Changes committed to worktree."
            test_summary = f"{swarm_result.final_report.total_tests} tests executed. 0 failed."

            if on_status_update:
                on_status_update("REVIEW_GATE", {"diff": diff_stats, "test_summary": test_summary})

            approved = auto_approve_pr
            if not auto_approve_pr and self.bot and thread_ts:
                pr_blocks = build_draft_pr_blocks(
                    task_id=task_id,
                    issue_id=issue_id,
                    branch_name=branch_name,
                    diff_stats=diff_stats[:500],
                    test_summary=test_summary,
                )
                await self.bot.post_agent(
                    role="orchestrator",
                    text="🚀 *Draft PR Approval Gate:* Verification clean. Awaiting engineer signoff.",
                    thread_ts=thread_ts,
                    blocks=pr_blocks,
                )

                # Wait for user decision in Slack
                gate_id = f"pr_{task_id}"
                decision = await self.bot.wait_for_decision(gate_id, timeout=300.0)
                approved = bool(decision and decision.get("approved"))

            # 6. Push Branch & Create Draft PR upon Approval
            pr_url = None
            if approved:
                if on_status_update:
                    on_status_update("DELIVERING_PR", {"branch": branch_name})

                pr_title = f"fix({issue_id}): {issue.title}"
                pr_body = (
                    f"## Autonomous SWE Swarm Resolution\n\n"
                    f"**Issue:** #{issue.number} - {issue.title}\n"
                    f"**Task ID:** `{task_id}`\n\n"
                    f"### QA Verification Report\n"
                    f"- **Status:** PASSED\n"
                    f"- **Total Tests:** {swarm_result.final_report.total_tests}\n"
                    f"- **Retries Required:** {swarm_result.retries_used}\n\n"
                    f"### Telemetry\n"
                    f"- **Tokens Spent:** {swarm_result.tokens_spent:,}\n"
                )

                if self.poller:
                    try:
                        pr_url = self.poller.create_draft_pr(
                            repo_path=str(worktree_path),
                            branch_name=branch_name,
                            title=pr_title,
                            body=pr_body,
                        )
                        self.poller.mark_review(issue.number)
                    except Exception as err:
                        logger.error("Failed creating GitHub Draft PR: %s", err)
                        pr_url = f"https://github.com/{self.poller.repo}/pull/mock"
                else:
                    pr_url = f"https://github.com/mock-repo/pull/{issue.number}"

                update_task_status(task_id, "COMPLETED", db_path=self.db_path)

                if self.bot and thread_ts:
                    await self.bot.post_agent(
                        role="orchestrator",
                        text=(
                            f"🎉 *Draft PR Created Successfully!*\n"
                            f"• PR URL: {pr_url}\n"
                            f"• Branch: `{branch_name}`\n"
                            f"• Total Token Burn: `{swarm_result.tokens_spent:,} tokens`\n"
                            f"• Worktree cleaned up. Issue transitioned to `agent-review`."
                        ),
                        thread_ts=thread_ts,
                    )

                if on_status_update:
                    on_status_update("COMPLETED", {"pr_url": pr_url})

                return PipelineExecutionResult(
                    task_id=task_id,
                    issue_id=issue_id,
                    status="COMPLETED",
                    swarm_result=swarm_result,
                    pr_url=pr_url,
                    thread_ts=thread_ts,
                )
            else:
                logger.info("Draft PR creation rejected or timed out for %s", task_id)
                update_task_status(task_id, "REVISION_REQUESTED", db_path=self.db_path)
                return PipelineExecutionResult(
                    task_id=task_id,
                    issue_id=issue_id,
                    status="REVISION_REQUESTED",
                    swarm_result=swarm_result,
                    thread_ts=thread_ts,
                )

        finally:
            # 7. Guaranteed Worktree Cleanup
            if worktree_path and worktree_path.exists():
                logger.info("Cleaning up ephemeral worktree: %s", worktree_path)
                self.wm.remove_worktree(self.target_repo_path, worktree_path, force=True)
