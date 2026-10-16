# 5. Use Dedicated Fixture Sample Repository for Agent Swarm Testing

## Context
Validating multi-agent code modification, test suites, and git worktree isolation requires a genuine Git repository. Running agents against production codebases or the agent repo itself risks unintended code mutations, dirty Git states, and unreproducible testing environments.

## Decision
We create a dedicated, isolated fixture Git repository (`tests/fixtures/sample_repo`) containing a minimal Python package with a pre-configured failing test to serve as the standardized target for all Stage 2+ PoC and integration tests.

## Consequences
- **Positive**: Controlled, deterministic fail-to-pass verification without polluting real repos or the host agent working copy.
- **Negative**: Fixture setup must be initialized as a nested or scripted testbed before running worktree tests.
