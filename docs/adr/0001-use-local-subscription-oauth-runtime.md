# 1. Use Local Subscription OAuth Runtime Over Per-Token API Keys

## Context
Autonomous engineering swarms running long-horizon SWE tasks consume hundreds of turns and tens of millions of tokens, leading to substantial per-token cloud API bills or runaway invoice risks.

## Decision
We execute all cloud model inference exclusively through the local `google-antigravity` SDK runtime binary (`LocalAgentConfig`), inheriting the active OAuth session from the host machine's Antigravity subscription.

## Consequences
- **Positive**: Zero marginal dollar cost per token, eliminating cloud billing accounts and secret API keys.
- **Negative**: Imposes a strict rolling 5-hour quota ceiling, mandating deterministic token budgets and early task halting.
