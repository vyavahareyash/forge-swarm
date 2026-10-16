# 6. Three-Tier Production Observability Architecture

## Context
Production multi-agent autonomous software engineering requires deep visibility into:
1. Agent actions (file edits, bash execution, git commits).
2. Agent history (token spend per turn, prompts, thoughts, completions).
3. Agent-to-agent communication (Orchestrator to Coder specs, QA to Coder failure tracebacks).

External SaaS APM tools (e.g. LangSmith) introduce 3rd-party cloud dependencies, potential code-diff IP exposure, and external latency. 

## Decision
We adopt a 3-tier self-contained observability architecture:
1. **Tier 1 (Real-Time Team UI):** Slack War-Room Thread (Stage 3) via `chat:write.customize` persona streams (`Atlas`, `Forge`, `Sentinel`, `Quota Warden`) with interactive Block Kit approval gates.
2. **Tier 2 (Deep-Dive Dashboard):** Embedded FastAPI WebSocket/SSE local console reading SQLite WAL for interactive sequence diagrams and diff inspection without external SaaS.
3. **Tier 3 (Immutable Audit Ledger):** Local SQLite `events` / `turns` tables (`storage/state.db`) + JSON session files (`storage/sessions/<task_id>.json`) for offline replay.

## Consequences
- **Positive:** Zero external API keys or cloud costs, zero IP leakage to third parties, native integration with existing engineer workflows in Slack, offline reproducibility.
- **Negative:** Custom web dashboard components must be maintained within the daemon codebase rather than using off-the-shelf SaaS.
