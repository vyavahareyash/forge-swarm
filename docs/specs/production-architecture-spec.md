## Problem Statement

Autonomous engineering systems frequently fail when moving from proof-of-concept to production due to lack of human-in-the-loop requirement alignment, unconstrained token expenditures, fragile single-agent code-and-pray loops, lack of persistent conversational context across daemon restarts, workspace pollution on host repositories, and unresponsive or zombie UI controls in chat collaboration channels. As a Chief Technology Lead (CTL) or engineering manager, supervising autonomous agents requires clear visibility, structured architectural sign-offs, rigorous multi-agent verification before code reaches main, and predictable quota controls without managing cloud API keys or dealing with noisy, robotic chat bots.

## Solution

A resilient, production-ready autonomous multi-agent software engineering team operated via Slack and GitHub. The system introduces persona-driven lifecycle phases (Mira for requirements grilling and spec synthesis, Arjun for architectural planning and test strategy, Kabir for test-driven development, Chetna for automated regression suites, Vivek for dual-axis code reviews, and Dhruv for real-time quota guard enforcement). All work executes inside isolated ephemeral git workspaces, with turn-level token and budget governance backed by local subscription credentials. Chat interactions occur within dedicated Slack War-Rooms featuring in-place mutating interactive Block Kit cards, thread rehydration from persistent storage, and dual-channel merge flows with verified reproducibility proofs.

## User Stories

1. As a CTL, I want to trigger a task by labeling a GitHub issue with `triage:agent` or typing `/agent solve <request>` in Slack, so that engineering work begins automatically without manual dispatch.
2. As a CTL, I want an automatic War-Room Slack thread created and mapped 1:1 to the task and GitHub issue, so that all communications and decisions remain organized in a single discussion context.
3. As a CTL, I want Mira to interview me with focused probing questions about edge cases and acceptance criteria, so that ambiguities are eliminated before any code is written.
4. As a CTL, I want Mira to summarize finalized requirements into a clean Block Kit acceptance card with an approval button, so that I can formally sign off on the task scope.
5. As a CTL, I want clicking the approval button to update the GitHub issue with clean markdown and transition its status to ready-for-dev, so that the issue tracker reflects the authoritative spec.
6. As a CTL, I want Arjun to inspect the codebase structure and present an architectural blueprint with planned TDD tests in the War-Room, so that I can verify the proposed design before implementation starts.
7. As a CTL, I want to review and approve Arjun's architectural blueprint via an interactive Slack button, so that design alignment is established up front.
8. As a CTL, I want to reply with steering guidance if an architectural plan needs adjustment, so that the lead architect refines the approach without starting from scratch.
9. As a Lead Orchestrator, I want an ephemeral git workspace provisioned for each task, so that the parent production repository is never mutated directly during development.
10. As a Coder Sub-Agent (Kabir), I want to execute a Red-Green-Refactor test-driven development cycle, so that new functionality is guaranteed to have verified test coverage.
11. As a Coder Sub-Agent, I want failing tests written and confirmed before writing implementation code, so that behavior verification is never skipped.
12. As a QA Sub-Agent (Chetna), I want to execute full regression test suites across the repository in the workspace, so that changes do not introduce unintended side effects in existing features.
13. As a Staff Reviewer (Vivek), I want to perform a dual-axis code review evaluating both repo coding standards and adherence to the agreed spec, so that low-quality code and scope creep are caught before commit.
14. As a Coder Sub-Agent, I want actionable defect feedback from the Staff Reviewer with automatic retry attempts up to a fixed limit, so that routine defects are resolved autonomously.
15. As a Staff Reviewer, I want to escalate unresolved defects to the CTL in the War-Room if automated retries fail, so that human judgment can resolve deadlocks.
16. As a Quota Warden (Dhruv), I want each turn metered against a defined task budget of tokens and turns, so that runaway agent loops cannot exhaust subscription quotas.
17. As a CTL, I want a safety gate to pause task execution and prompt for budget extension when token or turn limits are approached, so that costs remain under strict control.
18. As a CTL, I want interactive buttons in Slack to grant incremental budget extensions, so that valid long-running tasks can complete without losing state.
19. As a CTL, I want all agent communications to sound like pragmatic engineering colleagues with concise 2–4 sentence responses, so that chat threads remain readable and professional.
20. As a CTL, I want to mention specific personas like `@Kabir` or `@Arjun` in the War-Room thread, so that the tagged persona responds to specific queries regardless of the active phase.
21. As a CTL, I want interactive Block Kit buttons to mutate in place into timestamped audit text upon click, so that zombie buttons and accidental double-submissions are eliminated.
22. As a Lead Orchestrator, I want task session state and trajectory histories persisted in SQLite and disk storage, so that context can be rehydrated seamlessly after daemon restarts or upon historical replies.
23. As a Lead Orchestrator, I want a "Living Spec" injected into runtime system context during rehydration, so that core acceptance criteria are never lost to context truncation.
24. As a CTL, I want the team to assemble a GitHub Pull Request with a standardized verification proof, so that reviewers have step-by-step commands and test logs demonstrating correctness.
25. As a CTL, I want dual-channel merge capability via either GitHub UI or Slack War-Room buttons, so that I can merge changes wherever I am reviewing.
26. As a Lead Orchestrator, I want the ephemeral git workspace automatically cleaned up and the associated GitHub issue closed once the pull request merges, so that no orphaned branches or disk resources linger.
27. As a CTL, I want to toggle between compact single-channel operation and segregated high-volume channel topology via environment configuration, so that team notification volume matches organization size.
28. As a developer, I want local subscription OAuth credentials utilized for cloud inference without hardcoding or distributing API keys, so that development environments remain secure.

## Implementation Decisions

- **Module Structure & Responsibilities**:
  - *Trigger & Intake Subsystem*: Listens for GitHub issue label events and Slack slash commands/mentions. Maps task identifiers 1:1 with Slack War-Room thread timestamps and GitHub issue numbers.
  - *Persona & Conversation Engine*: Employs role-based personas with dedicated system prompts and skill integrations (Mira with grilling, Arjun with codebase-design, Kabir with tdd, Chetna with test automation, Vivek with code-review, Dhruv with quota management). Routes messages to active phase persona or regex-extracted virtual mentions.
  - *Task Budget & Governance Subsystem*: Implements post-turn runtime hooks that track prompt and response tokens and turn counts against task budget thresholds. Triggers Safety Gates that halt execution and request authorization when ceilings are reached.
  - *Workspace Management Subsystem*: Provisions isolated, ephemeral git worktrees for each task. Manages branch creation, workspace directory allocation, merge conflict avoidance, and automated teardown upon task completion.
  - *Dual-Axis Review & Quality Assurance Subsystem*: Orchestrates independent test suite runs, diff analysis against repo coding standards, and spec validation. Mediates autonomous retry cycles between reviewer and coder before escalation.
  - *Delivery & Pull Request Automation Subsystem*: Generates Pull Requests containing reproducible CLI verification steps, test output summaries, and diff highlights. Coordinates dual-channel merge listener (polling GitHub PR status and receiving Slack interactive block actions).
  - *Hybrid Persistence Store*: Couples an indexed relational database storing task metadata, living spec state, and event logs with flat-file session trajectory logs holding turn records, tool calls, and observations.
- **Architectural & Interface Decisions**:
  - *Zero Hardcoded Secrets*: Host authentication utilizes active local subscription credentials rather than raw API keys.
  - *In-Place Mutation*: All interactive UI controls in Slack replace button blocks with immutable, timestamped approval audit blocks upon user invocation to prevent stale interaction.
  - *Channel Topology Modes*: Supports a single-channel compact mode for low-volume operation and a segmented three-channel configuration (Triage, War-Room, Releases) for high-volume enterprise production.
  - *Thread-Aware Rehydration*: Any incoming webhook or event in a known War-Room thread restores task state and trajectory, injecting the living spec into prompt context.
  - *Autonomous Retry Bound*: Internal review defect cycles between the Staff Reviewer and Coder Sub-Agent are capped at two iterations before human escalation.

## Testing Decisions

- **Good Test Criteria**: Tests must verify external system behavior and contracts, not internal private functions or brittle class implementations. All tests must be deterministic, run without live external network calls, and execute against isolated, temporary environments.
- **Modules Tested**:
  - End-to-end task pipeline: intake through worktree creation, multi-agent loop, review gates, and pull request closure.
  - Quota warden hooks: enforcement of turn and token limits, safety gate triggering, and budget expansion handling.
  - Slack event routing and Block Kit mutation: persona extraction, thread rehydration, and interactive button payload processing.
  - Worktree manager: ephemeral workspace lifecycle, branch isolation, and cleanup upon task termination.
  - Multi-agent orchestrator: autonomous defect repair loops and escalation triggers.
- **Prior Art**:
  - `tests/test_e2e_pipeline.py`: End-to-end pipeline execution and worktree lifecycle verification against sample repositories.
  - `tests/test_orchestrator.py`: Multi-agent self-correction and retry loops between coder and QA sub-agents.
  - `tests/test_budget_hooks.py`: Task budget enforcement and post-turn token tracking.
  - `tests/test_slack.py`: Slack message handling, approval gates, and persona formatting.
  - `tests/fixtures/sample_repo.py`: Isolated git repository fixture management.

## Out of Scope

- Multi-repository simultaneous cross-repo tasks (each task is scoped strictly to a single target repository).
- Direct production deployment / CD orchestration (responsibility ends at merging verified PRs into main).
- Replacement of Slack with alternative chat platforms (Teams, Discord).
- Real-time voice or video streaming interactions with agent personas.
- Fine-tuning or retraining underlying foundational models.

## Further Notes

- The swarm architecture relies on Google Antigravity local runner runtime with subscription OAuth credentials.
- Standard task budget baseline is set to 150,000 tokens and 15 turns per task.
- Communication guardrails mandate concise 2–4 sentence collegial responses from all personas.
