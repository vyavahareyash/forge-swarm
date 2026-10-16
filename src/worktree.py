"""
Git Worktree Isolation Manager.

Implements ADR 0002: Ephemeral Git Worktrees for Task Isolation.
Provisions and prunes isolated working trees under ./workspaces/issue-<id>.
"""
import contextlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Generator, Optional


class WorktreeError(RuntimeError):
    """Raised when git worktree operations fail."""
    pass


class WorktreeManager:
    """Manages ephemeral Git worktree creation, diff tracking, and cleanup."""

    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = Path(workspace_root or os.path.abspath("workspaces"))
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def _run_git(self, cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
        """Run git command and return completed process, raising WorktreeError on failure."""
        res = subprocess.run(
            ["git"] + cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(cmd)} failed (code {res.returncode}):\n{res.stderr.strip()}"
            )
        return res

    def create_worktree(
        self,
        repo_path: str | Path,
        issue_id: str,
        branch_name: Optional[str] = None,
        base_branch: str = "main",
    ) -> Path:
        """
        Creates an isolated git worktree for the given issue.
        Path: ./workspaces/issue-<id>
        Branch: <branch_name> or fix/<issue_id>
        """
        repo_dir = Path(repo_path).resolve()
        worktree_path = (self.workspace_root / f"issue-{issue_id}").resolve()
        branch = branch_name or f"fix/{issue_id}"

        # Clean existing worktree if present
        if worktree_path.exists():
            self.remove_worktree(repo_dir, worktree_path, force=True)

        # Ensure base branch exists or fallback to current HEAD
        check_base = subprocess.run(
            ["git", "rev-parse", "--verify", base_branch],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
        )
        base_ref = base_branch if check_base.returncode == 0 else "HEAD"

        # Check if target branch already exists; if so, delete it first to ensure clean state
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
        )

        # Run git worktree add -b <branch> <path> <base_ref>
        self._run_git(
            ["worktree", "add", "-b", branch, str(worktree_path), base_ref],
            cwd=repo_dir,
        )
        return worktree_path

    def remove_worktree(
        self,
        repo_path: str | Path,
        worktree_path: str | Path,
        force: bool = True,
    ) -> None:
        """Prunes and removes an isolated worktree."""
        repo_dir = Path(repo_path).resolve()
        wt_path = Path(worktree_path).resolve()

        if not wt_path.exists():
            # Still prune metadata
            subprocess.run(["git", "worktree", "prune"], cwd=str(repo_dir), capture_output=True)
            return

        cmd = ["worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(wt_path))

        try:
            self._run_git(cmd, cwd=repo_dir)
        except WorktreeError:
            # Fallback if git fails to remove folder: prune and rmtree
            subprocess.run(["git", "worktree", "prune"], cwd=str(repo_dir), capture_output=True)
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)

        # Prune git internal worktree tracking
        subprocess.run(["git", "worktree", "prune"], cwd=str(repo_dir), capture_output=True)

    def get_diff(self, worktree_path: str | Path) -> str:
        """Returns unified diff of unstaged and staged changes in worktree."""
        wt_path = Path(worktree_path).resolve()
        # Staged + unstaged diff against HEAD
        res = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()

        # If nothing against HEAD, check unstaged alone
        res_unstaged = subprocess.run(
            ["git", "diff"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
        )
        return res_unstaged.stdout.strip()

    def commit_changes(self, worktree_path: str | Path, message: str) -> str:
        """Stages all changes and commits them in the worktree."""
        wt_path = Path(worktree_path).resolve()
        self._run_git(["add", "."], cwd=wt_path)
        # Check if there is anything to commit
        status = self._run_git(["status", "--porcelain"], cwd=wt_path).stdout.strip()
        if not status:
            return ""
        self._run_git(["commit", "-m", message], cwd=wt_path)
        commit_hash = self._run_git(["rev-parse", "HEAD"], cwd=wt_path).stdout.strip()
        return commit_hash

    @contextlib.contextmanager
    def isolated_worktree(
        self,
        repo_path: str | Path,
        issue_id: str,
        branch_name: Optional[str] = None,
        base_branch: str = "main",
        cleanup: bool = True,
    ) -> Generator[Path, None, None]:
        """Context manager creating and auto-cleaning an ephemeral worktree."""
        wt_path = self.create_worktree(repo_path, issue_id, branch_name, base_branch)
        try:
            yield wt_path
        finally:
            if cleanup:
                self.remove_worktree(repo_path, wt_path, force=True)
