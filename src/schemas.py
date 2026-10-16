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
