"""Data Loader & Telemetry Processor for Observability Dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.schemas import (
    ImplementationPlan,
    LivingSpec,
    QATestReport,
    SessionTrajectory,
    TurnRecord,
)
from src.storage.db import (
    DEFAULT_DB_PATH,
    get_living_spec,
    get_slack_events,
    get_task,
    get_turn_history,
    list_tasks,
)
from src.storage.session_manager import SessionManager


@dataclass
class PersonaBurn:
    persona: str
    tokens: int
    percentage: float
    turns_count: int


@dataclass
class TokenBurnAnalytics:
    task_id: str
    budget_limit: int
    turn_limit: int
    total_tokens: int
    current_turns: int
    percentage_budget_used: float
    is_budget_exceeded: bool
    burn_records: list[dict[str, Any]] = field(default_factory=list)
    persona_allocations: list[PersonaBurn] = field(default_factory=list)


@dataclass
class TaskDetailView:
    task_id: str
    task_record: dict[str, Any] | None
    trajectory: SessionTrajectory | None
    living_spec: LivingSpec | None
    plan: ImplementationPlan | None
    turns: list[TurnRecord]
    analytics: TokenBurnAnalytics
    final_report: QATestReport | None
    git_diff: str
    slack_events: list[dict[str, Any]]


def fetch_all_tasks(
    status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve filtered tasks from SQLite state store."""
    return list_tasks(status=status, search=search, limit=limit, db_path=db_path)


def load_task_detail(
    task_id: str,
    db_path: str | None = None,
    sessions_dir: str | None = None,
) -> TaskDetailView:
    """Reconstitute complete task observability detail from SQLite and disk."""
    db = db_path or DEFAULT_DB_PATH
    session_mgr = SessionManager(db_path=db, sessions_dir=sessions_dir)

    task_record = get_task(task_id, db_path=db)
    trajectory = session_mgr.load_session(task_id)
    living_spec = get_living_spec(task_id, db_path=db)
    if not living_spec and trajectory and trajectory.living_spec:
        living_spec = trajectory.living_spec

    turn_history = get_turn_history(task_id, db_path=db)
    slack_events = get_slack_events(task_id=task_id, db_path=db)

    # Resolve turns from trajectory or reconstruct from turn_history
    turns: list[TurnRecord] = []
    if trajectory and trajectory.turns:
        turns = trajectory.turns

    plan: ImplementationPlan | None = trajectory.plan if trajectory else None

    # Calculate token burn analytics
    analytics = compute_token_analytics(task_record, turn_history, turns)

    # Extract final QA report and git diff
    final_report, git_diff = extract_qa_and_diff(trajectory, turns)

    return TaskDetailView(
        task_id=task_id,
        task_record=task_record,
        trajectory=trajectory,
        living_spec=living_spec,
        plan=plan,
        turns=turns,
        analytics=analytics,
        final_report=final_report,
        git_diff=git_diff,
        slack_events=slack_events,
    )


def compute_token_analytics(
    task_record: dict[str, Any] | None,
    turn_history: list[dict[str, Any]],
    turns: list[TurnRecord],
) -> TokenBurnAnalytics:
    """Computes cumulative and persona-level token metrics."""
    budget_limit = task_record.get("budget_limit", 150_000) if task_record else 150_000
    turn_limit = task_record.get("turn_limit", 15) if task_record else 15
    task_id = task_record.get("task_id", "") if task_record else ""

    burn_records: list[dict[str, Any]] = []
    cumulative = 0
    persona_map: dict[str, dict[str, int]] = {}

    # Map turn index to turn record for persona association
    turn_to_persona: dict[int, str] = {}
    for t in turns:
        clean_persona = _normalize_persona_name(t.persona)
        turn_to_persona[t.turn_index] = clean_persona

    if turn_history:
        for row in turn_history:
            idx = row.get("turn_index", len(burn_records) + 1)
            t_total = row.get("total_tokens", 0)
            t_prompt = row.get("prompt_tokens", 0)
            t_cand = row.get("candidates_tokens", 0)
            t_thought = row.get("thoughts_tokens", 0)
            t_cum = row.get("cumulative_tokens", cumulative + t_total)
            cumulative = t_cum

            persona = turn_to_persona.get(idx, "Unknown")
            burn_records.append(
                {
                    "turn_index": idx,
                    "prompt_tokens": t_prompt,
                    "candidates_tokens": t_cand,
                    "thoughts_tokens": t_thought,
                    "total_tokens": t_total,
                    "cumulative_tokens": t_cum,
                    "budget_limit": budget_limit,
                    "persona": persona,
                    "timestamp": row.get("timestamp", ""),
                }
            )

            if persona not in persona_map:
                persona_map[persona] = {"tokens": 0, "turns": 0}
            persona_map[persona]["tokens"] += t_total
            persona_map[persona]["turns"] += 1
    elif turns:
        for t in turns:
            cumulative += t.tokens_spent
            clean_persona = _normalize_persona_name(t.persona)
            prompt_est = int(t.tokens_spent * 0.4)
            cand_est = int(t.tokens_spent * 0.4)
            thought_est = t.tokens_spent - prompt_est - cand_est

            burn_records.append(
                {
                    "turn_index": t.turn_index,
                    "prompt_tokens": prompt_est,
                    "candidates_tokens": cand_est,
                    "thoughts_tokens": thought_est,
                    "total_tokens": t.tokens_spent,
                    "cumulative_tokens": cumulative,
                    "budget_limit": budget_limit,
                    "persona": clean_persona,
                    "timestamp": t.timestamp,
                }
            )

            if clean_persona not in persona_map:
                persona_map[clean_persona] = {"tokens": 0, "turns": 0}
            persona_map[clean_persona]["tokens"] += t.tokens_spent
            persona_map[clean_persona]["turns"] += 1

    total_tokens = cumulative or (
        task_record.get("current_tokens", 0) if task_record else 0
    )
    current_turns = (
        len(burn_records)
        or (task_record.get("turn_count", 0) if task_record else 0)
        or len(turns)
    )
    pct = (total_tokens / budget_limit) * 100.0 if budget_limit > 0 else 0.0

    allocations: list[PersonaBurn] = []
    for p_name, data in sorted(
        persona_map.items(), key=lambda item: item[1]["tokens"], reverse=True
    ):
        p_pct = (data["tokens"] / total_tokens * 100.0) if total_tokens > 0 else 0.0
        allocations.append(
            PersonaBurn(
                persona=p_name,
                tokens=data["tokens"],
                percentage=round(p_pct, 1),
                turns_count=data["turns"],
            )
        )

    return TokenBurnAnalytics(
        task_id=task_id,
        budget_limit=budget_limit,
        turn_limit=turn_limit,
        total_tokens=total_tokens,
        current_turns=current_turns,
        percentage_budget_used=round(pct, 1),
        is_budget_exceeded=total_tokens >= budget_limit,
        burn_records=burn_records,
        persona_allocations=allocations,
    )


def extract_qa_and_diff(
    trajectory: SessionTrajectory | None,
    turns: list[TurnRecord],
) -> tuple[QATestReport | None, str]:
    """Extract QA test outcomes and git diff from trajectory or turn details."""
    report: QATestReport | None = None
    git_diff: str = ""

    if trajectory:
        if trajectory.final_report:
            report = trajectory.final_report
        if trajectory.git_diff:
            git_diff = trajectory.git_diff

    # If not attached at trajectory level, inspect turns in reverse
    if not report:
        for t in reversed(turns):
            if (
                "Sentinel" in t.persona
                or "QA" in t.role.upper()
                or "qa" in t.role.lower()
            ):
                details = t.details or {}
                if "passed" in details or "total_tests" in details:
                    report = QATestReport(
                        passed=details.get("passed", False),
                        command_executed=details.get("command_executed", "pytest"),
                        total_tests=details.get("total_tests", 0),
                        failed_tests=details.get("failed_tests", 0),
                        failure_summary=details.get("failure_summary"),
                        suggested_fix_files=details.get("suggested_fix_files", []),
                    )
                    break

    if not git_diff:
        for t in reversed(turns):
            details = t.details or {}
            if "git_diff" in details:
                git_diff = str(details["git_diff"])
                break
            elif "diff" in details:
                git_diff = str(details["diff"])
                break

    return report, git_diff


def _normalize_persona_name(raw: str) -> str:
    """Normalize persona strings to standard display tags."""
    low = raw.lower()
    if "atlas" in low or "architect" in low:
        return "Atlas (Architect)"
    if "forge" in low or "coder" in low:
        return "Forge (Coder)"
    if "sentinel" in low or "qa" in low:
        return "Sentinel (QA)"
    if "mira" in low or "specifier" in low:
        return "Mira (Specifier)"
    if "vivek" in low or "reviewer" in low:
        return "Vivek (Reviewer)"
    if "dhruv" in low or "warden" in low or "quota" in low:
        return "Dhruv (Quota)"
    if "arjun" in low:
        return "Arjun (Architect)"
    if "kabir" in low:
        return "Kabir (Coder)"
    if "chetna" in low:
        return "Chetna (QA)"
    return raw.strip()
