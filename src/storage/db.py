"""SQLite database state store for tasks and token burn history."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "storage" / "state.db")


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    if isinstance(db_path, sqlite3.Connection):
        return db_path
    target_path = db_path or DEFAULT_DB_PATH
    if not target_path.startswith("file:"):
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    conn = sqlite3.connect(target_path, uri=target_path.startswith("file:"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if not str(target_path).startswith("file:") and ":memory:" not in str(target_path):
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize SQLite schema for tasks and token turns."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    issue_id TEXT,
                    slack_thread_ts TEXT,
                    conversation_id TEXT,
                    status TEXT NOT NULL DEFAULT 'RUNNING',
                    budget_limit INTEGER NOT NULL DEFAULT 150000,
                    turn_limit INTEGER NOT NULL DEFAULT 15,
                    current_tokens INTEGER NOT NULL DEFAULT 0,
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    candidates_tokens INTEGER NOT NULL DEFAULT 0,
                    thoughts_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    cumulative_tokens INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    event_type TEXT NOT NULL,
                    user_id TEXT,
                    payload TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS living_specs (
                    task_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL DEFAULT 1,
                    requirements TEXT NOT NULL,
                    architecture_decisions TEXT NOT NULL,
                    review_state TEXT NOT NULL,
                    open_questions TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                );
                """
            )
    finally:
        conn.close()



def create_task(
    task_id: str,
    issue_id: Optional[str] = None,
    slack_thread_ts: Optional[str] = None,
    conversation_id: Optional[str] = None,
    budget_limit: int = 150_000,
    turn_limit: int = 15,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new task record in SQLite."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM token_turns WHERE task_id = ?;", (task_id,))
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks (
                    task_id, issue_id, slack_thread_ts, conversation_id,
                    status, budget_limit, turn_limit, current_tokens, turn_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, 0, 0, ?, ?);
                """,
                (
                    task_id,
                    issue_id,
                    slack_thread_ts,
                    conversation_id,
                    budget_limit,
                    turn_limit,
                    now,
                    now,
                ),
            )
        return get_task(task_id, db_path=db_path)  # type: ignore[return-value]
    finally:
        conn.close()


def get_task(task_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve task metadata by task_id."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("SELECT * FROM tasks WHERE task_id = ?;", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def list_tasks(
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve tasks with optional status and search filtering."""
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM tasks WHERE 1=1"
        params: List[Any] = []
        if status and status.upper() != "ALL":
            query += " AND status = ?"
            params.append(status.upper())
        if search:
            query += " AND (task_id LIKE ? OR issue_id LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term])
        query += " ORDER BY updated_at DESC LIMIT ?;"
        params.append(limit)
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_task_status(
    task_id: str,
    status: str,
    db_path: Optional[str] = None,
) -> None:
    """Update task status (e.g. RUNNING, PAUSED_BUDGET, COMPLETED, FAILED)."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?;",
                (status, now, task_id),
            )
    finally:
        conn.close()


def record_turn_tokens(
    task_id: str,
    turn_index: int,
    prompt_tokens: int,
    candidates_tokens: int,
    thoughts_tokens: int,
    total_tokens: int,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Record turn tokens and update cumulative spend on task atomically."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "SELECT current_tokens, turn_count, budget_limit FROM tasks WHERE task_id = ?;",
                (task_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Task {task_id} not found")

            new_cumulative = row["current_tokens"] + total_tokens
            new_turn_count = max(row["turn_count"], turn_index)

            conn.execute(
                """
                INSERT INTO token_turns (
                    task_id, turn_index, prompt_tokens, candidates_tokens,
                    thoughts_tokens, total_tokens, cumulative_tokens, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task_id,
                    turn_index,
                    prompt_tokens,
                    candidates_tokens,
                    thoughts_tokens,
                    total_tokens,
                    new_cumulative,
                    now,
                ),
            )

            conn.execute(
                """
                UPDATE tasks
                SET current_tokens = ?, turn_count = ?, updated_at = ?
                WHERE task_id = ?;
                """,
                (new_cumulative, new_turn_count, now, task_id),
            )

        return {
            "task_id": task_id,
            "turn_index": turn_index,
            "turn_tokens": total_tokens,
            "cumulative_tokens": new_cumulative,
            "budget_limit": row["budget_limit"],
            "budget_exceeded": new_cumulative >= row["budget_limit"],
        }
    finally:
        conn.close()


def extend_task_budget(
    task_id: str,
    additional_tokens: int,
    additional_turns: int = 5,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Extend budget and turns for a paused task."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                UPDATE tasks
                SET budget_limit = budget_limit + ?,
                    turn_limit = turn_limit + ?,
                    status = 'RUNNING',
                    updated_at = ?
                WHERE task_id = ?;
                """,
                (additional_tokens, additional_turns, now, task_id),
            )
        return get_task(task_id, db_path=db_path)  # type: ignore[return-value]
    finally:
        conn.close()


def get_turn_history(task_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve turn token log history for a task."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM token_turns WHERE task_id = ? ORDER BY turn_index ASC;",
            (task_id,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_task_by_thread_ts(slack_thread_ts: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve task record by associated Slack thread timestamp."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM tasks WHERE slack_thread_ts = ?;",
            (slack_thread_ts,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def update_task_thread_ts(task_id: str, slack_thread_ts: str, db_path: Optional[str] = None) -> None:
    """Update task's Slack thread timestamp."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE tasks SET slack_thread_ts = ?, updated_at = ? WHERE task_id = ?;",
                (slack_thread_ts, now, task_id),
            )
    finally:
        conn.close()


def record_slack_event(
    event_type: str,
    payload: Any,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Record a Slack event or interaction for audit history."""
    import json
    now = datetime.now(timezone.utc).isoformat()
    payload_str = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO slack_events (task_id, event_type, user_id, payload, timestamp)
                VALUES (?, ?, ?, ?, ?);
                """,
                (task_id, event_type, user_id, payload_str, now),
            )
    finally:
        conn.close()


def get_slack_events(task_id: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve recorded Slack events for audit trail."""
    conn = get_connection(db_path)
    try:
        if task_id:
            cursor = conn.execute(
                "SELECT * FROM slack_events WHERE task_id = ? ORDER BY id ASC;",
                (task_id,),
            )
        else:
            cursor = conn.execute("SELECT * FROM slack_events ORDER BY id DESC LIMIT 50;")
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def save_living_spec(
    spec: Any,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert or update a living spec in SQLite."""
    from src.schemas import LivingSpec
    if isinstance(spec, dict):
        spec = LivingSpec(**spec)

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO living_specs (
                    task_id, version, requirements, architecture_decisions,
                    review_state, open_questions, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    version = excluded.version,
                    requirements = excluded.requirements,
                    architecture_decisions = excluded.architecture_decisions,
                    review_state = excluded.review_state,
                    open_questions = excluded.open_questions,
                    updated_at = excluded.updated_at;
                """,
                (
                    spec.task_id,
                    spec.version,
                    json.dumps(spec.requirements),
                    json.dumps(spec.architecture_decisions),
                    json.dumps(spec.review_state),
                    json.dumps(spec.open_questions),
                    spec.updated_at or now,
                ),
            )
        return spec.model_dump()
    finally:
        conn.close()


def get_living_spec(task_id: str, db_path: Optional[str] = None) -> Optional[Any]:
    """Retrieve living spec for a task from SQLite."""
    from src.schemas import LivingSpec
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("SELECT * FROM living_specs WHERE task_id = ?;", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return LivingSpec(
            task_id=row["task_id"],
            version=row["version"],
            requirements=json.loads(row["requirements"]),
            architecture_decisions=json.loads(row["architecture_decisions"]),
            review_state=json.loads(row["review_state"]),
            open_questions=json.loads(row["open_questions"]),
            updated_at=row["updated_at"],
        )
    finally:
        conn.close()


def update_living_spec(
    task_id: str,
    requirements: Optional[List[str]] = None,
    architecture_decisions: Optional[List[str]] = None,
    review_state: Optional[Dict[str, Any]] = None,
    open_questions: Optional[List[str]] = None,
    db_path: Optional[str] = None,
) -> Optional[Any]:
    """Update specific fields of an existing living spec, incrementing its version."""
    from src.schemas import LivingSpec
    current = get_living_spec(task_id, db_path=db_path)
    if not current:
        return None

    now = datetime.now(timezone.utc).isoformat()
    new_version = current.version + 1
    new_reqs = requirements if requirements is not None else current.requirements
    new_arch = architecture_decisions if architecture_decisions is not None else current.architecture_decisions
    new_review = review_state if review_state is not None else current.review_state
    new_questions = open_questions if open_questions is not None else current.open_questions

    updated = LivingSpec(
        task_id=task_id,
        version=new_version,
        requirements=new_reqs,
        architecture_decisions=new_arch,
        review_state=new_review,
        open_questions=new_questions,
        updated_at=now,
    )
    save_living_spec(updated, db_path=db_path)
    return updated


def get_task_by_id_or_thread(identifier: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve task record by task_id or fallback to slack_thread_ts."""
    task = get_task(identifier, db_path=db_path)
    if task:
        return task
    return get_task_by_thread_ts(identifier, db_path=db_path)
