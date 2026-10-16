"""
Hybrid State & Trajectory Persistence Manager.

Combines SQLite state indexing (tasks, token turns, living specs, slack events)
with disk-based session trajectory persistence (storage/sessions/<task_id>.json)
and context rehydration across restarts and thread interactions.
"""
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.schemas import (
    ImplementationPlan,
    LivingSpec,
    SessionTrajectory,
    SwarmTurnLog,
    ToolInvocation,
    ToolResultRecord,
    TurnRecord,
)
from src.storage.db import (
    DEFAULT_DB_PATH,
    get_living_spec,
    get_task_by_id_or_thread,
    init_db,
    save_living_spec,
)

logger = logging.getLogger(__name__)


@dataclass
class RehydratedContext:
    """Reconstituted conversation and task context from persistent storage."""

    task: Dict[str, Any]
    living_spec: Optional[LivingSpec] = None
    trajectory: Optional[SessionTrajectory] = None
    recent_turns: List[TurnRecord] = field(default_factory=list)


class SessionManager:
    """
    Manages session trajectory persistence, atomic writes,
    task rehydration, and Living Spec prompt context injection.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        sessions_dir: Optional[str] = None,
    ):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.sessions_dir = Path(
            sessions_dir
            or Path(__file__).resolve().parent.parent.parent / "storage" / "sessions"
        ).resolve()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        init_db(self.db_path)

    def _get_session_path(self, task_id: str) -> Path:
        return self.sessions_dir / f"{task_id}.json"

    def _atomic_write_json(self, file_path: Path, data: Dict[str, Any]) -> None:
        """Writes JSON atomically via temporary file and atomic replace."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = file_path.parent
        temp_file = temp_dir / f".tmp_{file_path.stem}_{uuid.uuid4().hex}.json"

        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, file_path)
        except Exception:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            raise

    def init_session(
        self,
        task_id: str,
        issue_id: str,
        living_spec: Optional[LivingSpec] = None,
        plan: Optional[ImplementationPlan] = None,
    ) -> SessionTrajectory:
        """Initializes a new session trajectory and stores it on disk."""
        now = datetime.now(timezone.utc).isoformat()
        trajectory = SessionTrajectory(
            task_id=task_id,
            issue_id=issue_id,
            status="RUNNING",
            retries_used=0,
            plan=plan,
            living_spec=living_spec,
            turns=[],
            tokens_spent=0,
            created_at=now,
            updated_at=now,
        )

        session_path = self._get_session_path(task_id)
        self._atomic_write_json(session_path, trajectory.model_dump())

        if living_spec:
            save_living_spec(living_spec, db_path=self.db_path)

        return trajectory

    def load_session(self, task_id: str) -> Optional[SessionTrajectory]:
        """Loads and deserializes session trajectory from disk."""
        session_path = self._get_session_path(task_id)
        if not session_path.exists():
            return None

        try:
            raw_text = session_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)

            # Handle backward compatibility with legacy SwarmExecutionResult JSON
            if "turns" not in data and "trajectory" in data:
                turns = []
                for idx, t in enumerate(data.get("trajectory", []), 1):
                    turns.append(
                        TurnRecord(
                            turn_index=idx,
                            persona=t.get("persona", "Unknown"),
                            role=t.get("role", "orchestrator"),
                            action=t.get("action", ""),
                            thinking_level=t.get("thinking_level", "MEDIUM"),
                            tokens_spent=t.get("tokens_spent", 0),
                            timestamp=t.get("timestamp", datetime.now(timezone.utc).isoformat()),
                            details=t.get("details", {}),
                        )
                    )
                data["turns"] = [t.model_dump() for t in turns]

            if "created_at" not in data:
                data["created_at"] = datetime.now(timezone.utc).isoformat()
            if "updated_at" not in data:
                data["updated_at"] = datetime.now(timezone.utc).isoformat()

            return SessionTrajectory.model_validate(data)
        except Exception as e:
            logger.error("Failed to load session trajectory for %s: %s", task_id, e)
            return None

    def append_turn(
        self,
        task_id: str,
        turn: TurnRecord,
        status: Optional[str] = None,
        retries_used: Optional[int] = None,
    ) -> SessionTrajectory:
        """Appends a turn to the session trajectory and writes atomically."""
        session = self.load_session(task_id)
        now = datetime.now(timezone.utc).isoformat()

        if not session:
            session = SessionTrajectory(
                task_id=task_id,
                issue_id=task_id.replace("task-", ""),
                status=status or "RUNNING",
                retries_used=retries_used or 0,
                turns=[],
                tokens_spent=0,
                created_at=now,
                updated_at=now,
            )

        session.turns.append(turn)
        session.tokens_spent += turn.tokens_spent
        session.updated_at = now
        if status is not None:
            session.status = status
        if retries_used is not None:
            session.retries_used = retries_used

        session_path = self._get_session_path(task_id)
        self._atomic_write_json(session_path, session.model_dump())
        return session

    def update_living_spec(self, task_id: str, living_spec: LivingSpec) -> None:
        """Saves living spec to SQLite and updates session on disk."""
        save_living_spec(living_spec, db_path=self.db_path)
        session = self.load_session(task_id)
        if session:
            session.living_spec = living_spec
            session.updated_at = datetime.now(timezone.utc).isoformat()
            self._atomic_write_json(self._get_session_path(task_id), session.model_dump())

    def rehydrate(
        self,
        identifier: str,
        max_history_tokens: int = 8000,
    ) -> Optional[RehydratedContext]:
        """
        Reconstitutes conversation context from task_id or slack_thread_ts.
        Applies sliding window pruning on historical turns while keeping Living Spec intact.
        """
        task = get_task_by_id_or_thread(identifier, db_path=self.db_path)
        task_id = task["task_id"] if task else identifier

        # Load Living Spec from SQLite first, falling back to session file
        living_spec = get_living_spec(task_id, db_path=self.db_path)

        # Load session trajectory from disk
        trajectory = self.load_session(task_id)

        if not task and not trajectory:
            return None

        if not task and trajectory:
            task = {
                "task_id": trajectory.task_id,
                "issue_id": trajectory.issue_id,
                "status": trajectory.status,
                "current_tokens": trajectory.tokens_spent,
                "turn_count": len(trajectory.turns),
            }

        if not living_spec and trajectory and trajectory.living_spec:
            living_spec = trajectory.living_spec

        # Calculate sliding window of recent turns within max_history_tokens
        all_turns = trajectory.turns if trajectory else []
        accumulated_tokens = 0
        recent_turns: List[TurnRecord] = []

        for turn in reversed(all_turns):
            turn_cost = turn.tokens_spent or max(50, len(str(turn.details)) // 4)
            if accumulated_tokens + turn_cost > max_history_tokens and recent_turns:
                break
            recent_turns.append(turn)
            accumulated_tokens += turn_cost

        recent_turns.reverse()

        return RehydratedContext(
            task=task,
            living_spec=living_spec,
            trajectory=trajectory,
            recent_turns=recent_turns,
        )

    def format_prompt_context(
        self,
        context: RehydratedContext,
        base_prompt: str,
    ) -> str:
        """
        Formats rehydrated context with <LIVING_SPEC> and <CONVERSATION_HISTORY>
        injected ahead of the base prompt.
        """
        sections: List[str] = []

        if context.living_spec:
            spec = context.living_spec
            reqs = "\n".join(f"- {r}" for r in spec.requirements) if spec.requirements else "- None specified"
            archs = "\n".join(f"- {a}" for a in spec.architecture_decisions) if spec.architecture_decisions else "- None specified"
            questions = "\n".join(f"- {q}" for q in spec.open_questions) if spec.open_questions else "- None"
            review_txt = json.dumps(spec.review_state, indent=2) if spec.review_state else "{}"

            spec_block = (
                f"<LIVING_SPEC>\n"
                f"### Active Living Specification (Authoritative & Persistent)\n"
                f"- Task ID: `{spec.task_id}` | Spec Version: `{spec.version}` | Last Updated: `{spec.updated_at}`\n\n"
                f"#### Core Requirements:\n{reqs}\n\n"
                f"#### Architectural Decisions:\n{archs}\n\n"
                f"#### Review State:\n```json\n{review_txt}\n```\n\n"
                f"#### Open Questions:\n{questions}\n"
                f"</LIVING_SPEC>"
            )
            sections.append(spec_block)

        if context.recent_turns:
            history_lines = []
            for t in context.recent_turns:
                header = f"[Turn {t.turn_index}] {t.persona} ({t.thinking_level} Thinking): {t.action}"
                details = []
                if t.prompt:
                    details.append(f"Prompt: {t.prompt.strip()}")
                if t.thoughts:
                    details.append(f"Thoughts: {t.thoughts.strip()}")
                if t.tool_calls:
                    calls_str = ", ".join(c.tool_name for c in t.tool_calls)
                    details.append(f"Tools Called: {calls_str}")
                if t.tool_results:
                    results_str = "; ".join(f"{r.tool_name}: {r.output[:100]}" for r in t.tool_results)
                    details.append(f"Tool Results: {results_str}")
                if t.response_text:
                    details.append(f"Response: {t.response_text.strip()}")
                elif t.details:
                    details.append(f"Details: {json.dumps(t.details)}")

                body = "\n  ".join(details)
                history_lines.append(f"{header}\n  {body}" if body else header)

            history_block = (
                f"<CONVERSATION_HISTORY>\n"
                + "\n\n".join(history_lines)
                + "\n</CONVERSATION_HISTORY>"
            )
            sections.append(history_block)

        sections.append(base_prompt)
        return "\n\n".join(sections)
