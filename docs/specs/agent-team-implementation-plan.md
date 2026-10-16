# Autonomous Multi-Agent Software Engineering Team (`google-antigravity` SDK)
## Project Research, Architecture Specification & Handover Build Plan

> **Document Purpose:** This document serves as the complete architectural specification, technical research summary, and step-by-step build plan to be handed directly to a **Google Antigravity Agent** (via Antigravity IDE / CLI) to bootstrap and implement an issue-triggered, budget-governed, Slack-integrated multi-agent software engineering system.
> **Core Constraint:** **ZERO paid per-token Gemini/Vertex API keys.** All cloud inference must execute via the local `google-antigravity` SDK runtime binary inheriting the host machine's active Google AI / Antigravity subscription OAuth session (with optional hybrid local offload via LiteRT / Ollama).

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

## 2. Core Technical Research & `google-antigravity` SDK Capabilities

### 2.1 Zero-API-Key Authentication (Subscription Inheritance)
- Installing `pip install google-antigravity` downloads the platform-specific compiled **Antigravity Agent Runtime binary**.
- Instantiating `Agent(config=LocalAgentConfig(...))` connects to this local runtime, automatically inheriting the active OAuth session from the host machine's Antigravity Desktop IDE or CLI login.
- **Result:** No `GEMINI_API_KEY` or Google Cloud billing account is required. Usage draws directly from the user's Google AI Pro / Ultra 5x / Ultra 20x subscription windows (5-hour rolling window + weekly ceiling).

### 2.2 Quota Optimization & Task Budgeting Mechanics
To prevent a single complex issue or infinite test-fix loop from draining the 5-hour subscription quota, three SDK controls are combined:
1. **Dynamic Thinking Depth (`GenerationConfig`):**
   - **Orchestrator:** `thinking_level="HIGH"` (invests tokens upfront in architectural planning and root-cause analysis).
   - **Coder Sub-Agent:** `thinking_level="MEDIUM"` or `"LOW"` (fast code generation and targeted diffs).
   - **QA Sub-Agent:** `thinking_level="MINIMAL"` (deterministic test execution and traceback extraction).
2. **Real-Time Token & Turn Metering (`usage_metadata`):**
   - Every turn response exposes `response.usage_metadata` (`prompt_token_count`, `candidates_token_count`, `cached_content_token_count`, `thoughts_token_count`, `total_token_count`).
   - A `@post_turn` Decide Hook accumulates token usage per `issue_id` in SQLite. When `spent_tokens >= task_budget_tokens` (default: `150,000` tokens or `15` turns), execution halts and requests human intervention in Slack.
3. **Optional Hybrid Local Offload (`LocalOpenAIAgentConfig` / LiteRT):**
   - If the host machine has sufficient VRAM/Unified Memory, sub-agents can optionally route routine coding/testing turns to local `gemma-4` via Ollama/vLLM/LiteRT while keeping the Orchestrator on the cloud subscription.

### 2.3 Built-In Tooling & Sub-Agent Delegation
- `LocalAgentConfig` ships with Antigravity's native toolset out of the box: `view_file`, `edit_file`, `run_command`, directory search, headless browser automation, and **native sub-agent spawning**.
- Sub-agents are defined cleanly via modular `SKILL.md` manifests (`./skills/coder/SKILL.md`, `./skills/qa/SKILL.md`) and scoped workspace paths.

### 2.4 Declarative Policy & Lifecycle Hooks
- **`google.antigravity.hooks.policy`:** Enforces hard boundaries before any tool executes:
  - `allow("view_file")`, `allow("edit_file")` (restricted to `./workspaces/issue-<id>/*`).
  - `deny("run_command", pattern=r"(rm\s+-rf|git\s+push\s+--force|sudo)")`.
  - `ask_user("run_command", pattern=r"(git\s+push|gh\s+pr\s+create|pip\s+install)", handler=slack_approval_handler)`.
- **Lifecycle Hooks (`@pre_tool_call`, `@post_tool_call`, `@post_turn`):**
  - Intercept tool executions to post real-time status updates to the Slack thread and check token budgets.

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
    "orchestrator": {
        "username": "Atlas (Lead Architect)",
        "icon_emoji": ":compass:"
    },
    "coder": {
        "username": "Forge (SWE Sub-Agent)",
        "icon_emoji": ":hammer_and_wrench:"
    },
    "qa": {
        "username": "Sentinel (QA & Verification)",
        "icon_emoji": ":microscope:"
    },
    "budget_guard": {
        "username": "Quota Warden",
        "icon_emoji": ":scales:"
    }
}

async def post_agent_message(slack_client, channel: str, thread_ts: str, role: str, text: str, blocks: list = None):
    persona = AGENT_PERSONAS.get(role, AGENT_PERSONAS["orchestrator"])
    await slack_client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=text,
        blocks=blocks,
        username=persona["username"],
        icon_emoji=persona["icon_emoji"]
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

### Stage 1: Project Scaffolding, Core SDK Harness & Budget Hook PoC
- **Objective:** Verify that `google-antigravity` initializes via subscription OAuth (no API key), executes a basic tool call, tracks `usage_metadata`, and halts cleanly when a mock token budget is exceeded.
- **Tasks:**
  1. Initialize `pyproject.toml`, virtual environment, and directory structure (`src/`, `skills/`, `storage/`, `tests/`).
  2. Implement `src/config.py` and `src/db.py` (SQLite state and budget ledger).
  3. Implement `src/agents/policies.py` (blocking destructive commands like `rm -rf` and `git push --force`) and `src/agents/hooks.py` (the `@post_turn` token budget counter).
  4. Write a CLI verification script (`src/stage1_poc.py`) that spawns an `Agent(LocalAgentConfig(...))`, runs a dummy file inspection task with a low token cap (e.g., `5,000` tokens), and verifies that the budget hook intercepts execution and logs token metrics accurately.
- **Acceptance Criteria:** `pytest tests/test_budget_hooks.py` passes; `python -m src.stage1_poc` runs without any `GEMINI_API_KEY` environment variable set.

### Stage 2: Git Worktree Isolation & Multi-Agent Split (Orchestrator, Coder, QA)
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

> *"Read `@ANTIGRAVITY_AGENT_TEAM_PLAN.md` in full. We are building this issue-triggered, budget-governed multi-agent software engineering system using the `google-antigravity` SDK on my active subscription (no API keys). Start by executing **Stage 1: Project Scaffolding, Core SDK Harness & Budget Hook PoC**. Create the directory structure, `pyproject.toml`, SQLite budget state tracker, declarative safety policies, and the Stage 1 verification script, then run the tests to verify everything works."*
