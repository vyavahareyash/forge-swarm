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
import re
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
        use_agy: bool = False,
    ):
        self.repo = repo or os.getenv("GITHUB_REPO")
        self.poll_interval = poll_interval
        self.db_path = db_path
        if command_runner is not None:
            self._command_runner = command_runner
        elif use_agy:
            from src.agents.agy_runner import AgyRunner

            agy = AgyRunner()
            if agy.is_available():
                self._command_runner = agy.execute_command_sync
            else:
                self._command_runner = subprocess.run
        else:
            self._command_runner = subprocess.run
        self._is_running = False

    def _run_gh(
        self, args: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess:
        """Run gh command and return CompletedProcess, raising GitHubPollerError on failure."""
        cmd = ["gh"] + args
        env = os.environ.copy()
        token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
        if token:
            env["GH_TOKEN"] = token
            env["GITHUB_TOKEN"] = token
            env["GH_PROMPT_DISABLED"] = "1"
            env["NO_COLOR"] = "1"
        try:
            try:
                res = self._command_runner(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    check=False,
                    env=env,
                )
            except TypeError:
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

    def fetch_issues_by_label(self, label: str) -> list[GitHubIssue]:
        """
        Queries `gh issue list --repo <repo> --label <label> --json number,title,body,labels,url`.
        Returns parsed list of GitHubIssue objects, filtering out already tracked active/completed tasks.
        """
        if not self.repo:
            logger.warning("No GITHUB_REPO configured. Skipping fetch_issues_by_label.")
            return []

        args = [
            "issue",
            "list",
            "--repo",
            self.repo,
            "--label",
            label,
            "--json",
            "number,title,body,labels,url",
        ]

        try:
            res = self._run_gh(args)
            raw_data = json.loads(res.stdout or "[]")
        except (GitHubPollerError, json.JSONDecodeError) as e:
            logger.error(
                "Failed to fetch issues from GitHub for label %s: %s", label, e
            )
            return []

        issues: list[GitHubIssue] = []
        for item in raw_data:
            labels_raw = item.get("labels", [])
            label_names = [
                lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
                for lbl in labels_raw
            ]

            issue = GitHubIssue(
                number=item["number"],
                title=item.get("title", ""),
                body=item.get("body", "") or "",
                labels=label_names,
                url=item.get("url"),
            )

            if not self._is_task_already_handled(issue.issue_id):
                issues.append(issue)

        return issues

    def fetch_ready_issues(self) -> list[GitHubIssue]:
        """
        Queries `gh issue list` for ready issues ('agent-ready' or 'status:ready-for-dev').
        Returns parsed list of GitHubIssue objects.
        """
        issues = self.fetch_issues_by_label("agent-ready")
        ready_for_dev = self.fetch_issues_by_label("status:ready-for-dev")
        seen_numbers = {i.number for i in issues}
        for item in ready_for_dev:
            if item.number not in seen_numbers:
                issues.append(item)
                seen_numbers.add(item.number)
        return issues

    def fetch_triage_issues(self) -> list[GitHubIssue]:
        """
        Queries `gh issue list` for issues needing intake & grilling ('triage:agent' or 'needs-spec').
        """
        issues = self.fetch_issues_by_label("triage:agent")
        needs_spec = self.fetch_issues_by_label("needs-spec")
        seen_numbers = {i.number for i in issues}
        for item in needs_spec:
            if item.number not in seen_numbers:
                issues.append(item)
                seen_numbers.add(item.number)
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
                    if status in (
                        "COMPLETED",
                        "RUNNING",
                        "INTAKE",
                        "GRILLING",
                        "READY_FOR_DEV",
                        "PAUSED_BUDGET",
                        "PAUSED_APPROVAL",
                    ):
                        return True
            return False
        finally:
            conn.close()

    def create_draft_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> GitHubIssue:
        """
        Creates a draft GitHub issue via `gh issue create`.
        Prepends '[Draft]' to title if not already present.
        """
        if not self.repo:
            raise GitHubPollerError("GITHUB_REPO not configured")

        draft_title = title if title.startswith("[Draft]") else f"[Draft] {title}"
        target_labels = labels or ["triage:agent"]

        args = [
            "issue",
            "create",
            "--repo",
            self.repo,
            "--title",
            draft_title,
            "--body",
            body,
        ]
        for lbl in target_labels:
            args.extend(["--label", lbl])

        try:
            res = self._run_gh(args)
            output = res.stdout.strip()

            # Parse issue number and URL from gh output (usually returns issue URL)
            # e.g. https://github.com/owner/repo/issues/42
            issue_number = 0
            if "/issues/" in output:
                try:
                    issue_number = int(output.split("/issues/")[-1].split("/")[0])
                except ValueError:
                    pass

            if issue_number == 0:
                # Fallback regex search for number in output
                match = re.search(r"#?(\d+)", output)
                if match:
                    issue_number = int(match.group(1))

            return GitHubIssue(
                number=issue_number,
                title=draft_title,
                body=body,
                labels=target_labels,
                url=output if output.startswith("http") else None,
            )
        except GitHubPollerError as err:
            token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
            if token and self.repo:
                try:
                    logger.info(
                        "Falling back to GitHub REST API for draft issue creation..."
                    )
                    return self._create_issue_rest(draft_title, body, target_labels)
                except Exception as rest_err:
                    logger.warning("GitHub REST API fallback also failed: %s", rest_err)
            raise err

    def _create_issue_rest(
        self, title: str, body: str, labels: list[str] | None = None
    ) -> GitHubIssue:
        import urllib.request

        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        url = f"https://api.github.com/repos/{self.repo}/issues"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "forge-swarm",
            "Content-Type": "application/json",
        }
        payload = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            lbls = [
                lbl["name"] if isinstance(lbl, dict) else str(lbl)
                for lbl in data.get("labels", [])
            ]
            return GitHubIssue(
                number=data["number"],
                title=data["title"],
                body=data.get("body") or "",
                labels=lbls,
                url=data.get("html_url"),
            )

    def _update_issue_rest(
        self, issue_number: int, title: str | None = None, body: str | None = None
    ) -> None:
        import urllib.request

        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        url = f"https://api.github.com/repos/{self.repo}/issues/{issue_number}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "forge-swarm",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if not payload:
            return
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()

    def _add_issue_labels_rest(self, issue_number: int, labels: list[str]) -> None:
        import urllib.request

        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        url = f"https://api.github.com/repos/{self.repo}/issues/{issue_number}/labels"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "forge-swarm",
            "Content-Type": "application/json",
        }
        payload = {"labels": labels}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()

    def update_issue_spec(
        self,
        issue_number: int,
        title: str | None = None,
        body: str | None = None,
    ) -> None:
        """Updates the title and body of a GitHub issue via `gh issue edit`."""
        if not self.repo:
            return
        args = ["issue", "edit", str(issue_number), "--repo", self.repo]
        if title is not None:
            args.extend(["--title", title])
        if body is not None:
            args.extend(["--body", body])
        try:
            self._run_gh(args)
        except GitHubPollerError:
            token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
            if token:
                try:
                    self._update_issue_rest(issue_number, title=title, body=body)
                    return
                except Exception as e:
                    logger.warning("REST API update failed: %s", e)
            raise

    def mark_ready_for_dev(self, issue_number: int) -> None:
        """
        Transitions issue from intake to ready-for-dev:
        removes 'triage:agent' and 'needs-spec', adds 'status:ready-for-dev'.
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
            "status:ready-for-dev",
            "--remove-label",
            "triage:agent",
            "--remove-label",
            "needs-spec",
        ]
        try:
            self._run_gh(args)
        except GitHubPollerError:
            token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
            if token:
                try:
                    self._add_issue_labels_rest(issue_number, ["status:ready-for-dev"])
                    return
                except Exception as e:
                    logger.warning("REST API add label failed: %s", e)
            raise

    def mark_in_progress(self, issue_number: int) -> None:
        """
        Transitions issue label:
        removes 'agent-ready' and 'status:ready-for-dev', adds 'agent-in-progress'.
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
            "--remove-label",
            "status:ready-for-dev",
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

    def push_branch(
        self, repo_path: str, branch_name: str, remote: str = "origin"
    ) -> None:
        """Pushes the specified branch to remote."""
        cmd = ["git", "push", "-u", remote, branch_name]
        res = self._command_runner(
            cmd, cwd=repo_path, capture_output=True, text=True, check=False
        )
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
        on_triage_callback: Callable[[GitHubIssue], Coroutine[Any, Any, None]]
        | None = None,
    ) -> None:
        """Continuously polls for issues ('agent-ready' / 'status:ready-for-dev' and 'triage:agent') and invokes callbacks."""
        self._is_running = True
        logger.info(
            "Starting GitHub Issue Poller loop (interval: %ss, repo: %s)...",
            self.poll_interval,
            self.repo,
        )
        try:
            while self._is_running:
                try:
                    # 1. Poll triage issues (intake / grilling)
                    if on_triage_callback:
                        triage_issues = self.fetch_triage_issues()
                        for issue in triage_issues:
                            logger.info(
                                "Discovered triage issue #%s: %s",
                                issue.number,
                                issue.title,
                            )
                            await on_triage_callback(issue)

                    # 2. Poll ready issues (development execution)
                    ready_issues = self.fetch_ready_issues()
                    for issue in ready_issues:
                        logger.info(
                            "Discovered ready issue #%s: %s",
                            issue.number,
                            issue.title,
                        )
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
