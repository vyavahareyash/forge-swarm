"""
Async Slack Bolt Socket Mode Bot for Agent Team War-Room.

Manages:
- Socket Mode bidirectional connection (zero incoming ports).
- Persona-styled messages via chat:write.customize.
- Interactive Block Kit approval gates (budget top-ups, safety gates, draft PR approvals).
- Thread-to-Task session mapping and human steering context injection.
"""

import asyncio
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from src.agents.mira import MiraIntakeAgent
from src.schemas import LivingSpec
from src.slack.approvals import build_spec_acceptance_blocks
from src.slack.mutations import mutate_blocks_for_resolution
from src.slack.personas import get_persona, post_agent_message
from src.slack.router import ChannelTopologyRouter, resolve_target_persona
from src.storage.db import (
    create_task,
    extend_task_budget,
    get_living_spec,
    get_task,
    get_task_by_thread_ts,
    record_slack_event,
    save_living_spec,
    update_task_status,
    update_task_thread_ts,
)
from src.storage.session_manager import RehydratedContext, SessionManager
from src.triggers.github_poller import GitHubPoller

logger = logging.getLogger(__name__)


class SlackWarRoomBot:
    """
    Manages Slack Socket Mode event loop, persona dispatches,
    and interactive Block Kit approval gates.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        app_token: str | None = None,
        default_channel: str | None = None,
        db_path: str | None = None,
        poller: GitHubPoller | None = None,
        mira: MiraIntakeAgent | None = None,
    ):
        load_dotenv()
        self.bot_token = bot_token or os.getenv("SLACK_BOT_TOKEN")
        self.app_token = app_token or os.getenv("SLACK_APP_TOKEN")
        self.default_channel = default_channel or os.getenv("SLACK_CHANNEL_ID")
        self.db_path = db_path
        self.poller = poller
        self.mira = mira or MiraIntakeAgent(db_path=self.db_path)
        self.session_manager = SessionManager(db_path=self.db_path)
        self.channel_router = ChannelTopologyRouter.from_env()
        if self.default_channel:
            self.channel_router.default_channel = self.default_channel

        self.active_gates: dict[str, asyncio.Event] = {}
        self.gate_decisions: dict[str, dict[str, Any]] = {}
        self.steering_queues: dict[str, asyncio.Queue[str]] = {}

        self.app: AsyncApp | None = None
        self.handler: AsyncSocketModeHandler | None = None
        self.is_connected: bool = False

        if self.bot_token and self.app_token:
            self._init_bolt_app()

    def get_channel(self, phase: str | None = None) -> str:
        """Returns the target channel according to configured topology and phase."""
        return self.channel_router.get_channel(phase)

    async def mutate_message_in_place(
        self,
        client: Any,
        channel: str,
        body: dict[str, Any],
        action_id: str,
        user_id: str,
        resolution_text: str | None = None,
    ) -> None:
        """Mutate message in-place to remove interactive buttons and insert an audit context."""
        try:
            message = body.get("message", {})
            orig_blocks = message.get("blocks", [])
            message_ts = message.get("ts") or body.get("container", {}).get(
                "message_ts"
            )
            if orig_blocks and message_ts:
                updated_blocks = mutate_blocks_for_resolution(
                    blocks=orig_blocks,
                    action_id=action_id,
                    user_id=user_id,
                    resolution_text=resolution_text,
                )
                if hasattr(client, "chat_update"):
                    call = client.chat_update(
                        channel=channel,
                        ts=message_ts,
                        blocks=updated_blocks,
                        text=message.get("text", "Action processed"),
                    )
                    if hasattr(call, "__await__"):
                        await call
        except Exception as e:
            logger.warning("Failed to mutate message in-place for %s: %s", action_id, e)

    async def trigger_intake_flow(
        self,
        client: Any,
        channel_id: str,
        user_id: str,
        user_name: str,
        request_text: str,
        existing_thread_ts: str | None = None,
    ) -> str:
        """
        Triggers new task intake & grilling flow:
        1. Formats clean title.
        2. Creates draft issue on GitHub via agy CLI.
        3. Initializes task and living spec in SQLite.
        4. Spawns War-Room thread in target channel.
        5. Generates Round 1 grilling questions and dispatches Mira with her persona avatar.
        """
        text = request_text.strip()
        text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
        if text.lower().startswith("solve "):
            text = text[6:].strip()
        elif text.lower().startswith("task:"):
            text = text[5:].strip()
        elif text.lower().startswith("!agent "):
            text = text[7:].strip()
        elif text.lower().startswith("/agent "):
            text = text[7:].strip()

        if not text:
            text = "General engineering request"

        first_line = text.splitlines()[0][:80].strip()
        clean_title = first_line if first_line else "Autonomous Task"

        # Create Draft Issue if poller available
        issue_number = int(datetime.now(UTC).timestamp()) % 10000 + 100
        if self.poller and self.poller.repo:
            try:
                draft_issue = self.poller.create_draft_issue(
                    title=clean_title,
                    body=text,
                    labels=["triage:agent"],
                )
                issue_number = draft_issue.number
            except Exception as e:
                logger.warning("Failed to create draft issue via poller: %s", e)

        issue_id = f"issue-{issue_number}"
        task_id = f"task-{issue_id}-{int(datetime.now(UTC).timestamp())}"

        create_task(
            task_id=task_id,
            issue_id=issue_id,
            status="INTAKE",
            db_path=self.db_path,
        )
        now_iso = datetime.now(UTC).isoformat()
        save_living_spec(
            LivingSpec(
                task_id=task_id,
                version=1,
                requirements=[text],
                architecture_decisions=[],
                review_state={},
                open_questions=[],
                updated_at=now_iso,
            ),
            db_path=self.db_path,
        )

        target_channel = channel_id or self.get_channel("triage")
        if existing_thread_ts:
            thread_ts = existing_thread_ts
            await client.chat_postMessage(
                channel=target_channel,
                thread_ts=thread_ts,
                text=f"🎯 *New Task Intake Initialized:* `{issue_id}` - *{clean_title}*",
            )
        else:
            root_res = await client.chat_postMessage(
                channel=target_channel,
                text=f"🎯 *New Task Intake:* `{issue_id}` - *{clean_title}* triggered by <@{user_id}>",
            )
            thread_ts = root_res.get("ts") or str(datetime.now(UTC).timestamp())

        update_task_thread_ts(task_id, thread_ts, db_path=self.db_path)

        record_slack_event(
            event_type="agent_solve_command",
            payload={
                "task_id": task_id,
                "issue_id": issue_id,
                "request": text,
                "user": user_name,
            },
            task_id=task_id,
            user_id=user_id,
            db_path=self.db_path,
        )

        # Mira initial grilling
        mira_text, _ = await self.mira.generate_initial_grilling(
            title=clean_title,
            description=text,
        )
        await post_agent_message(
            client=client,
            channel=target_channel,
            role="mira",
            text=mira_text,
            thread_ts=thread_ts,
        )
        return thread_ts

    def _init_bolt_app(self) -> None:
        """Configures Bolt AsyncApp and registers interactive listeners."""
        self.app = AsyncApp(token=self.bot_token)
        self._register_listeners()

    def _register_listeners(self) -> None:
        if not self.app:
            return

        # 1. Action: Grant +50k Tokens
        @self.app.action("grant_budget_50k")
        async def handle_grant_budget(ack, body, client):
            await ack()
            action_val = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            container = body.get("container", {})
            thread_ts = container.get("thread_ts") or body.get("message", {}).get("ts")

            # Mutate message in-place to remove buttons
            await self.mutate_message_in_place(
                client=client,
                channel=channel,
                body=body,
                action_id="grant_budget_50k",
                user_id=user_id or user,
            )

            # Extend in DB
            extend_task_budget(
                action_val,
                additional_tokens=50_000,
                additional_turns=5,
                db_path=self.db_path,
            )
            record_slack_event(
                event_type="grant_budget_50k",
                payload={"task_id": action_val, "extended_by": user},
                task_id=action_val,
                user_id=user_id,
                db_path=self.db_path,
            )

            # Confirm in thread
            await post_agent_message(
                client=client,
                channel=channel,
                role="warden",
                text=f"⚡ *Quota Extended:* `{user}` granted `+50,000` tokens (+5 turns) for task `{action_val}`. Resuming execution.",
                thread_ts=thread_ts,
            )

            self.resolve_gate(
                action_val,
                {"action": "grant_budget_50k", "approved": True, "user": user},
            )

        # 2. Action: Abort Task
        @self.app.action("abort_task")
        async def handle_abort_task(ack, body, client):
            await ack()
            action_val = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            container = body.get("container", {})
            thread_ts = container.get("thread_ts") or body.get("message", {}).get("ts")

            # Mutate message in-place to remove buttons
            await self.mutate_message_in_place(
                client=client,
                channel=channel,
                body=body,
                action_id="abort_task",
                user_id=user_id or user,
            )

            update_task_status(action_val, "ABORTED", db_path=self.db_path)
            record_slack_event(
                event_type="abort_task",
                payload={"task_id": action_val, "aborted_by": user},
                task_id=action_val,
                user_id=user_id,
                db_path=self.db_path,
            )

            await post_agent_message(
                client=client,
                channel=channel,
                role="warden",
                text=f"🛑 *Task Aborted:* `{user}` stopped task `{action_val}`. Isolated workspace retained for human inspection.",
                thread_ts=thread_ts,
            )

            self.resolve_gate(
                action_val, {"action": "abort_task", "approved": False, "user": user}
            )

        # 3. Action: Approve / Deny Security Policy Commands
        @self.app.action("approve_command")
        async def handle_approve_command(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

            # Mutate message in-place to remove buttons
            await self.mutate_message_in_place(
                client=client,
                channel=channel,
                body=body,
                action_id="approve_command",
                user_id=user_id or user,
            )

            record_slack_event(
                event_type="policy_approved",
                payload={"task_id": task_id, "approved_by": user},
                task_id=task_id,
                user_id=user_id,
                db_path=self.db_path,
            )
            await post_agent_message(
                client=client,
                channel=channel,
                role="warden",
                text=f"✅ *Command Approved* by `{user}`. Resuming execution.",
                thread_ts=thread_ts,
            )
            self.resolve_gate(
                f"policy_{task_id}",
                {"action": "approve_command", "approved": True, "user": user},
            )

        @self.app.action("deny_command")
        async def handle_deny_command(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

            # Mutate message in-place to remove buttons
            await self.mutate_message_in_place(
                client=client,
                channel=channel,
                body=body,
                action_id="deny_command",
                user_id=user_id or user,
            )

            record_slack_event(
                event_type="policy_denied",
                payload={"task_id": task_id, "denied_by": user},
                task_id=task_id,
                user_id=user_id,
                db_path=self.db_path,
            )
            await post_agent_message(
                client=client,
                channel=channel,
                role="warden",
                text=f"❌ *Command Denied* by `{user}`. Skipping restricted operation.",
                thread_ts=thread_ts,
            )
            self.resolve_gate(
                f"policy_{task_id}",
                {"action": "deny_command", "approved": False, "user": user},
            )

        # 4. Action: Approve Draft PR
        @self.app.action("approve_draft_pr")
        async def handle_approve_draft_pr(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

            # Mutate message in-place to remove buttons
            await self.mutate_message_in_place(
                client=client,
                channel=channel,
                body=body,
                action_id="approve_draft_pr",
                user_id=user_id or user,
            )

            record_slack_event(
                event_type="draft_pr_approved",
                payload={"task_id": task_id, "approved_by": user},
                task_id=task_id,
                user_id=user_id,
                db_path=self.db_path,
            )
            await post_agent_message(
                client=client,
                channel=channel,
                role="orchestrator",
                text=f"🚀 *Draft PR Authorized* by `{user}`. Preparing branch push and GitHub Draft PR delivery.",
                thread_ts=thread_ts,
            )
            self.resolve_gate(
                f"pr_{task_id}",
                {"action": "approve_draft_pr", "approved": True, "user": user},
            )

        @self.app.action("request_revision")
        async def handle_request_revision(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

            # Mutate message in-place to remove buttons
            await self.mutate_message_in_place(
                client=client,
                channel=channel,
                body=body,
                action_id="request_revision",
                user_id=user_id or user,
            )

            record_slack_event(
                event_type="revision_requested",
                payload={"task_id": task_id, "requested_by": user},
                task_id=task_id,
                user_id=user_id,
                db_path=self.db_path,
            )
            await post_agent_message(
                client=client,
                channel=channel,
                role="orchestrator",
                text=f"💬 *Revision Requested* by `{user}`. Please leave steering notes in this thread.",
                thread_ts=thread_ts,
            )
            self.resolve_gate(
                f"pr_{task_id}",
                {"action": "request_revision", "approved": False, "user": user},
            )

        # 5. Action: Approve Spec & Acceptance Criteria
        @self.app.action("approve_spec")
        async def handle_approve_spec(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

            # Mutate message in-place to remove buttons
            await self.mutate_message_in_place(
                client=client,
                channel=channel,
                body=body,
                action_id="approve_spec",
                user_id=user_id or user,
            )

            # Retrieve task and living spec
            task = get_task(task_id, db_path=self.db_path) or {}
            issue_id = task.get("issue_id", "")
            living_spec = get_living_spec(task_id, db_path=self.db_path)

            clean_title = (
                (task.get("issue_id") or "Feature").replace("[Draft]", "").strip()
            )
            spec_data = self.mira.synthesize_acceptance_spec(
                task_id=task_id,
                issue_id=issue_id,
                title=clean_title,
                living_spec=living_spec or LivingSpec(task_id=task_id, updated_at=""),
            )
            spec_md = self.mira.format_github_issue_body(
                spec_data, war_room_channel=channel, thread_ts=thread_ts
            )

            # Update GitHub issue and labels if poller configured
            if self.poller and issue_id.startswith("issue-"):
                try:
                    issue_num = int(issue_id.replace("issue-", ""))
                    self.poller.update_issue_spec(
                        issue_num, title=spec_data.title, body=spec_md
                    )
                    self.poller.mark_ready_for_dev(issue_num)
                except Exception as e:
                    logger.warning(
                        "Failed to update GitHub issue for %s: %s", task_id, e
                    )

            update_task_status(task_id, "READY_FOR_DEV", db_path=self.db_path)
            record_slack_event(
                event_type="spec_approved",
                payload={"task_id": task_id, "approved_by": user, "issue_id": issue_id},
                task_id=task_id,
                user_id=user_id,
                db_path=self.db_path,
            )

            await post_agent_message(
                client=client,
                channel=channel,
                role="mira",
                text=(
                    f"✅ *Requirements Finalized & Approved* by `{user}`!\n"
                    f"• GitHub issue `{issue_id}` updated with clean markdown specification.\n"
                    f"• Transitioned label to `status:ready-for-dev`.\n"
                    f"• Handing off to *Arjun (Lead Architect)* for architectural design."
                ),
                thread_ts=thread_ts,
            )

            await post_agent_message(
                client=client,
                channel=channel,
                role="arjun",
                text=(
                    f"📐 *Arjun (Lead Architect)* taking over task `{task_id}`.\n"
                    f"• Reviewing agreed requirements from living spec.\n"
                    f"• System architecture design & task decomposition in progress."
                ),
                thread_ts=thread_ts,
            )
            self.resolve_gate(
                f"spec_{task_id}",
                {
                    "action": "approve_spec",
                    "approved": True,
                    "user": user,
                    "spec": spec_data.model_dump(),
                },
            )

        @self.app.action("request_spec_revision")
        async def handle_request_spec_revision(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

            await self.mutate_message_in_place(
                client=client,
                channel=channel,
                body=body,
                action_id="request_spec_revision",
                user_id=user_id or user,
            )

            record_slack_event(
                event_type="spec_revision_requested",
                payload={"task_id": task_id, "requested_by": user},
                task_id=task_id,
                user_id=user_id,
                db_path=self.db_path,
            )
            await post_agent_message(
                client=client,
                channel=channel,
                role="mira",
                text=f"💬 *Spec Revision Requested* by `{user}`. Please leave your steering notes or additional edge cases in this thread.",
                thread_ts=thread_ts,
            )
            self.resolve_gate(
                f"spec_{task_id}",
                {"action": "request_spec_revision", "approved": False, "user": user},
            )

        # 6. Slash Command: /agent
        @self.app.command("/agent")
        async def handle_agent_command(ack, body, client):
            await ack()
            text = body.get("text", "").strip()
            user_id = body.get("user_id", "")
            user_name = body.get("user_name", "Engineer")
            channel_id = body.get("channel_id") or self.get_channel("triage")
            await self.trigger_intake_flow(
                client=client,
                channel_id=channel_id,
                user_id=user_id,
                user_name=user_name,
                request_text=text,
            )

        # 7. Message Event: Thread steering & grilling listener
        @self.app.event("message")
        async def handle_thread_message(event, client):
            if event.get("bot_id") or event.get("subtype") == "bot_message":
                return

            thread_ts = event.get("thread_ts")
            text = event.get("text", "").strip()
            user_id = event.get("user", "")

            if not text:
                return

            if not thread_ts:
                # If message contains an @mention, app_mention handler already handles it
                if re.search(r"<@[A-Z0-9]+>", text):
                    return
                clean = text.strip()
                lower = clean.lower()
                if lower.startswith(("solve ", "task:", "!agent ", "/agent ")):
                    await self.trigger_intake_flow(
                        client=client,
                        channel_id=event.get("channel", self.default_channel),
                        user_id=user_id,
                        user_name=f"User_{user_id}",
                        request_text=clean,
                        existing_thread_ts=event.get("ts"),
                    )
                return

            rehydrated = self.session_manager.rehydrate(thread_ts)
            task_data = (
                rehydrated.task
                if rehydrated and rehydrated.task
                else (get_task_by_thread_ts(thread_ts, db_path=self.db_path) or {})
            )
            task_id = task_data.get("task_id", thread_ts)
            active_phase = str(task_data.get("status", "running")).lower()

            # Handle interactive intake grilling if in INTAKE or GRILLING phase
            if active_phase in ("intake", "grilling"):
                living_spec = get_living_spec(
                    task_id, db_path=self.db_path
                ) or LivingSpec(
                    task_id=task_id,
                    requirements=[],
                    updated_at=datetime.now(UTC).isoformat(),
                )
                title = task_data.get("issue_id", "Feature")
                mira_reply, is_complete, spec_data = await self.mira.process_reply(
                    task_id=task_id,
                    issue_id=task_data.get("issue_id", ""),
                    title=title,
                    user_reply=text,
                    current_spec=living_spec,
                )

                if is_complete and spec_data:
                    update_task_status(task_id, "GRILLING", db_path=self.db_path)
                    blocks = build_spec_acceptance_blocks(
                        task_id=task_id,
                        issue_id=task_data.get("issue_id", ""),
                        title=title,
                        summary=spec_data.summary,
                        acceptance_criteria=spec_data.acceptance_criteria,
                        scope_and_boundaries=spec_data.scope_and_boundaries,
                        out_of_scope=spec_data.out_of_scope,
                    )
                    await post_agent_message(
                        client=client,
                        channel=event.get("channel"),
                        role="mira",
                        text=mira_reply,
                        thread_ts=thread_ts,
                        blocks=blocks,
                    )
                else:
                    await post_agent_message(
                        client=client,
                        channel=event.get("channel"),
                        role="mira",
                        text=mira_reply,
                        thread_ts=thread_ts,
                    )
                return

            # Default: Thread steering for ongoing tasks
            target_persona = resolve_target_persona(text, active_phase=active_phase)
            persona_info = get_persona(target_persona)

            if task_id not in self.steering_queues:
                self.steering_queues[task_id] = asyncio.Queue()
            await self.steering_queues[task_id].put(text)

            record_slack_event(
                event_type="thread_steering",
                payload={
                    "task_id": task_id,
                    "text": text,
                    "thread_ts": thread_ts,
                    "persona": target_persona,
                },
                task_id=task_id,
                user_id=user_id,
                db_path=self.db_path,
            )

            try:
                await client.reactions_add(
                    channel=event.get("channel"),
                    name="brain",
                    timestamp=event.get("ts"),
                )
            except Exception as e:
                logger.debug("Failed to add reaction to user message: %s", e)

            spec_suffix = (
                f" [Living Spec v{rehydrated.living_spec.version} active]"
                if rehydrated and rehydrated.living_spec
                else ""
            )
            await post_agent_message(
                client=client,
                channel=event.get("channel"),
                role=target_persona,
                text=(
                    f"{persona_info['icon_emoji']} *{persona_info['username']}:* "
                    f"Acknowledged guidance from <@{user_id}>{spec_suffix}:\n"
                    f'> *"{text}"*\n'
                    f"Enqueued as active direction."
                ),
                thread_ts=thread_ts,
            )

        # 8. App Mention Event: Top-level mentions or thread mentions
        @self.app.event("app_mention")
        async def handle_app_mention(event, client):
            user_id = event.get("user", "")
            text = event.get("text", "")
            channel = event.get("channel", self.default_channel)
            thread_ts = event.get("thread_ts")

            try:
                await client.reactions_add(
                    channel=channel,
                    name="rocket",
                    timestamp=event.get("ts"),
                )
            except Exception as e:
                logger.debug("Failed to add rocket reaction: %s", e)

            clean_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

            # If inside an existing thread, delegate to thread message handler
            if thread_ts:
                event["text"] = clean_text
                await handle_thread_message(event, client)
                return

            if not clean_text:
                await post_agent_message(
                    client=client,
                    channel=channel,
                    role="mira",
                    text=(
                        f"👋 Hi <@{user_id}>! I'm **Mira** (Product Specifier).\n"
                        f"Tell me what feature you'd like to build, e.g.:\n"
                        f"> `@antigravity_orchestra solve Implement token bucket rate limiter`\n"
                        f"And I'll grill out edge cases and acceptance criteria before handing off to Arjun & Kabir."
                    ),
                    thread_ts=event.get("ts"),
                )
                return

            # Top-level mention with a task request -> start live intake & grilling flow!
            try:
                await self.trigger_intake_flow(
                    client=client,
                    channel_id=channel,
                    user_id=user_id,
                    user_name=f"User_{user_id}",
                    request_text=clean_text,
                    existing_thread_ts=event.get("ts"),
                )
            except Exception as e:
                logger.exception("Failed to trigger intake flow: %s", e)
                await post_agent_message(
                    client=client,
                    channel=channel,
                    role="mira",
                    text=f"⚠️ Failed to initialize task intake: `{e}`",
                    thread_ts=event.get("ts"),
                )

    def register_gate(self, gate_id: str) -> asyncio.Event:
        """Register an async event gate for pausing execution until button is clicked."""
        event = asyncio.Event()
        self.active_gates[gate_id] = event
        self.gate_decisions.pop(gate_id, None)
        return event

    def resolve_gate(self, gate_id: str, decision: dict[str, Any]) -> None:
        """Resolves an active gate, unblocking waiting coroutines."""
        self.gate_decisions[gate_id] = decision
        event = self.active_gates.get(gate_id)
        if event:
            event.set()

    async def wait_for_decision(
        self, gate_id: str, timeout: float | None = None
    ) -> dict[str, Any] | None:
        """Wait until human engineer resolves the gate in Slack."""
        event = self.active_gates.get(gate_id)
        if not event:
            event = self.register_gate(gate_id)

        try:
            if timeout:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            else:
                await event.wait()
            return self.gate_decisions.get(gate_id)
        except TimeoutError:
            return None

    def rehydrate_session(self, identifier: str) -> RehydratedContext | None:
        """Reconstitutes task and conversation context from task_id or slack_thread_ts."""
        return self.session_manager.rehydrate(identifier)

    async def get_steering_instruction(self, task_id: str) -> str | None:
        """Check for pending steering context from human thread replies."""
        queue = self.steering_queues.get(task_id)
        if queue and not queue.empty():
            return await queue.get()
        return None

    async def post_agent(
        self,
        role: str,
        text: str,
        channel: str | None = None,
        thread_ts: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Dispatch persona message through the Slack client."""
        target_channel = channel or self.default_channel
        if not target_channel:
            raise ValueError("No Slack channel specified or configured")

        if self.app and self.app.client:
            return await post_agent_message(
                client=self.app.client,
                channel=target_channel,
                role=role,
                text=text,
                thread_ts=thread_ts,
                blocks=blocks,
            )
        raise RuntimeError("Slack app client is not initialized")

    async def start(self) -> None:
        """Starts the Socket Mode background listener."""
        if not self.bot_token or not self.app_token:
            raise ValueError(
                "SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set to start Socket Mode"
            )

        if not self.handler:
            self.handler = AsyncSocketModeHandler(self.app, self.app_token)

        self.is_connected = True
        logger.info("Connecting Slack Socket Mode handler...")
        await self.handler.start_async()

    async def stop(self) -> None:
        """Gracefully disconnects Socket Mode."""
        if self.handler:
            await self.handler.close_async()
            self.is_connected = False
            logger.info("Slack Socket Mode disconnected.")
