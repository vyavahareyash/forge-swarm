# 1. Use Local Subscription OAuth Runtime Over Per-Token API Keys

## Context
Autonomous engineering swarms running long-horizon SWE tasks consume hundreds of turns and tens of millions of tokens, leading to substantial per-token cloud API bills or runaway invoice risks.

## Decision
We execute cloud model inference through the local Antigravity CLI runtime in Headless mode (`agy -p "<prompt>" --output-format json/stream-json`) and `google-antigravity` runtime, inheriting the active OAuth session from the host machine's Antigravity subscription without requiring a `GEMINI_API_KEY`.

## Consequences
- **Positive**: Zero marginal dollar cost per token, eliminating cloud billing accounts and secret API keys.
- **Negative**: Imposes a strict rolling 5-hour quota ceiling, mandating deterministic token budgets and early task halting.
