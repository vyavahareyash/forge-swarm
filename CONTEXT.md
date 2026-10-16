# Project Context: Autonomous Multi-Agent SWE Team

## 1. System Purpose & Vision
An event-driven, issue-triggered autonomous multi-agent engineering swarm designed to resolve GitHub issues and Slack requests without human intervention, while operating under strict token quotas and zero-API-key runtime constraints.

## 2. Inviolable Constraints
- **Zero Paid Token Billing**: No `GEMINI_API_KEY` or Vertex AI credentials. All cloud inference executes through `google-antigravity` local runtime binary inheriting the active host Google AI subscription OAuth session.
- **Quota Governance**: To prevent rolling 5-hour subscription exhaustion, tasks enforce a strict default budget (`150,000` tokens / `15` turns). Reaching the ceiling halts the swarm and requests human top-up via Slack.
- **Filesystem Isolation**: The primary repository working directory is never touched directly during execution. Every issue runs inside an isolated Git worktree (`./workspaces/issue-<id>`).

## 3. Core Swarm Roles & Personas
- **Atlas (Lead Orchestrator)**: `thinking_level="HIGH"`. Analyzes issues, formulates `ImplementationPlan`, coordinates sub-agents, and manages self-correction cycles.
- **Forge (Coder Sub-Agent)**: `thinking_level="MEDIUM"`. Scoped strictly to file editing and git hygiene inside the task worktree.
- **Sentinel (QA Sub-Agent)**: `thinking_level="MINIMAL"`. Executes test runners (`pytest`, `vitest`), linters, and emits structured `QATestReport`.
- **Quota Warden (Budget Guard)**: Monitors token burn and raises Block Kit interactive approval gates.

## 4. Interaction & Communication
- **Slack Socket Mode**: Zero incoming open ports required. Connects via websocket using `xapp` and `xoxb` tokens.
- **1:1 War-Room Mapping**: Each task maps `issue_id` ↔ `slack_thread_ts` ↔ SDK `conversation_id`.
- **Dynamic Personas**: Single Slack bot posts as Atlas, Forge, Sentinel, or Quota Warden using `chat:write.customize`.
- **Interactive Policy Gates**: Restricted commands (`git push`, destructive operations) and budget extensions pause the turn loop until an engineer clicks an approval button in the Slack thread.

## 5. Storage & State Model
- `storage/state.db`: SQLite database tracking issue metadata, thread timestamps, conversation IDs, token spend, and execution state.
- `storage/sessions/`: Native SDK trajectory and session storage (`save_dir`) for turn resumption.
