# 2. Use Ephemeral Git Worktrees for Task Isolation

## Context
Multiple concurrent tasks or multi-turn agent edits directly on the parent repository risk corrupting working trees, polluting unstaged git state, and producing branch merge collisions. Heavyweight Docker containers introduce significant startup latency and filesystem mounting overhead.

## Decision
We provision an isolated ephemeral Git worktree (`./workspaces/issue-<id>`) branched from the target repository for each task lifecycle and prune it upon completion or failure.

## Consequences
- **Positive**: Instantaneous workspace creation, native filesystem performance, and total isolation of the parent working directory.
- **Negative**: Sub-agents share the host machine's runtime dependencies and environment tooling.
