"""
GitHub Issue Poller & Lifecycle Manager for Autonomous SWE Swarm.

Implements Stage 4 Issue Trigger:
- Polls GitHub issues labeled 'agent-ready' via `gh` CLI.
- Manages label state transitions ('agent-ready' -> 'agent-in-progress' -> 'agent-review').
- Creates Draft PRs via `gh pr create --draft`.
- Supports dependency injection for command execution (enabling offline mock testing).
"""
import asyncio
import json
import logging
import os
import subprocess
from collections.abc import Callable, Coroutine
from typing import Any

from pydantic import BaseModel, Field

from src.storage.db import get_connection

logger = logging.getLogger(__name__)


class GitHubIssue(BaseModel):
    """Normalized GitHub Issue representation."""
    number: int
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    url: str | None = None

    @property
    def issue_id(self) -> str:
        return f"issue-{self.number}"


class GitHubPollerError(RuntimeError):
    """Raised when GitHub CLI operations fail."""
    pass


class GitHubPoller:
    """
    Polls GitHub repository for issues labeled 'agent-ready'
    and manages issue lifecycle and Draft PR delivery.
    """

    def __init__(
        self,
        repo: str | None = None,
        poll_interval: float = 30.0,
        db_path: str | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ):
        self.repo = repo or os.getenv("GITHUB_REPO")
        self.poll_interval = poll_interval
        self.db_path = db_path
        self._command_runner = command_runner or subprocess.run
        self._is_running = False

    def _run_gh(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
        """Run gh command and return CompletedProcess, raising GitHubPollerError on failure."""
        cmd = ["gh"] + args
        try:
            res = self._command_runner(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                check=False,
            )
        except Exception as e:
            raise GitHubPollerError(f"Failed to execute {' '.join(cmd)}: {e}") from e

        if res.returncode != 0:
            raise GitHubPollerError(
                f"gh command failed (exit code {res.returncode}):\n{' '.join(cmd)}\nStderr: {res.stderr.strip()}"
            )
        return res

    def fetch_ready_issues(self) -> list[GitHubIssue]:
        """
        Queries `gh issue list --repo <repo> --label agent-ready --json number,title,body,labels,url`.
        Returns parsed list of GitHubIssue objects, filtering out already tracked active/completed tasks.
        """
        if not self.repo:
            logger.warning("No GITHUB_REPO configured. Skipping fetch_ready_issues.")
            return []

        args = [
            "issue",
            "list",
            "--repo",
            self.repo,
            "--label",
            "agent-ready",
            "--json",
            "number,title,body,labels,url",
        ]

        try:
            res = self._run_gh(args)
            raw_data = json.loads(res.stdout or "[]")
        except (GitHubPollerError, json.JSONDecodeError) as e:
            logger.error("Failed to fetch issues from GitHub: %s", e)
            return []

        issues: list[GitHubIssue] = []
        for item in raw_data:
            # Extract label names
            labels_raw = item.get("labels", [])
            label_names = [lbl.get("name", "") if isinstance(lbl, dict) else str(lbl) for lbl in labels_raw]

            issue = GitHubIssue(
                number=item["number"],
                title=item.get("title", ""),
                body=item.get("body", "") or "",
                labels=label_names,
                url=item.get("url"),
            )

            # Check SQLite deduplication
            if not self._is_task_already_handled(issue.issue_id):
                issues.append(issue)

        return issues

    def _is_task_already_handled(self, issue_id: str) -> bool:
        """Checks if issue is currently being processed or already successfully resolved."""
        conn = get_connection(self.db_path)
        try:
            with conn:
                cursor = conn.execute(
                    "SELECT status FROM tasks WHERE issue_id = ? ORDER BY created_at DESC LIMIT 1;",
                    (issue_id,),
                )
                row = cursor.fetchone()
                if row:
                    status = row["status"]
                    # If completed or running, skip duplicate pickup
                    if status in ("COMPLETED", "RUNNING", "PAUSED_BUDGET", "PAUSED_APPROVAL"):
                        return True
            return False
        finally:
            conn.close()

    def mark_in_progress(self, issue_number: int) -> None:
        """
        Transitions issue label:
        removes 'agent-ready', adds 'agent-in-progress'.
        """
        if not self.repo:
            return
        args = [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            self.repo,
            "--add-label",
            "agent-in-progress",
            "--remove-label",
            "agent-ready",
        ]
        self._run_gh(args)

    def mark_review(self, issue_number: int) -> None:
        """
        Transitions issue label:
        removes 'agent-in-progress', adds 'agent-review'.
        """
        if not self.repo:
            return
        args = [
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            self.repo,
            "--add-label",
            "agent-review",
            "--remove-label",
            "agent-in-progress",
        ]
        self._run_gh(args)

    def push_branch(self, repo_path: str, branch_name: str, remote: str = "origin") -> None:
        """Pushes the specified branch to remote."""
        cmd = ["git", "push", "-u", remote, branch_name]
        res = self._command_runner(cmd, cwd=repo_path, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise GitHubPollerError(f"git push failed:\n{res.stderr.strip()}")

    def create_draft_pr(
        self,
        repo_path: str,
        branch_name: str,
        title: str,
        body: str,
        base: str = "main",
    ) -> str:
        """
        Pushes branch and creates a GitHub Draft Pull Request via `gh pr create --draft`.
        Returns the created PR URL.
        """
        if not self.repo:
            raise GitHubPollerError("GITHUB_REPO not configured")

        # 1. Push branch to origin
        self.push_branch(repo_path, branch_name)

        # 2. Create draft PR
        args = [
            "pr",
            "create",
            "--repo",
            self.repo,
            "--head",
            branch_name,
            "--base",
            base,
            "--draft",
            "--title",
            title,
            "--body",
            body,
        ]
        res = self._run_gh(args, cwd=repo_path)
        pr_url = res.stdout.strip()
        logger.info("Draft PR created: %s", pr_url)
        return pr_url

    async def poll_loop(
        self,
        on_issue_callback: Callable[[GitHubIssue], Coroutine[Any, Any, None]],
    ) -> None:
        """Continuously polls for issues labeled 'agent-ready' and invokes callback."""
        self._is_running = True
        logger.info("Starting GitHub Issue Poller loop (interval: %ss, repo: %s)...", self.poll_interval, self.repo)
        try:
            while self._is_running:
                try:
                    ready_issues = self.fetch_ready_issues()
                    for issue in ready_issues:
                        logger.info("Discovered agent-ready issue #%s: %s", issue.number, issue.title)
                        await on_issue_callback(issue)
                except Exception as e:
                    logger.error("Error during GitHub poller cycle: %s", e)

                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("GitHub Issue Poller loop cancelled.")
            self._is_running = False

    def stop(self) -> None:
        """Stops the polling loop."""
        self._is_running = False
