"""
Mira Intake & Requirements Grilling Agent.

Implements Phase 1 of Autonomous Swarm:
- Interactive grilling interview probing ambiguities and edge cases (grilling skill).
- Dynamic living spec updates in SQLite.
- Block Kit acceptance card synthesis with [Approve & Start Implementation].
- GitHub issue markdown formatting and status transitions.
"""

import asyncio
import logging
import re

from src.agents.agy_runner import AgyRunner
from src.schemas import LivingSpec, SpecAcceptanceData
from src.storage.db import update_living_spec

logger = logging.getLogger(__name__)

MIRA_SYSTEM_PROMPT = """You are Mira, the Product Specifier for the autonomous software engineering swarm.
Your role: Lead requirement intake and interactive grilling to eliminate ambiguities and edge cases before engineering begins.
Voice & Tone: Inquisitive, structured, sharp. Collegial engineering peer. 2-4 sentences max per explanation.
Never speak in robotic filler or preamble.

GRILLING RULES:
1. Work in rounds of numbered questions for the current frontier of decisions.
2. Format each question:
❓ **Q1** - **<title>**: <body/choices>
➡️ <recommended answer>
3. Word each question so "yes" accepts your recommended answer.
4. Probe: edge cases, error handling, failure modes, performance expectations, out-of-scope boundaries.
5. When requirements are sufficiently specified or the user confirms readiness, summarize the agreed specification.
"""


class MiraIntakeAgent:
    """
    Orchestrates the intake interview, grilling turns, and acceptance criteria synthesis.
    """

    def __init__(
        self,
        runner: AgyRunner | None = None,
        db_path: str | None = None,
    ):
        self._explicit_runner = runner is not None
        self.runner = runner or AgyRunner()
        self.db_path = db_path

    @property
    def has_live_inference(self) -> bool:
        """Determines if agy inference should run (bypasses during automated pytest unless runner was explicitly injected)."""
        import os

        if os.getenv("PYTEST_CURRENT_TEST") and not self._explicit_runner:
            return False
        return self.runner.is_available()

    async def generate_initial_grilling(
        self,
        title: str,
        description: str,
    ) -> tuple[str, list[str]]:
        """
        Analyzes the initial task description and generates Round 1 grilling questions.
        Returns (formatted_mira_text, list_of_question_titles).
        """
        prompt = (
            f"{MIRA_SYSTEM_PROMPT}\n\n"
            f"A new task has arrived for intake:\n"
            f"Title: {title}\n"
            f"Description:\n{description}\n\n"
            f"Generate Round 1 grilling questions probing ambiguous boundaries, failure modes, and acceptance criteria."
        )

        # Use runner if available; fallback to structured heuristic extraction
        if self.has_live_inference:
            try:
                res = await asyncio.wait_for(
                    self.runner.execute_turn(prompt), timeout=15.0
                )
                text = res.text.strip()
                questions = self._extract_question_titles(text)
                return text, questions
            except Exception as e:
                logger.warning(
                    "AgyRunner failed for initial grilling, falling back to heuristics: %s",
                    e,
                )

        return self._heuristic_initial_grilling(title, description)

    def _extract_question_titles(self, text: str) -> list[str]:
        """Extracts question titles matching ❓ **Q<n>** - **<title>**:"""
        matches = re.findall(r"❓\s*\*\*Q\d+\*\*\s*-\s*\*\*([^*]+)\*\*:", text)
        return [m.strip() for m in matches]

    def _heuristic_initial_grilling(
        self, title: str, description: str
    ) -> tuple[str, list[str]]:
        """Deterministic heuristic fallback for offline tests and when agy CLI is absent."""
        clean_title = title.replace("[Draft]", "").strip()
        text = (
            f"👋 I'm **Mira** (Product Specifier). Let's nail down the requirements for **{clean_title}** "
            f"before handing off to Arjun and Kabir.\n\n"
            f"❓ **Q1** - **Failure & Edge-Case Handling**: Should invalid inputs or downstream errors "
            f"fail fast with descriptive exceptions rather than swallowing failures silently?\n\n"
            f"➡️ Yes, fail fast with explicit domain exceptions and actionable error messages.\n\n"
            f"---\n\n"
            f"❓ **Q2** - **Scope & Boundaries**: Are changes strictly confined to the requested component, "
            f"with external refactoring and unrelated feature additions considered out of scope?\n\n"
            f"➡️ Yes, keep scope tightly bounded to this task without modifying unrelated systems.\n\n"
            f"---\n\n"
            f"Reply with your answers or steering notes in this thread. Once aligned, I'll compile the official acceptance card."
        )
        questions = ["Failure & Edge-Case Handling", "Scope & Boundaries"]
        return text, questions

    def is_user_confirming_readiness(self, text: str) -> bool:
        """Checks if user is signaling they are ready to finalize requirements."""
        normalized = text.lower().strip()
        ready_triggers = [
            "looks good",
            "ready",
            "approve",
            "finalize",
            "proceed",
            "go ahead",
            "no more questions",
            "all set",
            "ship it",
            "lgtm",
            "perfect",
        ]
        return any(trigger in normalized for trigger in ready_triggers)

    async def process_reply(
        self,
        task_id: str,
        issue_id: str,
        title: str,
        user_reply: str,
        current_spec: LivingSpec,
    ) -> tuple[str, bool, SpecAcceptanceData | None]:
        """
        Processes human reply in the War-Room thread.
        Returns:
            (reply_text, is_complete, spec_acceptance_data)
        """
        # 1. Update living spec requirements with user input
        updated_reqs = list(current_spec.requirements)
        updated_reqs.append(f"User guidance: {user_reply.strip()}")
        update_living_spec(task_id, requirements=updated_reqs, db_path=self.db_path)

        # 2. Check if user is confirming readiness
        if self.is_user_confirming_readiness(user_reply):
            spec_data = self.synthesize_acceptance_spec(
                task_id=task_id,
                issue_id=issue_id,
                title=title,
                living_spec=current_spec,
                latest_reply=user_reply,
            )
            msg = (
                "🎯 **Requirements Aligned!** I have summarized our agreed specification and acceptance criteria. "
                "Please review the acceptance card below and click **[Approve & Start Implementation]** to proceed."
            )
            return msg, True, spec_data

        # 3. If live agy inference is available, ask dynamic grilling turn
        if self.has_live_inference:
            try:
                round_num = len(updated_reqs) + 1
                prompt = (
                    f"{MIRA_SYSTEM_PROMPT}\n\n"
                    f"Task: {title}\n"
                    f"Requirements gathered so far:\n"
                    + "\n".join(f"- {r}" for r in updated_reqs)
                    + f"\nUser's latest response: {user_reply.strip()}\n\n"
                    f"Instructions:\n"
                    f"If the user says they are ready or if requirements are fully specified, respond with 'STATUS: READY' followed by brief summary.\n"
                    f"Otherwise, acknowledge their input and ask the next numbered question probing unaddressed edge cases, failure modes, or scope:\n"
                    f"❓ **Q{round_num}** - **<Topic>**: <Question>\n"
                    f"➡️ <Recommended decision>\n"
                )
                res = await self.runner.execute_turn(prompt)
                turn_text = res.text.strip()
                if "STATUS: READY" in turn_text or self.is_user_confirming_readiness(
                    turn_text
                ):
                    spec_data = self.synthesize_acceptance_spec(
                        task_id=task_id,
                        issue_id=issue_id,
                        title=title,
                        living_spec=current_spec,
                        latest_reply=user_reply,
                    )
                    clean_msg = turn_text.replace("STATUS: READY", "").strip() or (
                        "🎯 **Requirements Aligned!** I have summarized our agreed specification and acceptance criteria. "
                        "Please review the acceptance card below and click **[Approve & Start Implementation]** to proceed."
                    )
                    return clean_msg, True, spec_data
                return turn_text, False, None
            except Exception as e:
                logger.warning(
                    "AgyRunner failed for process_reply, using fallback: %s", e
                )

        # 4. Fallback to deterministic heuristic for offline testing
        if len(updated_reqs) >= 4:
            spec_data = self.synthesize_acceptance_spec(
                task_id=task_id,
                issue_id=issue_id,
                title=title,
                living_spec=current_spec,
                latest_reply=user_reply,
            )
            msg = (
                "🎯 **Requirements Aligned!** I have summarized our agreed specification and acceptance criteria. "
                "Please review the acceptance card below and click **[Approve & Start Implementation]** to proceed."
            )
            return msg, True, spec_data

        round_num = len(updated_reqs) + 1
        msg = (
            f'Got it. Noted your direction:\n> *"{user_reply.strip()}"*\n\n'
            f"❓ **Q{round_num}** - **Verification Proof**: Should we mandate complete automated test coverage "
            f"with deterministic assertions before any code is marked review-ready?\n\n"
            f"➡️ Yes, require automated unit/integration tests confirming both happy and error paths.\n\n"
            f"Reply with confirmation or type *'ready'* to finalize the spec."
        )
        return msg, False, None

    def synthesize_acceptance_spec(
        self,
        task_id: str,
        issue_id: str,
        title: str,
        living_spec: LivingSpec,
        latest_reply: str = "",
    ) -> SpecAcceptanceData:
        """Synthesizes structured SpecAcceptanceData from living spec and discussion context."""
        clean_title = title.replace("[Draft]", "").strip()

        criteria = [
            f"Implement core functionality for {clean_title}.",
            "Explicit failure mode and edge-case handling with descriptive error reporting.",
            "Automated test coverage validating both successful executions and failure paths.",
            "Zero regression in existing test suites.",
        ]

        boundaries = [
            "Confined strictly to target repository and module scope.",
            "Zero breaking changes to external consumer APIs unless explicitly requested.",
        ]

        out_of_scope = [
            "Unrelated refactoring of untouched codebase modules.",
            "Production cloud infrastructure or deployment pipeline alterations.",
        ]

        resolved_questions = [
            "Failure mode handling: fail fast with actionable error logs.",
            "Scope boundaries: isolated to task-specific interfaces and tests.",
            f"Latest human steering: '{latest_reply.strip()}' incorporated.",
        ]

        return SpecAcceptanceData(
            task_id=task_id,
            issue_id=issue_id,
            title=clean_title,
            summary=f"Automated specification and implementation requirements for {clean_title}.",
            scope_and_boundaries=boundaries,
            acceptance_criteria=criteria,
            out_of_scope=out_of_scope,
            open_questions_resolved=resolved_questions,
        )

    def format_github_issue_body(
        self,
        spec: SpecAcceptanceData,
        war_room_channel: str | None = None,
        thread_ts: str | None = None,
    ) -> str:
        """
        Formats standardized clean markdown specification for the GitHub issue.
        """
        criteria_md = "\n".join(f"- [ ] {c}" for c in spec.acceptance_criteria)
        scope_md = "\n".join(f"- {s}" for s in spec.scope_and_boundaries)
        out_of_scope_md = "\n".join(f"- {o}" for o in spec.out_of_scope)
        resolved_md = "\n".join(f"- {r}" for r in spec.open_questions_resolved)

        thread_ref = (
            f"Slack War-Room (`#{war_room_channel or 'agent-ops'}` thread: `{thread_ts}`)"
            if thread_ts
            else "Slack War-Room"
        )

        return (
            f"## Summary\n\n"
            f"{spec.summary}\n\n"
            f"## Scope & Boundaries\n\n"
            f"{scope_md}\n\n"
            f"## Acceptance Criteria\n\n"
            f"{criteria_md}\n\n"
            f"## Out of Scope\n\n"
            f"{out_of_scope_md}\n\n"
            f"## Refinement History & Clarifications\n\n"
            f"{resolved_md}\n\n"
            f"---\n"
            f"*Refined and approved via Mira Intake in {thread_ref}. Ready for architectural design.*"
        )
