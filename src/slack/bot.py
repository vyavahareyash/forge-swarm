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
from typing import Any

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from src.slack.personas import post_agent_message
from src.storage.db import (
    extend_task_budget,
    get_task_by_thread_ts,
    record_slack_event,
    update_task_status,
)
from src.storage.session_manager import RehydratedContext, SessionManager

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
    ):
        load_dotenv()
        self.bot_token = bot_token or os.getenv("SLACK_BOT_TOKEN")
        self.app_token = app_token or os.getenv("SLACK_APP_TOKEN")
        self.default_channel = default_channel or os.getenv("SLACK_CHANNEL_ID")
        self.db_path = db_path
        self.session_manager = SessionManager(db_path=self.db_path)

        self.active_gates: dict[str, asyncio.Event] = {}
        self.gate_decisions: dict[str, dict[str, Any]] = {}
        self.steering_queues: dict[str, asyncio.Queue[str]] = {}

        self.app: AsyncApp | None = None
        self.handler: AsyncSocketModeHandler | None = None
        self.is_connected: bool = False

        if self.bot_token and self.app_token:
            self._init_bolt_app()


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

            # Extend in DB
            extend_task_budget(action_val, additional_tokens=50_000, additional_turns=5, db_path=self.db_path)
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

            self.resolve_gate(action_val, {"action": "grant_budget_50k", "approved": True, "user": user})

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

            self.resolve_gate(action_val, {"action": "abort_task", "approved": False, "user": user})

        # 3. Action: Approve / Deny Security Policy Commands
        @self.app.action("approve_command")
        async def handle_approve_command(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

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
            self.resolve_gate(f"policy_{task_id}", {"action": "approve_command", "approved": True, "user": user})

        @self.app.action("deny_command")
        async def handle_deny_command(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

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
            self.resolve_gate(f"policy_{task_id}", {"action": "deny_command", "approved": False, "user": user})

        # 4. Action: Approve Draft PR
        @self.app.action("approve_draft_pr")
        async def handle_approve_draft_pr(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

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
            self.resolve_gate(f"pr_{task_id}", {"action": "approve_draft_pr", "approved": True, "user": user})

        @self.app.action("request_revision")
        async def handle_request_revision(ack, body, client):
            await ack()
            task_id = body["actions"][0]["value"]
            user = body.get("user", {}).get("username", "Engineer")
            user_id = body.get("user", {}).get("id", "")
            channel = body.get("channel", {}).get("id", self.default_channel)
            thread_ts = body.get("container", {}).get("thread_ts")

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
            self.resolve_gate(f"pr_{task_id}", {"action": "request_revision", "approved": False, "user": user})

        # 5. Message Event: Thread steering listener
        @self.app.event("message")
        async def handle_thread_message(event, client):
            # Ignore bot messages and top-level channel root messages without thread_ts
            if event.get("bot_id") or event.get("subtype") == "bot_message":
                return

            thread_ts = event.get("thread_ts")
            text = event.get("text", "").strip()
            user_id = event.get("user", "")

            if not thread_ts or not text:
                return

            # Rehydrate task context from persistent storage
            rehydrated = self.session_manager.rehydrate(thread_ts)
            task_id = (
                rehydrated.task.get("task_id")
                if rehydrated and rehydrated.task
                else (get_task_by_thread_ts(thread_ts, db_path=self.db_path) or {}).get("task_id", thread_ts)
            )

            # Enqueue steering instruction
            if task_id not in self.steering_queues:
                self.steering_queues[task_id] = asyncio.Queue()
            await self.steering_queues[task_id].put(text)

            record_slack_event(
                event_type="thread_steering",
                payload={"task_id": task_id, "text": text, "thread_ts": thread_ts},
                task_id=task_id,
                user_id=user_id,
                db_path=self.db_path,
            )

            # Add acknowledgment reaction and persona reply
            try:
                await client.reactions_add(
                    channel=event.get("channel"),
                    name="brain",
                    timestamp=event.get("ts"),
                )
            except Exception as e:
                logger.debug("Failed to add reaction to user message: %s", e)

            # Post acknowledgment from Atlas in the thread
            spec_suffix = (
                f" [Living Spec v{rehydrated.living_spec.version} active]"
                if rehydrated and rehydrated.living_spec
                else ""
            )
            await post_agent_message(
                client=client,
                channel=event.get("channel"),
                role="orchestrator",
                text=f"🧭 *Atlas (Lead Architect):* Acknowledged steering guidance from <@{user_id}>{spec_suffix}:\n> *\"{text}\"*\nEnqueued as active direction for Forge and Sentinel.",
                thread_ts=thread_ts,
            )


        # 6. App Mention Event: Top-level mentions
        @self.app.event("app_mention")
        async def handle_app_mention(event, client):
            user_id = event.get("user", "")
            text = event.get("text", "")
            channel = event.get("channel", self.default_channel)
            thread_ts = event.get("thread_ts") or event.get("ts")

            await client.reactions_add(
                channel=channel,
                name="rocket",
                timestamp=event.get("ts"),
            )

            await post_agent_message(
                client=client,
                channel=channel,
                role="orchestrator",
                text=(
                    f"🤖 *Antigravity Dev Team War-Room Online*\n"
                    f"Hello <@{user_id}>! I received your mention: *\"{text}\"*\n"
                    f"• Type replies in this thread to steer the swarm.\n"
                    f"• Interactive Block Kit buttons appear automatically when human decisions or budget extensions are needed."
                ),
                thread_ts=thread_ts,
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

    async def wait_for_decision(self, gate_id: str, timeout: float | None = None) -> dict[str, Any] | None:
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
            raise ValueError("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set to start Socket Mode")

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
