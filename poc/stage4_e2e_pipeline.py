"""
Stage 4 PoC: End-to-End Pipeline Verification Script.

Demonstrates the complete event-driven pipeline:
1. Issue Ingestion: Detects/fetches issue labeled 'agent-ready'.
2. Workspace Isolation: Provisions ephemeral Git worktree (`./workspaces/issue-<id>`).
3. War-Room Thread: Streams updates under Atlas, Forge, and Sentinel in Slack.
4. Autonomous Swarm: Coordinates Coder & QA with 3-retry self-correction loop.
5. Interactive Review: Renders unified diff and test report.
6. Human Gate: Awaits or simulates `[Approve & Create Draft PR]` Block Kit button.
7. Delivery: Pushes branch, creates GitHub Draft PR, updates labels, and cleans worktree.

Run in mock mode (offline, fast):
    .venv/bin/python poc/stage4_e2e_pipeline.py --mock

Run in live mode (with GitHub & Slack):
    .venv/bin/python poc/stage4_e2e_pipeline.py
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.pipeline import E2EPipeline
from src.slack.bot import SlackWarRoomBot
from src.storage.db import init_db
from src.triggers.github_poller import GitHubIssue, GitHubPoller
from tests.fixtures.sample_repo import ensure_sample_repo

load_dotenv()


async def run_stage4_poc(mock_mode: bool = False, auto_approve: bool = False):
    print("=" * 75)
    print("🚀 Stage 4 PoC: End-to-End GitHub Issue Poller & Draft PR Delivery")
    print("=" * 75)

    db_path = os.path.abspath("storage/state.db")
    init_db(db_path)

    bot_token = os.getenv("SLACK_BOT_TOKEN")
    app_token = os.getenv("SLACK_APP_TOKEN")
    channel_id = os.getenv("SLACK_CHANNEL_ID")
    github_repo = os.getenv("GITHUB_REPO")
    target_repo_path = os.getenv("TARGET_REPO_PATH")

    if mock_mode or not (bot_token and app_token and channel_id and github_repo and target_repo_path):
        print("\n[INFO] Running in MOCK / FIXTURE Mode (Zero network required)")
        await run_mock_pipeline(db_path)
        return

    print("\n[1/6] Connecting to GitHub & Slack Services...")
    print(f"      Target Repo Path: {target_repo_path}")
    print(f"      GitHub Repo:      {github_repo}")
    print(f"      Slack Channel:    {channel_id}")

    # Initialize bot
    bot = SlackWarRoomBot(
        bot_token=bot_token,
        app_token=app_token,
        default_channel=channel_id,
        db_path=db_path,
    )
    asyncio.create_task(bot.start())
    await asyncio.sleep(2.0)
    print("      ✓ Slack Socket Mode connected.")

    # Initialize poller
    poller = GitHubPoller(
        repo=github_repo,
        poll_interval=10.0,
        db_path=db_path,
    )

    pipeline = E2EPipeline(
        target_repo_path=target_repo_path,
        poller=poller,
        bot=bot,
        db_path=db_path,
        mode="standalone",
    )

    print("\n[2/6] Querying GitHub for issues labeled 'agent-ready'...")
    issues = poller.fetch_ready_issues()
    if not issues:
        print(f"      ℹ️ No 'agent-ready' issues found in {github_repo}.")
        print("      Creating/using Issue #1 for verification...")
        target_issue = GitHubIssue(
            number=1,
            title="Fix Fibonacci calculation defect",
            body="calculate_fibonacci(1) returns 2 instead of 1 causing tests to fail.",
            labels=["agent-ready"],
            url=f"https://github.com/{github_repo}/issues/1",
        )
    else:
        target_issue = issues[0]
        print(f"      ✓ Found ready issue #{target_issue.number}: '{target_issue.title}'")

    print(f"\n[3/6] Launching E2E Pipeline for Issue #{target_issue.number}...")
    print("      • Transitioning label: agent-ready -> agent-in-progress")
    print("      • Creating Slack War-Room thread in channel...")
    print("      • Provisioning isolated Git worktree under ./workspaces/...")

    def on_status(stage: str, data: dict):
        print(f"      [PIPELINE STAGE] {stage} -> {list(data.keys())}")

    start_t = time.time()
    res = await pipeline.execute_issue(
        issue=target_issue,
        auto_approve_pr=auto_approve,
        on_status_update=on_status,
    )
    elapsed = time.time() - start_t

    print("\n[4/6] Verification Results:")
    print("=" * 50)
    print(f"Task ID:         {res.task_id}")
    print(f"Issue:           {res.issue_id}")
    print(f"Status:          {res.status}")
    print(f"Elapsed Time:    {elapsed:.2f}s")
    if res.swarm_result:
        print(f"Tokens Spent:    {res.swarm_result.tokens_spent:,}")
        print(f"Retries Used:    {res.swarm_result.retries_used}")
    if res.pr_url:
        print(f"Draft PR URL:    {res.pr_url}")
    print("=" * 50)

    # Check worktree pruned
    wt_path = Path("workspaces") / f"issue-{res.issue_id}"
    print(f"\n[5/6] Worktree Cleanup Verification: {'Cleaned ✓' if not wt_path.exists() else 'Still exists ❌'}")

    await bot.stop()
    print("\n[6/6] Stage 4 PoC Complete! 🎉\n")


async def run_mock_pipeline(db_path: str):
    """Executes full pipeline offline using sample_repo fixture."""
    print("Setting up sample repo fixture...")
    sample_repo = ensure_sample_repo(reset=True)

    issue = GitHubIssue(
        number=101,
        title="Fix Fibonacci calculation defect",
        body="calculate_fibonacci(1) returns 2 instead of 1 in calculator.py",
        labels=["agent-ready"],
        url="https://github.com/example/sample-calc/issues/101",
    )

    pipeline = E2EPipeline(
        target_repo_path=str(sample_repo),
        db_path=db_path,
        mode="standalone",
    )

    print("Executing offline pipeline with auto_approve_pr=True...")
    res = await pipeline.execute_issue(issue=issue, auto_approve_pr=True)

    print("=" * 50)
    print(f"Task ID:      {res.task_id}")
    print(f"Status:       {res.status}")
    print(f"Tokens Spent: {res.swarm_result.tokens_spent:,}")
    print(f"PR Delivery:  {res.pr_url}")
    print("=" * 50)
    assert res.status == "COMPLETED"
    print("✓ Offline Stage 4 PoC Verified Successfully!")


def main():
    parser = argparse.ArgumentParser(description="Stage 4 End-to-End Pipeline PoC")
    parser.add_argument("--mock", action="store_true", help="Run with synthetic/mock data offline")
    parser.add_argument("--auto-approve", action="store_true", help="Automatically approve Draft PR without waiting for Slack button")
    args = parser.parse_args()

    asyncio.run(run_stage4_poc(mock_mode=args.mock, auto_approve=args.auto_approve))


if __name__ == "__main__":
    main()
