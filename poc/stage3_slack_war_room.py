"""
Stage 3 PoC: Slack Socket Mode War-Room & Block Kit Approval Gates.

Executes a live multi-agent dialogue in Slack `#agent-ops`:
1. Connects via Slack Bolt Socket Mode (zero public ingress ports).
2. Dispatches distinct persona updates in a dedicated thread:
   - Atlas (Lead Architect) :compass:
   - Sentinel (QA Automation) :shield:
   - Forge (SWE Sub-Agent) :hammer_and_wrench:
   - Quota Warden (Budget Gatekeeper) :stopwatch:
3. Triggers interactive Block Kit budget approval gate ([Grant +50k Tokens] / [Abort Task]).
4. Waits asynchronously for engineer interaction in Slack.
5. Resumes execution and completes verification upon button click.
"""
import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.slack.approvals import (
    build_budget_exhaustion_blocks,
    build_draft_pr_blocks,
)
from src.slack.bot import SlackWarRoomBot
from src.storage.db import (
    create_task,
    get_task,
    init_db,
    record_turn_tokens,
    update_task_thread_ts,
)

load_dotenv()


async def run_stage3_war_room(mock_mode: bool = False) -> None:
    print("=" * 70)
    print("🤖 Stage 3 PoC: Slack Socket Mode War-Room & Block Kit Approval Gates")
    print("=" * 70)

    db_path = os.path.abspath("storage/state.db")
    init_db(db_path)

    bot_token = os.getenv("SLACK_BOT_TOKEN")
    app_token = os.getenv("SLACK_APP_TOKEN")
    channel_id = os.getenv("SLACK_CHANNEL_ID")

    if mock_mode or not (bot_token and app_token and channel_id):
        print("\n[INFO] Running in MOCK / SIMULATION Mode (No Slack network activity)")
        await run_mock_war_room(db_path)
        return

    print("\n[1/5] Initializing Slack Socket Mode Bot...")
    print(f"      Channel ID: {channel_id}")
    bot = SlackWarRoomBot(
        bot_token=bot_token,
        app_token=app_token,
        default_channel=channel_id,
        db_path=db_path,
    )

    # Start Socket Mode handler in background task
    socket_task = asyncio.create_task(bot.start())
    # Give websocket time to handshake
    await asyncio.sleep(2.0)
    print("      ✓ Socket Mode connected successfully (zero incoming firewall ports)")

    task_id = f"task-issue-101-{int(datetime.now(UTC).timestamp())}"
    issue_id = "issue-101"

    create_task(
        task_id=task_id,
        issue_id=issue_id,
        budget_limit=150_000,
        turn_limit=15,
        db_path=db_path,
    )

    try:
        print(f"\n[2/5] Creating dedicated War-Room Thread in channel {channel_id}...")
        root_msg = await bot.post_agent(
            role="orchestrator",
            text=(
                f"🚨 *New Incident / Task Spawned:* `{issue_id}`: *Fix Fibonacci calculation error*\n"
                f"Task ID: `{task_id}` | Budget Ceiling: `150,000 tokens`\n"
                f"War-Room Thread active. Agents will stream progress below. 👇"
            ),
        )
        thread_ts = root_msg.get("ts")
        if not thread_ts:
            raise RuntimeError(f"Failed to extract thread_ts from Slack response: {root_msg}")

        update_task_thread_ts(task_id, thread_ts, db_path=db_path)
        print(f"      ✓ Thread created: thread_ts={thread_ts}")

        # Stream Turn 1: Atlas Planning
        print("\n[3/5] Streaming multi-persona dialogue...")
        print("      Posting Atlas (Lead Architect) plan...")
        await bot.post_agent(
            role="orchestrator",
            thread_ts=thread_ts,
            text=(
                "🧭 *Atlas (Lead Architect):* Analyzed issue specifications.\n"
                "• Decomposed into 3 subtasks (Baseline QA -> Forge Patch -> Sentinel Signoff).\n"
                "• Provisioned isolated Git worktree: `workspaces/issue-101`.\n"
                "• Dispatching Sentinel for baseline failure verification."
            ),
        )
        record_turn_tokens(task_id, 1, 40_000, 10_000, 15_000, 65_000, db_path=db_path)
        await asyncio.sleep(1.5)

        # Stream Turn 2: Sentinel Baseline QA
        print("      Posting Sentinel (QA Automation) test results...")
        await bot.post_agent(
            role="qa",
            thread_ts=thread_ts,
            text=(
                "🛡️ *Sentinel (QA Automation):* Executed test suite in isolated worktree.\n"
                "❌ *Test Failure Confirmed:* `tests/test_calculator.py::test_fibonacci_base`\n"
                "```assert calculate_fibonacci(1) == 1 (returned 2)```\n"
                "Routing failure report and traceback to Forge for remediation."
            ),
        )
        record_turn_tokens(task_id, 2, 20_000, 5_000, 10_000, 35_000, db_path=db_path)
        await asyncio.sleep(1.5)

        # Stream Turn 3: Forge Patch
        print("      Posting Forge (SWE Sub-Agent) code patch...")
        await bot.post_agent(
            role="coder",
            thread_ts=thread_ts,
            text=(
                "🛠️ *Forge (SWE Sub-Agent):* Received QA traceback. Located defect in `math_lib/calculator.py`.\n"
                "Applied patch: modified base case conditional `return 2` -> `return 1`.\n"
                "Requesting Sentinel re-verification."
            ),
        )
        record_turn_tokens(task_id, 3, 30_000, 8_000, 12_000, 50_000, db_path=db_path)
        await asyncio.sleep(1.5)

        # Total spend is now 150,000 / 150,000 tokens! Budget Ceiling Hit!
        print("\n[4/5] Triggering Quota Warden Budget Approval Gate...")
        task_data = get_task(task_id, db_path=db_path)
        budget_blocks = build_budget_exhaustion_blocks(
            task_id=task_id,
            current_tokens=task_data["current_tokens"],
            budget_limit=task_data["budget_limit"],
            turn_count=task_data["turn_count"],
        )

        bot.register_gate(task_id)
        await bot.post_agent(
            role="warden",
            thread_ts=thread_ts,
            text=f"🛑 Task budget ceiling reached ({task_data['current_tokens']:,} tokens). Approval required.",
            blocks=budget_blocks,
        )

        print("\n" + "=" * 70)
        print("⏸️  EXECUTION PAUSED: Waiting for human interaction in Slack...")
        print(f"👉 Go to Slack channel {channel_id} and click '[Grant +50k Tokens]' or '[Abort Task]'.")
        print("=" * 70 + "\n")

        # Wait for user click in Slack
        decision = await bot.wait_for_decision(task_id, timeout=120.0)
        if not decision:
            print("⚠️ Timeout waiting for Slack button click (120s limit).")
            return

        print(f"      ✓ Received Slack interaction from `{decision.get('user')}`: {decision.get('action')}")
        await asyncio.sleep(1.0)

        if not decision.get("approved"):
            print("🛑 Task aborted by user in Slack.")
            return

        # Turn 4: Sentinel Green Verification
        print("\n[5/5] Resuming Swarm execution after approval...")
        await bot.post_agent(
            role="qa",
            thread_ts=thread_ts,
            text=(
                "🛡️ *Sentinel (QA Automation):* Re-ran full test suite on patched worktree.\n"
                "✅ *Result: PASSED (3/3 tests clean)*\n"
                "```================ 3 passed in 0.04s ===============```\n"
                "Code verified regression-free."
            ),
        )
        await asyncio.sleep(1.0)

        # Turn 5: Atlas Draft PR Delivery
        diff_stats = "math_lib/calculator.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)"
        pr_blocks = build_draft_pr_blocks(
            task_id=task_id,
            issue_id=issue_id,
            branch_name="fix/issue-101",
            diff_stats=diff_stats,
            test_summary="3 passed in 0.04s",
        )
        await bot.post_agent(
            role="orchestrator",
            thread_ts=thread_ts,
            text="🎉 Issue resolution complete. Draft PR approval requested.",
            blocks=pr_blocks,
        )
        print("      ✓ Draft PR Block Kit review card posted in Slack thread.")

        # Check for any human steering text replies
        steering_note = await bot.get_steering_instruction(task_id)
        if steering_note:
            print(f"      💬 Detected human steering message in thread: '{steering_note}'")

        print("\n" + "=" * 70)
        print("✅ STAGE 3 POC VERIFICATION COMPLETE!")
        print("=" * 70)
        final_task = get_task(task_id, db_path=db_path)
        print(f"• Task ID:        {final_task['task_id']}")
        print(f"• Final Status:   {final_task['status']}")
        print(f"• Budget Limit:   {final_task['budget_limit']:,} tokens (Extended from 150,000)")
        print(f"• Tokens Burned:  {final_task['current_tokens']:,} tokens")
        print(f"• Slack Thread:   {thread_ts}")
        print("=" * 70)

    finally:
        socket_task.cancel()
        await bot.stop()


async def run_mock_war_room(db_path: str) -> None:
    print("[MOCK] Simulating Slack Multi-Persona Thread & Block Kit Interaction...")
    task_id = "task-mock-101"
    create_task(task_id, "issue-mock-101", budget_limit=150_000, db_path=db_path)
    print(" [Atlas]        Analyzed issue & created plan.")
    print(" [Sentinel]     Caught intentional bug in calculator.py.")
    print(" [Forge]        Applied patch to fix base case.")
    print(" [Quota Warden] Budget limit reached (150,000 tokens). Gate engaged.")
    print(" [User Action]  Simulating button click: [Grant +50k Tokens]...")
    bot = SlackWarRoomBot(bot_token=None, app_token=None, db_path=db_path)
    gate = bot.register_gate(task_id)
    bot.resolve_gate(task_id, {"action": "grant_budget_50k", "approved": True, "user": "EngineerMock"})
    decision = await bot.wait_for_decision(task_id)
    print(f" [Quota Warden] Unpaused: {decision}")
    print(" [Sentinel]     Re-verified tests: All passed!")
    print(" [Atlas]        Ready for Draft PR delivery.")
    print("[MOCK] Verification SUCCESS.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 3 Slack War-Room PoC")
    parser.add_argument("--mock", action="store_true", help="Run simulated offline mock war-room")
    args = parser.parse_args()

    asyncio.run(run_stage3_war_room(mock_mode=args.mock))
