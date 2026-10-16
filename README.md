# Autonomous Multi-Agent Software Engineering Team

An event-driven, issue-triggered autonomous multi-agent engineering swarm powered by the `google-antigravity` SDK. The system decomposes, implements, verifies, and delivers software tasks inside isolated Git worktrees under strict token budget governance and Slack war-room supervision.

---

## ⚡ Key Highlights

- **Zero-API-Key Architecture**: Inherits active Google AI / Antigravity subscription OAuth credentials from the host machine runtime (`LocalAgentConfig`). No paid per-token API keys required.
- **Budget-Governed Lifecycle**: Enforces token and turn limits via runtime `@post_turn` lifecycle hooks to protect against rolling subscription quota exhaustion.
- **Git Worktree Isolation**: Every task executes in a dedicated worktree (`./workspaces/issue-<id>`), leaving the host repository working copy untouched.
- **Triad Swarm Collaboration**: Specialized personas collaborating with tiered thinking depth:
  - 🧭 **Atlas (Lead Orchestrator)**: `thinking_level="HIGH"` (Problem decomposition & spec formulation)
  - 🛠️ **Forge (Coder Sub-Agent)**: `thinking_level="MEDIUM"` (Source code authoring & git hygiene)
  - 🔬 **Sentinel (QA Sub-Agent)**: `thinking_level="MINIMAL"` (Test execution & verification reports)
  - ⚖️ **Quota Warden (Budget Guard)**: Real-time token metering and safety gates
- **Slack War-Room Warping**: Single-app Socket Mode connection streaming updates under distinct agent identities via `chat:write.customize`, with Block Kit interactive approval buttons.

---

## 📂 Repository Layout

```text
.
├── docs/
│   ├── adr/                      # Architecture Decision Records (0001–0004)
│   ├── research/                 # SWE benchmark analysis & multi-agent research
│   └── specs/                    # Implementation plan & phased PoC specifications
├── skills/
│   ├── coder/                    # Coder sub-agent instructions & file boundaries
│   └── qa/                       # QA sub-agent test execution rules & reporting
├── src/
│   ├── agents/                   # Orchestrator, lifecycle hooks & safety policies
│   ├── slack/                    # Bolt Socket Mode app, personas & Block Kit approvals
│   └── triggers/                 # GitHub issue poller & task dispatch queue
├── storage/
│   ├── sessions/                 # Antigravity native conversation trajectories
│   └── state.db                  # SQLite database tracking tasks, budgets, and threads
├── tests/                        # Unit and integration test suites
├── workspaces/                   # Ephemeral task worktrees (gitignored)
├── CONTEXT.md                    # Core project context and operating invariants
├── GLOSSARY.md                   # Canonical domain vocabulary
└── README.md
```

---

## 🧭 Documentation & Specifications

- [CONTEXT.md](CONTEXT.md): System purpose, inviolable constraints, and finalized requirements.
- [GLOSSARY.md](GLOSSARY.md): Ubiquitous domain terminology (Task, War-Room, Workspace, Safety Gate).
- [docs/specs/poc-implementation-plan.md](docs/specs/poc-implementation-plan.md): 4-stage incremental verification plan.
- [docs/adr/](docs/adr/):
  - [0001: Local Subscription OAuth Runtime](docs/adr/0001-use-local-subscription-oauth-runtime.md)
  - [0002: Ephemeral Git Worktrees for Isolation](docs/adr/0002-use-ephemeral-git-worktrees-for-isolation.md)
  - [0003: Slack Socket Mode with Persona Customization](docs/adr/0003-use-slack-socket-mode-with-persona-customization.md)
  - [0004: Post-Turn Budget Enforcement Hooks](docs/adr/0004-enforce-task-budgets-via-post-turn-hooks.md)
  - [0005: Dedicated Fixture Sample Repository for Testing](docs/adr/0005-use-dedicated-fixture-sample-repo-for-testing.md)

---

## 🛠️ Prerequisites

1. **Host Environment**: macOS or Linux with Python 3.11+.
2. **Antigravity CLI / IDE**: Authenticated into an active Google AI subscription on the host machine.
3. **GitHub CLI**: `gh auth status` configured with repository access.
4. **Slack App**: App configured with Socket Mode and `chat:write.customize` permissions.

---

## 🚦 Roadmap & Implementation Phases

- [x] **Repository Skeleton & Architecture Specs**
- [x] **Stage 1 PoC**: SDK OAuth inheritance & budget hook early halt
- [ ] **Stage 2 PoC**: Git worktree manager & 3-retry self-correction loop
- [ ] **Stage 3 PoC**: Slack Socket Mode war-room & Block Kit approval buttons
- [ ] **Stage 4 PoC**: End-to-end GitHub poller & Draft PR delivery
