"""
Interactive Live Slack Intake & Requirements Grilling CLI Tool.

Conducts real intake against GitHub and Slack without simulated responses:
- Uses actual `gh` CLI to create and update draft issues on GitHub.
- Uses actual Slack WebClient to spawn live War-Room threads and post Mira turns.
- Interactively prompts human engineer via terminal CLI (`input()`).
- Renders live Block Kit acceptance card with [Approve & Start Implementation].
- Excluded from pytest testpaths.
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
from slack_sdk.web.async_client import AsyncWebClient

from src.agents.mira import MiraIntakeAgent
from src.schemas import LivingSpec
from src.slack.approvals import build_spec_acceptance_blocks
from src.slack.mutations import mutate_blocks_for_resolution
from src.slack.personas import post_agent_message
from src.storage.db import (
    create_task,
    get_living_spec,
    get_task,
    init_db,
    save_living_spec,
    update_task_status,
    update_task_thread_ts,
)
from src.triggers.github_poller import GitHubPoller


async def main():
    load_dotenv()
    db_path = os.path.abspath("storage/state.db")
    init_db(db_path)

    bot_token = os.getenv("SLACK_BOT_TOKEN")
    channel_id = os.getenv("SLACK_CHANNEL_ID")
    repo = os.getenv("GITHUB_REPO")

    if not (bot_token and channel_id):
        print("❌ Error: Missing SLACK_BOT_TOKEN or SLACK_CHANNEL_ID in .env")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Live Slack Intake & Grilling CLI")
    parser.add_argument("--title", help="Task title", default=None)
    parser.add_argument("--prompt", help="Initial requirement prompt", default=None)
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("🤖 Live Intake & Requirements Grilling CLI (Mira)")
    print("=" * 60)

    # 1. Gather title & description interactively if not provided
    title = args.title
    if not title:
        title = input("\n📝 Enter Task Title: ").strip()
        while not title:
            title = input("Title cannot be empty. Enter Task Title: ").strip()

    prompt = args.prompt
    if not prompt:
        print(
            "\n📋 Enter Task Description / Requirements (Press Enter twice to finish):"
        )
        lines = []
        while True:
            line = input()
            if not line and lines:
                break
            if line:
                lines.append(line)
        prompt = "\n".join(lines).strip()
        if not prompt:
            prompt = title

    # 2. Use GitHubPoller powered by agy CLI (or direct gh CLI fallback)
    poller = GitHubPoller(repo=repo, db_path=db_path, use_agy=True)
    client = AsyncWebClient(token=bot_token)
    mira = MiraIntakeAgent(db_path=db_path)

    issue_number = int(datetime.now(UTC).timestamp()) % 1000 + 100
    issue_url = None
    if repo:
        import subprocess

        backend = (
            "agy CLI (executing terminal commands)"
            if poller._command_runner != subprocess.run
            else "gh CLI"
        )
        print(f"\n🐙 Creating draft issue on GitHub ({repo}) via {backend}...")
        try:
            draft_issue = poller.create_draft_issue(
                title=title,
                body=prompt,
                labels=["triage:agent"],
            )
            issue_number = draft_issue.number
            issue_url = draft_issue.url
            print(
                f"   ✅ Created GitHub Draft Issue #{issue_number}: {issue_url or ''}"
            )
        except Exception as e:
            print(f"   ⚠️ Could not create GitHub issue ({e}). Using local ID.")

    issue_id = f"issue-{issue_number}"
    task_id = f"task-{issue_id}-{int(datetime.now(UTC).timestamp())}"

    # 3. Initialize Task and Living Spec in SQLite
    create_task(task_id=task_id, issue_id=issue_id, status="INTAKE", db_path=db_path)
    now_iso = datetime.now(UTC).isoformat()
    save_living_spec(
        LivingSpec(
            task_id=task_id,
            version=1,
            requirements=[prompt],
            architecture_decisions=[],
            review_state={},
            open_questions=[],
            updated_at=now_iso,
        ),
        db_path=db_path,
    )

    # 4. Post War-Room root message in Slack
    print(f"\n📡 Spawning War-Room thread in Slack channel {channel_id}...")
    root_msg = await client.chat_postMessage(
        channel=channel_id,
        text=f"🎯 *New Task Intake:* `{issue_id}` - *{title}* (triggered via Live CLI)",
    )
    thread_ts = root_msg["ts"]
    update_task_thread_ts(task_id, thread_ts, db_path=db_path)
    print(f"   ✅ Live thread online at ts: {thread_ts}")

    # 5. Generate and post Mira Round 1 grilling questions
    print("\n🎯 Generating Mira Round 1 grilling questions...")
    mira_text, _ = await mira.generate_initial_grilling(title=title, description=prompt)
    await post_agent_message(
        client=client,
        channel=channel_id,
        role="mira",
        text=mira_text,
        thread_ts=thread_ts,
    )

    print("\n" + "-" * 60)
    print(f"🎯 [Mira (Product Specifier)]:\n\n{mira_text}")
    print("-" * 60)

    # 6. Interactive grilling loop
    round_count = 1
    while True:
        user_input = input(
            "\n🧑‍💻 Your response / direction (or type 'ready' to finalize spec): "
        ).strip()
        while not user_input:
            user_input = input("Response cannot be empty: ").strip()

        # Post human response to the Slack thread
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f"🧑‍💻 *Engineer (CLI):* {user_input}",
        )

        current_spec = get_living_spec(task_id, db_path=db_path) or LivingSpec(
            task_id=task_id,
            version=1,
            requirements=[prompt],
            updated_at=now_iso,
        )

        mira_reply, is_complete, spec_data = await mira.process_reply(
            task_id=task_id,
            issue_id=issue_id,
            title=title,
            user_reply=user_input,
            current_spec=current_spec,
        )

        round_count += 1

        if is_complete and spec_data:
            update_task_status(task_id, "GRILLING", db_path=db_path)
            print(
                "\n🎯 [Mira]: Requirements aligned! Posting Block Kit acceptance card to Slack..."
            )

            blocks = build_spec_acceptance_blocks(
                task_id=task_id,
                issue_id=issue_id,
                title=title,
                summary=spec_data.summary,
                acceptance_criteria=spec_data.acceptance_criteria,
                scope_and_boundaries=spec_data.scope_and_boundaries,
                out_of_scope=spec_data.out_of_scope,
            )

            card_post = await post_agent_message(
                client=client,
                channel=channel_id,
                role="mira",
                text=mira_reply,
                thread_ts=thread_ts,
                blocks=blocks,
            )
            card_ts = card_post["ts"]

            print("\n" + "=" * 60)
            print("📋 AGREED SPECIFICATION & ACCEPTANCE CRITERIA")
            print("=" * 60)
            print(f"\nSummary:\n{spec_data.summary}\n")
            print("Acceptance Criteria:")
            for ac in spec_data.acceptance_criteria:
                print(f"  • [ ] {ac}")
            print("\nScope & Boundaries:")
            for sb in spec_data.scope_and_boundaries:
                print(f"  • {sb}")
            print("\nOut of Scope:")
            for oos in spec_data.out_of_scope:
                print(f"  • {oos}")
            print("=" * 60)

            print(
                "\n⚡ Live Block Kit card is now posted in Slack with [Approve & Start Implementation]."
            )
            approve_choice = (
                input("\nApprove and sync specification to GitHub now? [Y/n]: ")
                .strip()
                .lower()
            )

            if approve_choice in ("", "y", "yes"):
                print("\n🚀 Executing approval & in-place card mutation...")

                # In-place Block Kit mutation
                mutated = mutate_blocks_for_resolution(
                    blocks=blocks,
                    action_id="approve_spec",
                    user_id="U_CLI_ENGINEER",
                    resolution_text="Spec & Acceptance Criteria Approved (Live CLI)",
                )
                await client.chat_update(
                    channel=channel_id,
                    ts=card_ts,
                    blocks=mutated,
                    text="✅ Spec & Acceptance Criteria Approved (Live CLI)",
                )

                # Format clean markdown and sync to GitHub via actual `gh` CLI
                clean_md = mira.format_github_issue_body(
                    spec_data, war_room_channel="agent-ops", thread_ts=thread_ts
                )

                if repo:
                    try:
                        clean_title = title.replace("[Draft]", "").strip()
                        poller.update_issue_spec(
                            issue_number, title=clean_title, body=clean_md
                        )
                        poller.mark_ready_for_dev(issue_number)
                        print(
                            f"   ✅ GitHub Issue #{issue_number} updated with clean markdown & status:ready-for-dev."
                        )
                    except Exception as e:
                        print(f"   ⚠️ gh CLI update failed: {e}")

                update_task_status(task_id, "READY_FOR_DEV", db_path=db_path)

                await post_agent_message(
                    client=client,
                    channel=channel_id,
                    role="mira",
                    text=(
                        f"✅ *Requirements Finalized & Approved* via Live CLI!\n"
                        f"• GitHub issue `{issue_id}` updated with clean markdown spec.\n"
                        f"• Transitioned label to `status:ready-for-dev`.\n"
                        f"• Ready for Arjun (Lead Architect) design phase."
                    ),
                    thread_ts=thread_ts,
                )

                task = get_task(task_id, db_path=db_path)
                print(f"\n🎉 Task Status in SQLite: {task['status']}")
                print(f"🔗 Slack War-Room Thread: {thread_ts}")
                print("All steps completed successfully!\n")
            else:
                print(
                    "Intake kept open. You can click [Approve & Start Implementation] in Slack."
                )
            break
        else:
            # Post next grilling round
            await post_agent_message(
                client=client,
                channel=channel_id,
                role="mira",
                text=mira_reply,
                thread_ts=thread_ts,
            )
            print("\n" + "-" * 60)
            print(f"🎯 [Mira (Product Specifier)]:\n\n{mira_reply}")
            print("-" * 60)


if __name__ == "__main__":
    asyncio.run(main())
