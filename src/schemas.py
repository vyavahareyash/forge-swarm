"""
Pydantic Schemas for Multi-Agent SWE Swarm Task Contracts.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class SubTask(BaseModel):
    id: str = Field(description="Unique identifier for subtask, e.g. subtask-1")
    title: str = Field(description="Brief title of subtask")
    target_files: list[str] = Field(
        default_factory=list, description="Files to create or edit"
    )
    acceptance_criteria: str = Field(description="Specific condition for completion")


class ImplementationPlan(BaseModel):
    issue_number: int = Field(description="Target issue number")
    summary: str = Field(description="High-level plan summary")
    branch_name: str = Field(description="Target git branch name")
    subtasks: list[SubTask] = Field(
        default_factory=list, description="Ordered execution subtasks"
    )
    estimated_token_budget: int = Field(
        default=150_000, description="Estimated total token budget"
    )


class QATestReport(BaseModel):
    passed: bool = Field(description="True if all tests passed, False otherwise")
    command_executed: str = Field(description="Shell test command executed")
    total_tests: int = Field(default=0, description="Number of tests executed")
    failed_tests: int = Field(default=0, description="Number of failing tests")
    failure_summary: str | None = Field(
        default=None, description="Detailed traceback / error log if failed"
    )
    suggested_fix_files: list[str] = Field(
        default_factory=list, description="Files suggested for inspection"
    )


class SwarmTurnLog(BaseModel):
    persona: str
    role: str
    action: str
    thinking_level: Literal["HIGH", "MEDIUM", "MINIMAL"]
    tokens_spent: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


class ToolInvocation(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str | None = None


class ToolResultRecord(BaseModel):
    tool_name: str
    output: str
    status: str = "success"
    tool_call_id: str | None = None


class LivingSpec(BaseModel):
    task_id: str
    version: int = 1
    requirements: list[str] = Field(default_factory=list)
    architecture_decisions: list[str] = Field(default_factory=list)
    review_state: dict[str, Any] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)
    updated_at: str


class SpecAcceptanceData(BaseModel):
    """Structured requirement specification prepared by Mira for human sign-off."""

    task_id: str
    issue_id: str
    title: str
    summary: str
    scope_and_boundaries: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    open_questions_resolved: list[str] = Field(default_factory=list)
    war_room_thread_ts: str | None = None


class TurnRecord(BaseModel):
    turn_index: int
    persona: str
    role: str
    action: str
    thinking_level: Literal["HIGH", "MEDIUM", "MINIMAL"] = "MEDIUM"
    prompt: str | None = None
    thoughts: str | None = None
    tool_calls: list[ToolInvocation] = Field(default_factory=list)
    tool_results: list[ToolResultRecord] = Field(default_factory=list)
    response_text: str | None = None
    tokens_spent: int = 0
    timestamp: str
    details: dict[str, Any] = Field(default_factory=dict)

    def to_turn_log(self) -> SwarmTurnLog:
        return SwarmTurnLog(
            persona=self.persona,
            role=self.role,
            action=self.action,
            thinking_level=self.thinking_level,
            tokens_spent=self.tokens_spent,
            details=self.details
            or ({"summary": self.response_text} if self.response_text else {}),
        )


class SessionTrajectory(BaseModel):
    task_id: str
    issue_id: str
    status: str = "RUNNING"
    retries_used: int = 0
    plan: ImplementationPlan | None = None
    living_spec: LivingSpec | None = None
    turns: list[TurnRecord] = Field(default_factory=list)
    final_report: QATestReport | None = None
    git_diff: str = ""
    tokens_spent: int = 0
    created_at: str
    updated_at: str


class SwarmExecutionResult(BaseModel):
    task_id: str
    issue_id: str
    status: Literal["SUCCESS", "FAILED", "PAUSED_BUDGET", "ABORTED"]
    retries_used: int
    plan: ImplementationPlan | None = None
    final_report: QATestReport | None = None
    git_diff: str = ""
    trajectory: list[SwarmTurnLog] = Field(default_factory=list)
    tokens_spent: int = 0
