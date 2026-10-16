# Autonomous Multi-Agent Software Engineering Team (Antigravity CLI Headless Runtime)
## Project Research, Architecture Specification & Handover Build Plan

> **Document Purpose:** This document serves as the complete architectural specification, technical research summary, and step-by-step build plan to be handed directly to a **Google Antigravity Agent** (via Antigravity IDE / CLI) to bootstrap and implement an issue-triggered, budget-governed, Slack-integrated multi-agent software engineering system.
> **Core Constraint:** **ZERO paid per-token Gemini/Vertex API keys.** All cloud inference must execute via the host machine's Antigravity CLI in Headless Mode (`agy -p "<prompt>" --output-format json`) inheriting the active Google AI / Antigravity subscription OAuth session (with `google-antigravity` as optional SDK abstraction).

---

## 1. Executive Summary & Project Vision

Instead of running an expensive 24/7 polling loop that exhausts subscription quotas, this system operates as an **event-driven, issue-triggered autonomous engineering team**.
1. **Trigger:** When the user creates an issue (GitHub Issues or Slack command) or applies the `agent-ready` label, the daemon wakes up from a zero-token idle state.
2. **Isolation:** The daemon provisions a dedicated **Git Worktree** (`./workspaces/issue-<id>`) and opens a dedicated **Slack War-Room Thread** (`thread_ts` mapped 1:1 with the Antigravity SDK `conversation_id`).
3. **Multi-Agent Execution:** A **Lead Orchestrator Agent** analyzes the issue, writes a technical spec, and delegates implementation and verification across two specialized sub-agents:
   - **Coder Sub-Agent:** Scoped to file editing and git operations inside the isolated worktree.
   - **QA / Tester Sub-Agent:** Scoped to running test suites (`pytest`/`vitest`), linters, and headless browser checks, returning structured Pydantic verification reports.
4. **Multi-Persona Slack Collaboration:** Using a single Slack App with `chat:write.customize`, Lifecycle Hooks (`@post_tool_call`, `@post_turn`) stream inter-agent updates into the Slack thread under distinct personas (`[Staff Orchestrator]`, `[Coder Agent]`, `[QA Automation]`).
5. **Strict Task Budgets & Safety Gates:** Every task is assigned a strict token and turn budget (tracked via SDK `usage_metadata`). If a task hits its budget ceiling or attempts a restricted command (e.g., `git push`, `rm -rf`), declarative Policy Hooks pause execution and send interactive Slack Block Kit buttons (`[Approve]`, `[Grant +50k Tokens]`, `[Abort]`) to the user.

---

## 2. Core Technical Architecture & Antigravity CLI Headless Runtime

### 2.1 Zero-API-Key Authentication via CLI Headless Mode (`agy -p`)
- **CLI Headless Invocation**: The daemon executes cloud model inference through the host machine's Antigravity CLI in headless print mode (`agy -p "<prompt>" --output-format json/stream-json`) via `src/agents/agy_runner.py` (`AgyRunner`).
- **Subscription Inheritance**: The `agy` CLI automatically inherits the active Google AI Pro / Ultra subscription OAuth session authenticated on the developer's workstation without requiring a `GEMINI_API_KEY` or Google Cloud billing credentials.
- **Robust Subprocess Decoupling**: Driving the CLI binary via subprocess eliminates dependence on undocumented or unreleased private Python SDK packages, providing stable execution across macOS and Linux hosts.

### 2.2 Quota Optimization & Task Budgeting Mechanics
To prevent a single complex issue or infinite test-fix loop from draining the rolling 5-hour subscription quota, three controls are combined:
1. **Dynamic Thinking Depth & Prompt Specialization:**
   - **Orchestrator (Atlas):** High thinking instructions (invests tokens upfront in architectural planning and root-cause analysis).
   - **Coder Sub-Agent (Forge):** Medium thinking instructions (fast code generation and targeted diffs).
   - **QA Sub-Agent (Sentinel):** Minimal thinking instructions (deterministic test execution and traceback extraction).
2. **Real-Time Token Telemetry Parsing (`AgyRunner`):**
   - Headless `agy -p --output-format json` emits structured telemetry containing `prompt_token_count`, `candidates_token_count`, `thoughts_token_count`, and `total_token_count`.
   - `AgyRunner` parses this payload into an `AgyTurnResult` dataclass and feeds it into the `@post_turn` `BudgetGuard`.
3. **Deterministic Token & Turn Halting (`BudgetGuard`):**
   - The `@post_turn` guard accumulates token spend per `task_id` in SQLite (`storage/state.db`).
   - When `current_tokens >= budget_limit` (default: `150,000` tokens or `15` turns), execution halts with status `PAUSED_BUDGET` and triggers human top-up approval in Slack.

### 2.3 Built-In Tooling & Sub-Agent Delegation
- Sub-agents are defined cleanly via modular `SKILL.md` manifests (`./skills/coder/SKILL.md`, `./skills/qa/SKILL.md`) and scoped workspace paths.
- Each sub-agent turn runs with scoped permissions inside the target Git worktree (`./workspaces/issue-<id>`).

### 2.4 Declarative Safety Policies & Lifecycle Hooks
- **`src/agents/policies.py` (`PolicyEngine`):** Enforces hard pre-execution boundaries before any tool runs:
  - `ALLOW`: Safe workspace file inspections and local test runs (`pytest`, `git status`).
  - `DENY`: Destructive shell commands (`rm -rf`, `git reset --hard`, `chmod 777`) and path traversal outside the worktree.
  - `ASK_USER`: Remote operations (`git push`, `gh pr create`) gated by human Slack Block Kit approval.
- **Lifecycle Hooks (`src/agents/hooks.py`):**
  - Intercept turn execution to update SQLite state, verify token budget ceilings, and raise pause gates.

---

## 3. Target System Architecture & Repository Structure

When building this project, the Antigravity Agent should scaffold the following directory layout:

```text
antigravity-agent-team/
├── README.md
├── pyproject.toml                  # Dependencies: google-antigravity, slack-bolt, fastapi, uvicorn, pydantic, aiosqlite
├── .env.example                    # SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL_ID, GITHUB_REPO, DEFAULT_TOKEN_BUDGET
├── slack_manifest.yaml             # Ready-to-import Slack App Manifest (Socket Mode + chat:write.customize)
├── src/
│   ├── __init__.py
│   ├── main.py                     # Async entrypoint starting Slack Socket Mode + Issue Poller / Webhook Server
│   ├── config.py                   # Pydantic Settings (budgets, model tiers, workspace paths)
│   ├── db.py                       # SQLite state store (issue_id <-> slack_thread_ts <-> conversation_id <-> token_spend)
│   ├── worktree.py                 # Git worktree manager (create, clean, branch isolation per issue)
│   ├── schemas.py                  # Pydantic structured outputs (ImplementationPlan, QATestReport, BudgetStatus)
│   ├── slack/
│   │   ├── __init__.py
│   │   ├── bot.py                  # Slack Bolt AsyncApp (Socket Mode listeners, thread replies, /agent commands)
│   │   ├── personas.py             # Dynamic persona sender (Orchestrator, Coder, QA usernames & emoji avatars)
│   │   └── approvals.py            # Block Kit interactive buttons for ask_user() policy gates & budget top-ups
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── agy_runner.py           # Subprocess runner driving agy -p headless inference and token telemetry
│   │   ├── orchestrator.py         # Lead Orchestrator Agent setup, session binding, and task loop
│   │   ├── hooks.py                # @post_tool_call (Slack telemetry) & @post_turn (Token budget enforcement)
│   │   └── policies.py             # Declarative safety rules (allow/deny/ask_user)
│   └── triggers/
│       ├── __init__.py
│       └── github_poller.py        # Polls `gh issue list --label agent-ready` or receives FastAPI webhooks
├── skills/
│   ├── coder/
│   │   └── SKILL.md                # Coder Sub-Agent instructions, git hygiene, and code style rules
│   └── qa/
│       └── SKILL.md                # QA Sub-Agent instructions, pytest/linter execution, and failure reporting
├── storage/
│   ├── sessions/                   # Antigravity SDK native trajectory & conversation persistence (`save_dir`)
│   └── state.db                    # SQLite database for budgets and thread mappings
└── tests/
    ├── test_budget_hooks.py
    ├── test_worktree.py
    └── test_orchestrator_mock.py
```

---

## 4. Core Component Specifications (Implementation Blueprints)

### 4.1 Data Models & Structured Schemas (`src/schemas.py`)
Use Pydantic models with `response.structured_output()` so handoffs between the Orchestrator, Coder, and QA agents are deterministic:

```python
from pydantic import BaseModel, Field
from typing import List, Literal, Optional


class SubTask(BaseModel):
    id: str
    title: str
    target_files: List[str]
    acceptance_criteria: str


class ImplementationPlan(BaseModel):
    issue_number: int
    summary: str
    branch_name: str
    subtasks: List[SubTask]
    estimated_token_budget: int = Field(default=150000)


class QATestReport(BaseModel):
    passed: bool
    command_executed: str
    total_tests: int
    failed_tests: int
    failure_summary: Optional[str] = None
    suggested_fix_files: List[str] = Field(default_factory=list)
```

### 4.2 Database & Budget State Tracking (`src/db.py`)
Track every active task in SQLite (`storage/state.db`) with the following schema:
- `issue_id` (TEXT PRIMARY KEY) — e.g., `"gh-42"` or `"slack-17100023"`
- `slack_thread_ts` (TEXT UNIQUE) — Slack thread timestamp
- `conversation_id` (TEXT UNIQUE) — Antigravity SDK `conversation_id`
- `worktree_path` (TEXT) — Path to `./workspaces/issue-42`
- `token_budget` (INTEGER) — Default `150000`
- `tokens_spent` (INTEGER) — Updated after every turn via `usage_metadata.total_token_count`
- `turns_spent` (INTEGER) — Updated after every turn (max default `15`)
- `status` (TEXT) — `PLANNING | CODING | TESTING | PAUSED_BUDGET | PAUSED_APPROVAL | COMPLETED | FAILED`

### 4.3 Slack Multi-Persona Bridge (`src/slack/personas.py`)
Using a single Slack Bot token with the `chat:write.customize` scope, all agents post into the same Slack thread while appearing as distinct teammates:

```python
AGENT_PERSONAS = {
    "orchestrator": {"username": "Atlas (Lead Architect)", "icon_emoji": ":compass:"},
    "coder": {"username": "Forge (SWE Sub-Agent)", "icon_emoji": ":hammer_and_wrench:"},
    "qa": {"username": "Sentinel (QA & Verification)", "icon_emoji": ":microscope:"},
    "budget_guard": {"username": "Quota Warden", "icon_emoji": ":scales:"},
}


async def post_agent_message(
    slack_client,
    channel: str,
    thread_ts: str,
    role: str,
    text: str,
    blocks: list = None,
):
    persona = AGENT_PERSONAS.get(role, AGENT_PERSONAS["orchestrator"])
    await slack_client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=text,
        blocks=blocks,
        username=persona["username"],
        icon_emoji=persona["icon_emoji"],
    )
```

### 4.4 Token Budget & Safety Hooks (`src/agents/hooks.py` & `policies.py`)
Every turn must pass through the budget guard:
1. **`@post_turn` Budget Hook:**
   - Reads `turn_tokens = response.usage_metadata.total_token_count`.
   - Increments `tokens_spent` and `turns_spent` in SQLite.
   - If `tokens_spent >= token_budget` or `turns_spent >= max_turns`:
     - Emits a Slack Block Kit message via `Quota Warden` showing token breakdown and two interactive buttons:
       - `action_id="grant_budget_50k"` (`+50,000 Tokens & +5 Turns`)
       - `action_id="abort_task"` (`Stop & Keep Branch for Human`)
     - Pauses the agent turn loop via an `asyncio.Event` until the user clicks a button in Slack.
2. **`@post_tool_call` Telemetry Hook:**
   - Filters noisy tool calls (ignores routine `view_file` reads; logs `edit_file`, `run_command`, and sub-agent delegations) and posts concise, readable updates to the Slack thread under the active sub-agent's persona.

---

## 5. Prerequisites Checklist (What the Human User Must Provide)

Before running Stage 3 and Stage 4, the user will supply:
1. **Authenticated Host Environment:**
   - Antigravity IDE or CLI logged into an active Google AI subscription on the host machine.
   - Python 3.11+ environment.
   - GitHub CLI (`gh auth status` authenticated) and `git` installed.
2. **Slack App Tokens (`.env`):**
   - `SLACK_BOT_TOKEN=xoxb-...`
   - `SLACK_APP_TOKEN=xapp-...` (with Socket Mode enabled)
   - `SLACK_CHANNEL_ID=C0XXXXXXX` (target channel for `#agent-ops`)
3. **Target Repository Path:**
   - `TARGET_REPO_PATH=/path/to/target/repo` (the codebase where git worktrees will be branched from).

---

## 6. Phased Implementation Plan (Instructions for the Antigravity Builder Agent)

> **Instructions for the Antigravity Agent executing this file:**
> Execute the build in the 4 sequential stages below. **Do not skip ahead.** Complete all verification tests for a stage before moving to the next.

### Stage 1: Project Scaffolding, Core CLI Harness & Budget Hook PoC ✅ [COMPLETED]
- **Objective:** Verify that `agy` CLI Headless Mode (`agy -p`) initializes via host subscription OAuth (zero API key), executes live model inference, parses real `usage_metadata` token telemetry, enforces declarative safety policies, and deterministically halts when task token budget limit is reached.
- **Tasks:**
  1. Initialize `pyproject.toml`, virtual environment, and directory structure (`src/`, `skills/`, `storage/`, `tests/`, `poc/`).
  2. Implement `src/storage/db.py` (SQLite state store and token turn ledger).
  3. Implement `src/agents/agy_runner.py` (`AgyRunner` subprocess driver with token telemetry extraction).
  4. Implement `src/agents/policies.py` (blocking destructive commands like `rm -rf` and `git push --force`) and `src/agents/hooks.py` (`BudgetGuard` `@post_turn` token counter and pause gate).
  5. Author CLI verification script (`poc/stage1_sdk_budget.py`) and Streamlit verification hub ([`poc/app.py`](file:///Users/yash/Documents/Repos/npc-inc/poc/app.py)) with 5 dedicated interactive testbenches.
- **Acceptance Criteria:** `pytest tests/` passes 100% (9/9 tests); live inference executes via `agy -p` without any `GEMINI_API_KEY` set; task halts with `Status: PAUSED_BUDGET` upon reaching ceiling.

### Stage 2: Git Worktree Isolation & Multi-Agent Split (Orchestrator, Coder, QA) ✅ [COMPLETED]
- **Objective:** Enable the Orchestrator to provision an isolated git worktree per issue and coordinate the Coder and QA sub-agents using structured outputs (`ImplementationPlan` and `QATestReport`).
- **Tasks:**
  1. Implement `src/worktree.py` to create (`git worktree add -b fix/issue-<id> ./workspaces/issue-<id>`) and clean up isolated workspaces.
  2. Author `skills/coder/SKILL.md` and `skills/qa/SKILL.md` with clear tool boundaries and instructions.
  3. Implement `src/agents/orchestrator.py` with tiered `GenerationConfig` thinking levels (`HIGH` for Orchestrator planning, `LOW`/`MEDIUM` for Coder, `MINIMAL` for QA).
  4. Build the autonomous self-correction loop:
     - Orchestrator generates `ImplementationPlan`.
     - Coder implements changes in the worktree.
     - QA runs tests and returns `QATestReport`.
     - If `QATestReport.passed == False`, Orchestrator routes the failure traceback back to Coder (up to 3 retries or until the task token budget is hit).
- **Acceptance Criteria:** Running the orchestrator against a local test issue creates a clean git worktree, edits the target file, runs `pytest`, self-corrects a deliberate failing test, and saves the trajectory in `./storage/sessions`.

### Stage 3: Slack Socket Mode War-Room, Multi-Persona Stream & Interactive Approvals
- **Objective:** Connect the multi-agent loop to Slack so agents communicate visibly in a thread and ask the user for approval/budget extensions via interactive buttons.
- **Tasks:**
  1. Generate `slack_manifest.yaml` so the user can one-click create the Slack App with Socket Mode, `chat:write`, `chat:write.customize`, `channels:history`, and Interactivity enabled.
  2. Implement `src/slack/personas.py`, `src/slack/bot.py`, and `src/slack/approvals.py`.
  3. Bind Slack `thread_ts` to Antigravity `conversation_id` in `src/db.py` so any human reply inside the Slack thread resumes the exact agent conversation session and steers the Orchestrator.
  4. Wire the `ask_user()` policy handler and `@post_turn` budget exhaustion hook to Slack Block Kit buttons (`[Approve Command]`, `[Reject]`, `[+50k Token Budget]`, `[Abort Task]`).
- **Acceptance Criteria:** Posting `@AgentTeam solve: add healthcheck endpoint` in Slack opens a thread, streams updates from `Atlas (Lead Architect)`, `Forge (SWE Sub-Agent)`, and `Sentinel (QA & Verification)`, pauses on `git push` with an interactive Slack button, and resumes when clicked.

### Stage 4: GitHub Issue Trigger & Automated Draft PR Delivery
- **Objective:** Close the loop so creating or labeling a GitHub issue automatically triggers the team and delivers a verified Draft Pull Request.
- **Tasks:**
  1. Implement `src/triggers/github_poller.py` (using `gh issue list --label agent-ready --json number,title,body` on a lightweight configurable interval, plus an optional FastAPI `/webhook/github` endpoint).
  2. When a new `agent-ready` issue is detected:
     - Swap label from `agent-ready` to `agent-in-progress` via `gh issue edit`.
     - Announce the issue in the Slack channel and create the War-Room thread.
     - Execute the budgeted Orchestrator -> Coder -> QA loop inside `./workspaces/issue-<number>`.
     - Once QA passes and the user approves push in Slack, push the branch, run `gh pr create --draft`, swap the label to `agent-review`, and post the PR link + total token budget summary in Slack.
- **Acceptance Criteria:** End-to-end flow from labeling a GitHub issue `agent-ready` to receiving a tested Draft PR and Slack thread summary works reliably within the configured token budget.

---

## 7. Ready-to-Use Slack App Manifest (`slack_manifest.yaml`)

Save this block as `slack_manifest.yaml` during Stage 3 and import it at `https://api.slack.com/apps`:

```yaml
display_information:
  name: Antigravity Dev Team
  description: Autonomous Issue-Triggered SWE, QA & Orchestrator Agents
  background_color: "#111827"
features:
  bot_user:
    display_name: Antigravity Orchestrator
    always_online: true
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - channels:history
      - channels:read
      - chat:write
      - chat:write.customize
      - commands
      - groups:history
      - reactions:read
      - reactions:write
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.channels
      - message.groups
  interactivity:
    is_enabled: true
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

---

## 8. Kickoff Prompt for Your Antigravity Agent

Once you drop this file (`ANTIGRAVITY_AGENT_TEAM_PLAN.md`) into your new project directory, open your Antigravity IDE or CLI and run this prompt:

> *"Read `@ANTIGRAVITY_AGENT_TEAM_PLAN.md` in full. We are building this issue-triggered, budget-governed multi-agent software engineering system using the Antigravity CLI in headless mode (`agy -p`) on my active subscription (no API keys). Start by executing **Stage 1: Project Scaffolding, Core CLI Harness & Budget Hook PoC**. Create the directory structure, `pyproject.toml`, SQLite budget state tracker, declarative safety policies, and the Stage 1 verification script, then run the tests to verify everything works."*
