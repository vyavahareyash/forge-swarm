"""
Unit tests for structured Pydantic schemas.
"""
import pytest
from src.schemas import (
    ImplementationPlan,
    QATestReport,
    SubTask,
    SwarmExecutionResult,
    SwarmTurnLog,
)


def test_subtask_and_implementation_plan_validation():
    subtask = SubTask(
        id="subtask-1",
        title="Fix math defect",
        target_files=["math_lib/calculator.py"],
        acceptance_criteria="Tests pass",
    )
    plan = ImplementationPlan(
        issue_number=42,
        summary="Fix bug in calculator",
        branch_name="fix/issue-42",
        subtasks=[subtask],
        estimated_token_budget=100_000,
    )
    assert plan.issue_number == 42
    assert len(plan.subtasks) == 1
    assert plan.subtasks[0].id == "subtask-1"
    json_str = plan.model_dump_json()
    assert "fix/issue-42" in json_str


def test_qa_test_report_validation():
    report = QATestReport(
        passed=False,
        command_executed="pytest tests/",
        total_tests=5,
        failed_tests=1,
        failure_summary="AssertionError: 2 != 1",
        suggested_fix_files=["math_lib/calculator.py"],
    )
    assert not report.passed
    assert report.failed_tests == 1
    assert "math_lib/calculator.py" in report.suggested_fix_files


def test_swarm_execution_result_serialization():
    turn = SwarmTurnLog(
        persona="Atlas",
        role="orchestrator",
        action="Planned",
        thinking_level="HIGH",
        tokens_spent=2000,
    )
    result = SwarmExecutionResult(
        task_id="task-123",
        issue_id="issue-42",
        status="SUCCESS",
        retries_used=1,
        trajectory=[turn],
        tokens_spent=2000,
    )
    dumped = result.model_dump()
    assert dumped["status"] == "SUCCESS"
    assert dumped["tokens_spent"] == 2000
