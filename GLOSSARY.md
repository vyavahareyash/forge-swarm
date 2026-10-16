# Autonomous Multi-Agent Software Engineering Team

Event-driven, issue-triggered autonomous software engineering swarm governed by subscription quotas and Slack collaboration.

## Language

**Task**:
An atomic unit of work originating from an issue or slash command assigned to the agent swarm.
_Avoid_: Job, ticket, item

**War-Room**:
A dedicated Slack thread mapped 1:1 to an active task conversation for streaming agent progress and human interaction.
_Avoid_: Channel, group chat, discussion

**Lead Orchestrator**:
The primary coordinating agent responsible for task decomposition, spec authoring, and sub-agent supervision.
_Avoid_: Manager, master, boss, supervisor

**Coder Sub-Agent**:
A specialized sub-agent scoped strictly to source code authoring, file manipulation, and git commits.
_Avoid_: Worker, programmer, dev bot

**QA Sub-Agent**:
A specialized sub-agent scoped strictly to running test suites, linters, and verification checks.
_Avoid_: Tester bot, verifier, reviewer

**Quota Warden**:
The automated guard persona enforcing token budgets and pause gates when limits are reached.
_Avoid_: Budget checker, throttle bot

**Workspace**:
An isolated git worktree created specifically for executing a single task without mutating the parent repository.
_Avoid_: Sandbox, folder, branch directory

**Turn**:
A single round-trip cycle between an agent and the model runtime containing prompts, tool invocations, and observations.
_Avoid_: Step, call, round

**Task Budget**:
The maximum ceiling of tokens and turns allocated to resolve an individual issue before requiring human approval.
_Avoid_: Limit, quota, allowance

**Safety Gate**:
A declarative policy check intercepting destructive commands or external operations to require human consent.
_Avoid_: Hook block, barrier, wall
