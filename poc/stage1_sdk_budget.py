"""
PoC Stage 1: SDK Harness & Budget Hook Verification Script.

Verifies:
1. Operates without GEMINI_API_KEY (inheriting local desktop OAuth session).
2. Safety policies evaluate and deny dangerous operations.
3. @post_turn hook meters token spend into storage/state.db.
4. Deterministic early halt when task exceeds 5,000 token limit with Status: PAUSED_BUDGET.
"""
import asyncio
import os
import sys

from typing import Any, Dict

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.hooks import BudgetGuard
from src.agents.policies import Decision, PolicyEngine, create_agent_policies
from src.storage.db import (
    create_task,
    get_task,
    get_turn_history,
    init_db,
)

# Attempt import of native SDK
try:
    from google.antigravity import Agent, LocalAgentConfig, types
    from google.antigravity.hooks import hooks
    HAS_SDK = True
except ImportError:
    HAS_SDK = False

from src.agents.agy_runner import (
    AgyAuthenticationError,
    AgyNotInstalledError,
    AgyRunner,
    find_agy_binary,
)


async def run_stage1_poc(mode: str = "standalone") -> int:
    """
    Runs Stage 1 PoC.
    Modes:
      - 'agy': Live execution via agy CLI headless mode (-p) with cached subscription OAuth (0 API key)
      - 'live': Live execution via google.genai using GEMINI_API_KEY
      - 'standalone': Deterministic offline verification
    """
    mode_label = {
        "agy": "AGY HEADLESS (SUBSCRIPTION OAUTH)",
        "live": "DIRECT GEMINI API",
        "standalone": "STANDALONE TEST",
    }.get(mode, mode.upper())

    print("=" * 60)
    print(f"🚀 Running PoC Stage 1: Auth & Budget Guard ({mode_label})")
    print("=" * 60)

    # 1. Mode Validation & Verification
    agy_runner = None
    client = None

    if mode == "agy":
        agy_bin = find_agy_binary()
        if not agy_bin:
            print("❌ Antigravity CLI (`agy`) not found on host.")
            print("   Install via: curl -fsSL https://antigravity.google/cli/install.sh | bash")
            print("   Then authenticate once with your subscription: agy")
            return 1
        print(f"✅ Found agy binary at: {agy_bin}")
        print("🔑 Subscription OAuth Mode: Zero API keys required.")
        agy_runner = AgyRunner(agy_path=agy_bin)

    elif mode == "live":
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_key:
            print("❌ Live mode requested but GEMINI_API_KEY is not set.")
            print("   Set GEMINI_API_KEY=<key> or run with --agy for subscription OAuth.")
            return 1
        print("🔑 Direct API Key Mode active.")
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
        except Exception as e:
            print(f"❌ Failed to initialize google.genai Client: {e}")
            return 1
    else:
        print("ℹ️ Standalone verification mode (offline). Pass --agy for subscription OAuth.")

    # 2. Initialize SQLite State DB
    db_path = os.path.abspath("storage/state.db")
    init_db(db_path)
    print(f"✅ Storage database initialized at: {db_path}")

    # 3. Create task with 5,000 token budget
    task_id = "poc-stage1-verify"
    budget_limit = 5_000
    turn_limit = 5

    task = create_task(
        task_id=task_id,
        issue_id="issue-poc-1",
        slack_thread_ts="1729000000.000100",
        conversation_id="conv-poc-stage1",
        budget_limit=budget_limit,
        turn_limit=turn_limit,
        db_path=db_path,
    )
    print(f"✅ Task created in SQLite: {task_id} (Budget: {budget_limit:,} tokens, Turn Limit: {turn_limit})")

    # 4. Verify declarative safety policy boundaries
    print("\n🛡️ Testing declarative safety policies...")
    rules = create_agent_policies(workspace_path=os.path.abspath("workspaces/issue-poc-1"))
    engine = PolicyEngine(rules)

    assert engine.evaluate("run_command", {"CommandLine": "rm -rf /"}) == Decision.DENY
    print("   [Policy] Denied dangerous command: `rm -rf /` -> DENY")

    assert engine.evaluate("run_command", {"CommandLine": "git push origin main"}) == Decision.ASK_USER
    print("   [Policy] Gated remote push: `git push` -> ASK_USER")

    assert engine.evaluate("run_command", {"CommandLine": "pytest tests/"}) == Decision.ALLOW
    print("   [Policy] Allowed test execution: `pytest tests/` -> ALLOW")

    # 5. Execute turns with BudgetGuard
    print("\n⚡ Initializing BudgetGuard and executing task turns...")

    def on_exceeded(guard: BudgetGuard, record: Dict[str, Any]):
        reason = f"Budget exceeded: {record['cumulative_tokens']:,}/{record['budget_limit']:,} tokens"
        print(f"🛑 [BudgetGuard Hook Alert] {reason} -> Halted with PAUSED_BUDGET")

    guard = BudgetGuard(
        task_id=task_id,
        budget_limit=budget_limit,
        turn_limit=turn_limit,
        db_path=db_path,
        on_budget_exceeded=on_exceeded,
    )

    turn_prompts = [
        ("Turn 1 (Atlas Spec)", "Analyze this issue: User cannot login when session expires. Formulate a 2-step fix."),
        ("Turn 2 (Forge Edit)", "Provide a 10-line Python implementation fixing session expiration."),
        ("Turn 3 (Sentinel Test)", "Write a pytest test suite verifying the session expiration fix."),
    ]

    for turn_idx, (turn_name, prompt) in enumerate(turn_prompts, start=1):
        print(f"\n--- {turn_name} ---")

        if mode == "agy" and agy_runner:
            print(f"   [agy Headless] Executing via `agy -p` (subscription OAuth)...")
            try:
                turn_res = await agy_runner.execute_turn(prompt)
                usage_data = turn_res.usage_dict
                print(f"   Live Tokens Returned: {usage_data['total_token_count']:,} "
                      f"(Prompt: {usage_data['prompt_token_count']}, Candidates: {usage_data['candidates_token_count']})")
            except (AgyNotInstalledError, AgyAuthenticationError) as err:
                print(f"❌ agy error: {err}")
                return 1

        elif mode == "live" and client:
            print(f"   [Live Inference] Dispatching prompt to Gemini model...")
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            raw_usage = resp.usage_metadata
            usage_data = {
                "prompt_token_count": getattr(raw_usage, "prompt_token_count", 0),
                "candidates_token_count": getattr(raw_usage, "candidates_token_count", 0),
                "thoughts_token_count": getattr(raw_usage, "thoughts_token_count", 0) or 0,
                "total_token_count": getattr(raw_usage, "total_token_count", 0),
            }
            print(f"   Live Tokens Returned: {usage_data['total_token_count']:,} "
                  f"(Prompt: {usage_data['prompt_token_count']}, Candidates: {usage_data['candidates_token_count']})")
        else:
            # Standalone deterministic token fixture
            synthetic_tokens = [1900, 2000, 1800]
            t = synthetic_tokens[turn_idx - 1]
            usage_data = {"total_token_count": t, "prompt_token_count": t - 500, "candidates_token_count": 500}
            print(f"   Turn spend: {t:,} tokens")

        res = await guard.handle_post_turn(turn_idx, usage_data)
        print(f"   Cumulative spend: {res['cumulative_tokens']:,} / {res['budget_limit']:,} tokens")

        if guard.is_paused:
            print(f"   ⏸️ Turn loop paused by BudgetGuard at Turn {turn_idx}!")
            break

    # 6. Verify final state in SQLite
    final_task = get_task(task_id, db_path=db_path)
    assert final_task is not None
    print("\n" + "=" * 60)
    print("📊 Final Verification Summary:")
    print(f"   Task ID:           {final_task['task_id']}")
    print(f"   Status:            {final_task['status']}")
    print(f"   Tokens Consumed:   {final_task['current_tokens']:,} / {final_task['budget_limit']:,}")
    print(f"   Turns Completed:   {final_task['turn_count']}")
    print("=" * 60)

    # Assertions for acceptance criteria
    assert final_task["status"] == "PAUSED_BUDGET", f"Expected status PAUSED_BUDGET, got {final_task['status']}"
    assert final_task["current_tokens"] >= budget_limit, "Expected cumulative tokens to meet or exceed ceiling"

    print(f"🎉 PoC Stage 1 verification SUCCESSFUL ({mode_label}): Halts cleanly with Status: PAUSED_BUDGET\n")
    return 0


if __name__ == "__main__":
    selected_mode = "standalone"
    if "--agy" in sys.argv:
        selected_mode = "agy"
    elif "--live" in sys.argv:
        selected_mode = "live"

    code = asyncio.run(run_stage1_poc(mode=selected_mode))
    sys.exit(code)
