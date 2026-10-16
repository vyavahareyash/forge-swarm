"""
Lead Orchestrator & Sub-Agent Swarm Coordinator.

Implements Stage 2: Multi-Agent Swarm with Tiered Thinking:
- Orchestrator (Atlas): High Thinking
- Coder Sub-Agent (Forge): Medium Thinking
- QA Sub-Agent (Sentinel): Minimal Thinking

Autonomous 3-retry self-correction loop against isolated worktrees.
"""

import asyncio
import os
import re
import shutil
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.agents.agy_runner import AgyRunner, find_agy_binary
from src.schemas import (
    ImplementationPlan,
    LivingSpec,
    QATestReport,
    SubTask,
    SwarmExecutionResult,
    SwarmTurnLog,
    TurnRecord,
)
from src.storage.db import (
    create_task,
    init_db,
    record_turn_tokens,
    update_task_status,
)
from src.storage.session_manager import SessionManager
from src.worktree import WorktreeManager


class QASubAgent:
    """Sentinel: Runs tests in worktree and produces structured QATestReport."""

    def __init__(self, worktree_path: Path):
        self.worktree_path = worktree_path

    async def run_tests(self, test_cmd: str | None = None) -> tuple[QATestReport, int]:
        """Runs test command inside worktree and returns (QATestReport, tokens_spent)."""
        tokens_spent = 350  # Telemetry baseline for QA telemetry
        cmd = (
            test_cmd
            or f"{sys.executable} -m pytest -c pyproject.toml -p no:deepeval tests/"
        )
        env = {
            **os.environ,
            "PYTHONPATH": str(self.worktree_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(self.worktree_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")
        full_output = f"{stdout_str}\n{stderr_str}".strip()

        passed = proc.returncode == 0

        # Parse test metrics from pytest output
        total_tests = 0

        # Pattern match e.g. "1 failed, 2 passed in 0.05s" or "3 passed in 0.05s"
        match_passed = re.search(r"(\d+)\s+passed", full_output)
        match_failed = re.search(r"(\d+)\s+failed", full_output)

        p_count = int(match_passed.group(1)) if match_passed else 0
        f_count = int(match_failed.group(1)) if match_failed else 0
        total_tests = p_count + f_count

        failure_summary = None if passed else full_output

        # Identify files from output
        suggested_files = []
        for line in full_output.splitlines():
            if ".py:" in line or "math_lib/" in line:
                for part in line.split():
                    if ".py" in part:
                        file_clean = part.split(":")[0].strip()
                        if (
                            file_clean not in suggested_files
                            and (self.worktree_path / file_clean).exists()
                        ):
                            suggested_files.append(file_clean)

        if not suggested_files and not passed:
            suggested_files = ["math_lib/calculator.py"]

        report = QATestReport(
            passed=passed,
            command_executed=cmd,
            total_tests=total_tests,
            failed_tests=f_count,
            failure_summary=failure_summary,
            suggested_fix_files=suggested_files,
        )
        return report, tokens_spent


class CoderSubAgent:
    """Forge: Scoped file editor applying patches in the worktree."""

    def __init__(self, worktree_path: Path, runner: AgyRunner | None = None):
        self.worktree_path = worktree_path
        self.runner = runner

    async def apply_fix(
        self,
        plan: ImplementationPlan,
        failure_report: QATestReport,
        attempt: int,
        mode: str = "standalone",
    ) -> tuple[str, int]:
        """Analyzes failure and edits target files inside worktree."""
        tokens_spent = 1500 + (attempt * 200)

        if mode == "agy" and self.runner and self.runner.is_available():
            prompt = (
                f"You are Forge (SWE sub-agent). Working directory: {self.worktree_path}.\n"
                f"Task: Implement the required fix or feature according to this plan:\n"
                f"Summary: {plan.summary}\n"
                f"Failure traceback (if any):\n{failure_report.failure_summary}\n"
                f"Target files: {failure_report.suggested_fix_files}.\n"
                f"Please inspect the workspace, write the code changes or new features, and add/update unit tests in tests/."
            )
            res = await self.runner.execute_turn(
                prompt, workspace_dir=str(self.worktree_path)
            )
            tokens_spent = res.total_tokens or tokens_spent
            return res.text or "Applied code changes via agy", tokens_spent

        # Standalone / deterministic code fix application
        target_file = self.worktree_path / "math_lib" / "calculator.py"
        test_file = self.worktree_path / "tests" / "test_calculator.py"
        actions = []

        if target_file.exists():
            content = target_file.read_text()
            # Fix defect: return 2 -> return 1 for n == 1
            if "return 2" in content:
                content = content.replace("return 2", "return 1")
                actions.append("fixed calculate_fibonacci(1) base case")

            # Check if factorial feature requested
            plan_text = (plan.summary or "").lower()
            if "factorial" in plan_text and "calculate_factorial" not in content:
                factorial_code = (
                    "\n\ndef calculate_factorial(n: int) -> int:\n"
                    '    """Calculates the factorial of non-negative integer n."""\n'
                    "    if n < 0:\n"
                    '        raise ValueError("n must be non-negative")\n'
                    "    if n in (0, 1):\n"
                    "        return 1\n"
                    "    res = 1\n"
                    "    for i in range(2, n + 1):\n"
                    "        res *= i\n"
                    "    return res\n"
                )
                content += factorial_code
                actions.append("implemented calculate_factorial")

                if test_file.exists():
                    test_content = test_file.read_text()
                    if "test_factorial" not in test_content:
                        test_code = (
                            "\n\ndef test_factorial():\n"
                            "    from math_lib.calculator import calculate_factorial\n"
                            "    assert calculate_factorial(0) == 1\n"
                            "    assert calculate_factorial(1) == 1\n"
                            "    assert calculate_factorial(4) == 24\n"
                            "    assert calculate_factorial(5) == 120\n"
                        )
                        test_file.write_text(test_content + test_code)

            if actions:
                target_file.write_text(content)
                pycache_dir = target_file.parent / "__pycache__"
                if pycache_dir.exists():
                    shutil.rmtree(pycache_dir, ignore_errors=True)
                return f"Patched code: {', '.join(actions)}", tokens_spent

        return "No modification applied or file not found", tokens_spent


class Orchestrator:
    """Lead Orchestrator (Atlas) coordinating worktree and sub-agents."""

    def __init__(
        self,
        workspace_manager: WorktreeManager | None = None,
        db_path: str | None = None,
        sessions_dir: str | None = None,
    ):
        self.wm = workspace_manager or WorktreeManager()
        self.db_path = db_path or os.path.abspath("storage/state.db")
        self.sessions_dir = Path(sessions_dir or os.path.abspath("storage/sessions"))
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        init_db(self.db_path)
        self.session_manager = SessionManager(
            db_path=self.db_path, sessions_dir=str(self.sessions_dir)
        )

    async def plan_task(
        self,
        issue_id: str,
        issue_desc: str,
        mode: str = "standalone",
        runner: AgyRunner | None = None,
    ) -> tuple[ImplementationPlan, int]:
        """Atlas generates high-level structured implementation plan (High thinking)."""
        tokens_spent = 2100

        if mode == "agy" and runner and runner.is_available():
            prompt = (
                f"You are Atlas (Lead Architect). In one clear sentence, summarize the implementation strategy for:\n"
                f"Issue: {issue_id}: {issue_desc}"
            )
            res = await runner.execute_turn(prompt, timeout=180.0)
            tokens_spent = res.total_tokens or tokens_spent

        plan = ImplementationPlan(
            issue_number=int(re.sub(r"\D", "", issue_id) or "1"),
            summary=f"Resolve defect reported in {issue_id}: {issue_desc}",
            branch_name=f"fix/{issue_id}",
            subtasks=[
                SubTask(
                    id="subtask-1",
                    title="Run Sentinel QA verification to reproduce failing test",
                    target_files=["tests/test_calculator.py"],
                    acceptance_criteria="Test failure reproduced with detailed traceback",
                ),
                SubTask(
                    id="subtask-2",
                    title="Dispatch Forge Coder to correct logic in calculator.py",
                    target_files=["math_lib/calculator.py"],
                    acceptance_criteria="Fibonacci base case n=1 returns 1",
                ),
                SubTask(
                    id="subtask-3",
                    title="Re-run Sentinel QA test suite to verify green status",
                    target_files=["tests/test_calculator.py"],
                    acceptance_criteria="All tests pass cleanly",
                ),
            ],
            estimated_token_budget=150_000,
        )
        return plan, tokens_spent

    async def run_task(
        self,
        issue_id: str,
        issue_desc: str,
        repo_path: str | Path,
        budget_limit: int = 150_000,
        max_retries: int = 3,
        mode: str = "standalone",
        on_turn_callback: Callable[[SwarmTurnLog], Any] | None = None,
        cleanup_worktree: bool = True,
    ) -> SwarmExecutionResult:
        """
        Executes the autonomous swarm loop inside an isolated worktree.
        """
        task_id = f"task-{issue_id}-{int(datetime.now(UTC).timestamp())}"
        create_task(
            task_id=task_id,
            issue_id=issue_id,
            slack_thread_ts=f"thread-{issue_id}",
            conversation_id=f"conv-{task_id}",
            budget_limit=budget_limit,
            turn_limit=15,
            db_path=self.db_path,
        )

        living_spec = LivingSpec(
            task_id=task_id,
            version=1,
            requirements=[issue_desc],
            architecture_decisions=[],
            review_state={"retries_used": 0, "status": "RUNNING"},
            open_questions=[],
            updated_at=datetime.now(UTC).isoformat(),
        )
        self.session_manager.init_session(
            task_id=task_id,
            issue_id=issue_id,
            living_spec=living_spec,
        )

        trajectory: list[SwarmTurnLog] = []
        total_tokens = 0
        branch_name = f"fix/{issue_id}"

        turn_counter = 0

        def log_turn(
            persona: str,
            role: str,
            action: str,
            thinking_level: str,
            tokens: int,
            details: dict,
        ):
            nonlocal total_tokens, turn_counter
            turn_counter += 1
            total_tokens += tokens
            turn_log = SwarmTurnLog(
                persona=persona,
                role=role,
                action=action,
                thinking_level=thinking_level,  # type: ignore[arg-type]
                tokens_spent=tokens,
                details=details,
            )
            trajectory.append(turn_log)

            turn_record = TurnRecord(
                turn_index=turn_counter,
                persona=persona,
                role=role,
                action=action,
                thinking_level=thinking_level,  # type: ignore[arg-type]
                prompt=details.get("prompt"),
                thoughts=details.get("thoughts"),
                tool_calls=details.get("tool_calls", []),
                tool_results=details.get("tool_results", []),
                response_text=details.get("summary") or details.get("response"),
                tokens_spent=tokens,
                timestamp=datetime.now(UTC).isoformat(),
                details=details,
            )
            self.session_manager.append_turn(task_id, turn_record)

            prompt_t = int(tokens * 0.4)
            cand_t = int(tokens * 0.4)
            thought_t = tokens - prompt_t - cand_t
            record_turn_tokens(
                task_id=task_id,
                turn_index=turn_counter,
                prompt_tokens=prompt_t,
                candidates_tokens=cand_t,
                thoughts_tokens=thought_t,
                total_tokens=tokens,
                db_path=self.db_path,
            )
            if on_turn_callback:
                on_turn_callback(turn_log)

        agy_bin = find_agy_binary() if mode == "agy" else None
        runner = AgyRunner(agy_bin) if agy_bin else None

        # 1. Provision isolated worktree
        worktree_path = self.wm.create_worktree(
            repo_path=repo_path,
            issue_id=issue_id,
            branch_name=branch_name,
        )

        try:
            # 2. Atlas Planning Turn
            plan, plan_tokens = await self.plan_task(
                issue_id, issue_desc, mode=mode, runner=runner
            )
            living_spec.architecture_decisions = [plan.summary] + [
                s.title for s in plan.subtasks
            ]
            self.session_manager.update_living_spec(task_id, living_spec)

            log_turn(
                persona="Atlas (Lead Architect)",
                role="orchestrator",
                action="Generated ImplementationPlan",
                thinking_level="HIGH",
                tokens=plan_tokens,
                details={
                    "plan_summary": plan.summary,
                    "subtasks_count": len(plan.subtasks),
                },
            )

            # 3. Sentinel Baseline QA Verification Turn
            qa_agent = QASubAgent(worktree_path)
            coder_agent = CoderSubAgent(worktree_path, runner=runner)

            report, qa_tokens = await qa_agent.run_tests()
            log_turn(
                persona="Sentinel (QA Automation)",
                role="qa",
                action="Executed Baseline Test Suite",
                thinking_level="MINIMAL",
                tokens=qa_tokens,
                details={
                    "passed": report.passed,
                    "failed_tests": report.failed_tests,
                    "total_tests": report.total_tests,
                },
            )

            # 4. Self-Correction Loop
            retries = 0
            final_report = report

            while not final_report.passed and retries < max_retries:
                retries += 1

                # Forge Coder Turn
                fix_msg, coder_tokens = await coder_agent.apply_fix(
                    plan, final_report, attempt=retries, mode=mode
                )
                log_turn(
                    persona="Forge (SWE Sub-Agent)",
                    role="coder",
                    action=f"Applied Patch (Attempt {retries}/{max_retries})",
                    thinking_level="MEDIUM",
                    tokens=coder_tokens,
                    details={"summary": fix_msg, "attempt": retries},
                )

                # Sentinel Re-verification Turn
                final_report, test_tokens = await qa_agent.run_tests()
                log_turn(
                    persona="Sentinel (QA Automation)",
                    role="qa",
                    action=f"Re-executed Test Suite (Attempt {retries})",
                    thinking_level="MINIMAL",
                    tokens=test_tokens,
                    details={
                        "passed": final_report.passed,
                        "failed_tests": final_report.failed_tests,
                        "total_tests": final_report.total_tests,
                    },
                )

                if final_report.passed:
                    break

            # 5. Final status and commit
            git_diff = ""
            if final_report.passed:
                status = "SUCCESS"
                self.wm.commit_changes(
                    worktree_path, f"fix({issue_id}): resolve defect verified by QA"
                )
                git_diff = self.wm.get_diff(worktree_path)
                update_task_status(task_id, "COMPLETED", db_path=self.db_path)
            else:
                status = "FAILED"
                update_task_status(task_id, "FAILED", db_path=self.db_path)

            living_spec.review_state = {
                "status": status,
                "retries_used": retries,
                "passed": final_report.passed,
                "total_tests": final_report.total_tests,
                "failed_tests": final_report.failed_tests,
            }
            self.session_manager.update_living_spec(task_id, living_spec)

            # Update final session trajectory status
            final_session = self.session_manager.load_session(task_id)
            if final_session:
                final_session.status = status
                final_session.retries_used = retries
                final_session.plan = plan
                final_session.updated_at = datetime.now(UTC).isoformat()
                self.session_manager._atomic_write_json(
                    self.session_manager._get_session_path(task_id),
                    final_session.model_dump(),
                )

            result = SwarmExecutionResult(
                task_id=task_id,
                issue_id=issue_id,
                status=status,
                retries_used=retries,
                plan=plan,
                final_report=final_report,
                git_diff=git_diff,
                trajectory=trajectory,
                tokens_spent=total_tokens,
            )

            return result

        finally:
            if cleanup_worktree:
                self.wm.remove_worktree(repo_path, worktree_path, force=True)
