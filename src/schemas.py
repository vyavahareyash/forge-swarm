"""
Pydantic Schemas for Multi-Agent SWE Swarm Task Contracts.
"""
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class SubTask(BaseModel):
    id: str = Field(description="Unique identifier for subtask, e.g. subtask-1")
    title: str = Field(description="Brief title of subtask")
    target_files: List[str] = Field(default_factory=list, description="Files to create or edit")
    acceptance_criteria: str = Field(description="Specific condition for completion")


class ImplementationPlan(BaseModel):
    issue_number: int = Field(description="Target issue number")
    summary: str = Field(description="High-level plan summary")
    branch_name: str = Field(description="Target git branch name")
    subtasks: List[SubTask] = Field(default_factory=list, description="Ordered execution subtasks")
    estimated_token_budget: int = Field(default=150_000, description="Estimated total token budget")


class QATestReport(BaseModel):
    passed: bool = Field(description="True if all tests passed, False otherwise")
    command_executed: str = Field(description="Shell test command executed")
    total_tests: int = Field(default=0, description="Number of tests executed")
    failed_tests: int = Field(default=0, description="Number of failing tests")
    failure_summary: Optional[str] = Field(default=None, description="Detailed traceback / error log if failed")
    suggested_fix_files: List[str] = Field(default_factory=list, description="Files suggested for inspection")


class SwarmTurnLog(BaseModel):
    persona: str
    role: str
    action: str
    thinking_level: Literal["HIGH", "MEDIUM", "MINIMAL"]
    tokens_spent: int = 0
    details: Dict[str, Any] = Field(default_factory=dict)


class ToolInvocation(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    tool_call_id: Optional[str] = None


class ToolResultRecord(BaseModel):
    tool_name: str
    output: str
    status: str = "success"
    tool_call_id: Optional[str] = None


class LivingSpec(BaseModel):
    task_id: str
    version: int = 1
    requirements: List[str] = Field(default_factory=list)
    architecture_decisions: List[str] = Field(default_factory=list)
    review_state: Dict[str, Any] = Field(default_factory=dict)
    open_questions: List[str] = Field(default_factory=list)
    updated_at: str


class TurnRecord(BaseModel):
    turn_index: int
    persona: str
    role: str
    action: str
    thinking_level: Literal["HIGH", "MEDIUM", "MINIMAL"] = "MEDIUM"
    prompt: Optional[str] = None
    thoughts: Optional[str] = None
    tool_calls: List[ToolInvocation] = Field(default_factory=list)
    tool_results: List[ToolResultRecord] = Field(default_factory=list)
    response_text: Optional[str] = None
    tokens_spent: int = 0
    timestamp: str
    details: Dict[str, Any] = Field(default_factory=dict)

    def to_turn_log(self) -> SwarmTurnLog:
        return SwarmTurnLog(
            persona=self.persona,
            role=self.role,
            action=self.action,
            thinking_level=self.thinking_level,
            tokens_spent=self.tokens_spent,
            details=self.details or ({"summary": self.response_text} if self.response_text else {}),
        )


class SessionTrajectory(BaseModel):
    task_id: str
    issue_id: str
    status: str = "RUNNING"
    retries_used: int = 0
    plan: Optional[ImplementationPlan] = None
    living_spec: Optional[LivingSpec] = None
    turns: List[TurnRecord] = Field(default_factory=list)
    tokens_spent: int = 0
    created_at: str
    updated_at: str


class SwarmExecutionResult(BaseModel):
    task_id: str
    issue_id: str
    status: Literal["SUCCESS", "FAILED", "PAUSED_BUDGET", "ABORTED"]
    retries_used: int
    plan: Optional[ImplementationPlan] = None
    final_report: Optional[QATestReport] = None
    git_diff: str = ""
    trajectory: List[SwarmTurnLog] = Field(default_factory=list)
    tokens_spent: int = 0

