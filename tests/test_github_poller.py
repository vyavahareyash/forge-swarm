"""Unit tests for GitHubPoller and issue lifecycle."""
import json
import subprocess

import pytest

from src.storage.db import create_task, init_db, update_task_status
from src.triggers.github_poller import GitHubIssue, GitHubPoller, GitHubPollerError


def test_github_issue_schema():
    issue = GitHubIssue(
        number=42,
        title="Add healthcheck endpoint",
        body="Requires /health endpoint returning 200 OK",
        labels=["agent-ready", "backend"],
        url="https://github.com/org/repo/issues/42",
    )
    assert issue.number == 42
    assert issue.issue_id == "issue-42"
    assert "agent-ready" in issue.labels


def test_fetch_ready_issues_success(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    sample_output = json.dumps([
        {
            "number": 1,
            "title": "Bug in calculator",
            "body": "Fix base case",
            "labels": [{"name": "agent-ready"}],
            "url": "https://github.com/org/repo/issues/1",
        },
        {
            "number": 2,
            "title": "Update README",
            "body": "Fix typo",
            "labels": [{"name": "agent-ready"}],
            "url": "https://github.com/org/repo/issues/2",
        },
    ])

    def mock_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=sample_output, stderr="")

    poller = GitHubPoller(
        repo="org/repo",
        db_path=db_path,
        command_runner=mock_runner,
    )

    issues = poller.fetch_ready_issues()
    assert len(issues) == 2
    assert issues[0].number == 1
    assert issues[0].title == "Bug in calculator"
    assert issues[1].number == 2


def test_fetch_ready_issues_deduplication(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    # Pre-record issue-1 as COMPLETED in SQLite
    create_task("task-1", issue_id="issue-1", db_path=db_path)
    update_task_status("task-1", "COMPLETED", db_path=db_path)

    sample_output = json.dumps([
        {
            "number": 1,
            "title": "Bug in calculator (completed)",
            "labels": [{"name": "agent-ready"}],
        },
        {
            "number": 2,
            "title": "New issue",
            "labels": [{"name": "agent-ready"}],
        },
    ])

    def mock_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=sample_output, stderr="")

    poller = GitHubPoller(
        repo="org/repo",
        db_path=db_path,
        command_runner=mock_runner,
    )

    issues = poller.fetch_ready_issues()
    # Issue 1 is completed in DB, so only Issue 2 should be returned
    assert len(issues) == 1
    assert issues[0].number == 2


def test_label_transitions():
    executed_commands = []

    def mock_runner(cmd, **kwargs):
        executed_commands.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    poller = GitHubPoller(
        repo="org/repo",
        command_runner=mock_runner,
    )

    poller.mark_in_progress(42)
    assert any("agent-in-progress" in cmd and "agent-ready" in cmd for cmd in executed_commands)

    poller.mark_review(42)
    assert any("agent-review" in cmd and "agent-in-progress" in cmd for cmd in executed_commands)


def test_poller_error_handling():
    def failing_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="API rate limit exceeded")

    poller = GitHubPoller(
        repo="org/repo",
        command_runner=failing_runner,
    )

    # fetch_ready_issues handles error gracefully and returns empty list
    issues = poller.fetch_ready_issues()
    assert issues == []

    # mark_in_progress raises GitHubPollerError
    with pytest.raises(GitHubPollerError):
        poller.mark_in_progress(99)
