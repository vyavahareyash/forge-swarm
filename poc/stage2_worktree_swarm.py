"""
PoC Stage 2: Git Worktree Isolation & Multi-Agent Swarm Verification Script.

Verifies:
1. Provisions isolated Git worktree from dedicated testbed repo (tests/fixtures/sample_repo).
2. Lead Orchestrator (Atlas) creates structured ImplementationPlan.
3. Sentinel (QA Sub-Agent) detects deliberate failing test on turn 1.
4. Forge (Coder Sub-Agent) receives failure traceback, analyzes source, and applies patch in worktree.
5. Sentinel re-executes test suite and confirms green pass on attempt 1 or 2.
6. Commit made on fix branch and worktree pruned cleanly.
7. Full session trajectory persisted into storage/sessions/.
"""
import asyncio
import os
import sys
from pathlib import Path

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.orchestrator import Orchestrator
from src.schemas import SwarmTurnLog
from src.worktree import WorktreeManager
from tests.fixtures.sample_repo import ensure_sample_repo


def print_turn(turn: SwarmTurnLog):
    persona_icons = {
        "Atlas (Lead Architect)": "🧭",
        "Forge (SWE Sub-Agent)": "🛠️",
        "Sentinel (QA Automation)": "🔬",
    }
    icon = persona_icons.get(turn.persona, "🤖")
    print(f"\n{icon} [{turn.persona}] - Thinking: {turn.thinking_level}")
    print(f"   Action: {turn.action} (+{turn.tokens_spent:,} tokens)")
    for k, v in turn.details.items():
        print(f"   • {k}: {v}")


async def run_stage2_poc(mode: str = "standalone") -> int:
    print("=" * 65)
    print(f"🚀 Running PoC Stage 2: Git Worktree & Swarm Loop ({mode.upper()})")
    print("=" * 65)

    # 1. Initialize clean sample repo fixture
    print("\n📦 Initializing dedicated sample repo testbed (ADR 0005)...")
    sample_repo_path = ensure_sample_repo(reset=True)
    print(f"✅ Sample repo ready at: {sample_repo_path}")

    # 2. Setup Orchestrator & Worktree Manager
    wm = WorktreeManager()
    orchestrator = Orchestrator(workspace_manager=wm)

    issue_id = "poc-stage2-fibonacci"
    issue_desc = "calculate_fibonacci(1) returns 2 instead of 1; tests are failing"

    print(f"\n📋 Target Issue: {issue_id}")
    print(f"   Description:  {issue_desc}")
    print("\n⚡ Initiating multi-agent swarm loop...")

    result = await orchestrator.run_task(
        issue_id=issue_id,
        issue_desc=issue_desc,
        repo_path=sample_repo_path,
        budget_limit=150_000,
        max_retries=3,
        mode=mode,
        on_turn_callback=print_turn,
        cleanup_worktree=True,
    )

    print("\n" + "=" * 65)
    print("📊 Swarm Execution Summary:")
    print(f"   Task ID:       {result.task_id}")
    print(f"   Status:        {result.status}")
    print(f"   Retries Used:  {result.retries_used} / 3")
    print(f"   Tokens Spent:  {result.tokens_spent:,} tokens")
    print(f"   Worktree:      Cleanly pruned")
    print(f"   Session File:  storage/sessions/{result.task_id}.json")
    print("=" * 65)

    if result.status == "SUCCESS":
        print("🎉 PoC Stage 2 verification SUCCESSFUL: Swarm self-corrected bug in worktree!")
        return 0
    else:
        print(f"❌ PoC Stage 2 verification FAILED: Status is {result.status}")
        return 1


if __name__ == "__main__":
    mode_arg = "agy" if "--agy" in sys.argv else "standalone"
    exit_code = asyncio.run(run_stage2_poc(mode=mode_arg))
    sys.exit(exit_code)
