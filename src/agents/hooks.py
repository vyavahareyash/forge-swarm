"""Lifecycle hooks for token metering, budget governance, and pause gates."""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from src.storage.db import (
    extend_task_budget as db_extend_budget,
    get_task,
    record_turn_tokens,
    update_task_status,
)

logger = logging.getLogger("npc_inc.hooks")


@dataclass
class TurnUsage:
    prompt_tokens: int = 0
    candidates_tokens: int = 0
    thoughts_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_sdk_metadata(cls, metadata: Any) -> "TurnUsage":
        """Extract token usage from UsageMetadata or dictionary."""
        if metadata is None:
            return cls()

        if isinstance(metadata, dict):
            return cls(
                prompt_tokens=metadata.get("prompt_token_count", 0) or metadata.get("prompt_tokens", 0),
                candidates_tokens=metadata.get("candidates_token_count", 0) or metadata.get("candidates_tokens", 0),
                thoughts_tokens=metadata.get("thoughts_token_count", 0) or metadata.get("thoughts_tokens", 0),
                total_tokens=metadata.get("total_token_count", 0) or metadata.get("total_tokens", 0),
            )

        # Object with attributes (Antigravity SDK UsageMetadata)
        prompt = getattr(metadata, "prompt_token_count", 0) or 0
        candidates = getattr(metadata, "candidates_token_count", 0) or 0
        thoughts = getattr(metadata, "thoughts_token_count", 0) or 0
        total = getattr(metadata, "total_token_count", 0) or 0

        if total == 0 and (prompt or candidates or thoughts):
            total = prompt + candidates + thoughts

        return cls(
            prompt_tokens=prompt,
            candidates_tokens=candidates,
            thoughts_tokens=thoughts,
            total_tokens=total,
        )


class BudgetGuard:
    """
    Monitors token burn and turn counts per task.
    Triggers an early halt gate (asyncio.Event) when budget limit is reached.
    """

    def __init__(
        self,
        task_id: str,
        budget_limit: int = 150_000,
        turn_limit: int = 15,
        db_path: Optional[str] = None,
        on_budget_exceeded: Optional[Callable[["BudgetGuard", Dict[str, Any]], Any]] = None,
    ):
        self.task_id = task_id
        self.budget_limit = budget_limit
        self.turn_limit = turn_limit
        self.db_path = db_path
        self.on_budget_exceeded = on_budget_exceeded

        # Event indicates whether execution is allowed to proceed (set = can run, clear = paused)
        self.proceed_event = asyncio.Event()
        self.proceed_event.set()

        self.last_cumulative_tokens: int = 0
        self.current_turn: int = 0
        self._is_paused: bool = False

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    async def handle_post_turn(
        self,
        turn_index: int,
        usage_data: Any,
        is_cumulative: bool = False,
    ) -> Dict[str, Any]:
        """
        Processes post-turn token counts, updates SQLite, and evaluates quota ceiling.
        """
        usage = TurnUsage.from_sdk_metadata(usage_data)
        self.current_turn = turn_index

        if is_cumulative:
            # If usage reported is cumulative from conversation.total_usage, compute delta
            turn_total = max(0, usage.total_tokens - self.last_cumulative_tokens)
            turn_prompt = max(0, usage.prompt_tokens)
            turn_candidates = max(0, usage.candidates_tokens)
            turn_thoughts = max(0, usage.thoughts_tokens)
            self.last_cumulative_tokens = usage.total_tokens
        else:
            turn_total = usage.total_tokens
            turn_prompt = usage.prompt_tokens
            turn_candidates = usage.candidates_tokens
            turn_thoughts = usage.thoughts_tokens
            self.last_cumulative_tokens += turn_total

        # Persist turn spend to SQLite
        record = record_turn_tokens(
            task_id=self.task_id,
            turn_index=turn_index,
            prompt_tokens=turn_prompt,
            candidates_tokens=turn_candidates,
            thoughts_tokens=turn_thoughts,
            total_tokens=turn_total,
            db_path=self.db_path,
        )

        cumulative = record["cumulative_tokens"]
        budget_limit = record["budget_limit"]

        # Check budget and turn limits
        budget_breached = cumulative >= budget_limit
        turns_breached = turn_index >= self.turn_limit

        if budget_breached or turns_breached:
            self._is_paused = True
            self.proceed_event.clear()
            reason = "BUDGET_EXCEEDED" if budget_breached else "MAX_TURNS_REACHED"
            update_task_status(self.task_id, "PAUSED_BUDGET", db_path=self.db_path)
            logger.warning(
                f"[BudgetGuard] Task {self.task_id} halted: {reason} "
                f"({cumulative}/{budget_limit} tokens, turn {turn_index}/{self.turn_limit})"
            )

            if self.on_budget_exceeded:
                res = self.on_budget_exceeded(self, record)
                if asyncio.iscoroutine(res):
                    await res

        return record

    async def wait_if_paused(self, timeout: Optional[float] = None) -> bool:
        """
        Wait until execution is resumed or timeout expires.
        Returns True if execution can proceed, False if timed out.
        """
        if self.proceed_event.is_set():
            return True
        try:
            await asyncio.wait_for(self.proceed_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def extend_budget(self, additional_tokens: int, additional_turns: int = 5) -> Dict[str, Any]:
        """
        Grants additional tokens and unblocks the pause gate.
        """
        updated = db_extend_budget(
            task_id=self.task_id,
            additional_tokens=additional_tokens,
            additional_turns=additional_turns,
            db_path=self.db_path,
        )
        self.budget_limit = updated["budget_limit"]
        self.turn_limit = updated["turn_limit"]
        self._is_paused = False
        self.proceed_event.set()
        logger.info(
            f"[BudgetGuard] Task {self.task_id} resumed with +{additional_tokens} tokens. "
            f"New ceiling: {self.budget_limit}"
        )
        return updated
