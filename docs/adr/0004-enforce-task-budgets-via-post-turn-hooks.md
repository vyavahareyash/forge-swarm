# 4. Enforce Task Budgets via Post-Turn Lifecycle Hooks

## Context
LLMs frequently hallucinate or ignore natural language system prompt reminders regarding token constraints, leading to runaway multi-hour retry cascades and quota exhaustion.

## Decision
We enforce task token ceilings and turn limits deterministically at the runtime harness level via an `@post_turn` lifecycle hook. The hook extracts SDK `usage_metadata`, persists consumption to SQLite, and halts agent execution via an `asyncio.Event` until human approval is received via Slack buttons.

## Consequences
- **Positive**: Ironclad guarantee that runaway loops cannot exhaust host subscription quotas without explicit human approval.
- **Negative**: Long-running complex tasks may halt prematurely if initial budget estimations are calibrated too conservatively.
