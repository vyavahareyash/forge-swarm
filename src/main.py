"""
Unified Autonomous Multi-Agent SWE Team Daemon.

Main daemon coordinating:
- Slack Socket Mode War-Room Bot (`SlackWarRoomBot`)
- GitHub Issue Poller (`GitHubPoller`)
- Sequential Task Queue Worker (`E2EPipeline`)
- Graceful lifecycle shutdown (SIGINT/SIGTERM)
"""
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.agy_runner import find_agy_binary
from src.pipeline import E2EPipeline
from src.slack.bot import SlackWarRoomBot
from src.storage.db import init_db
from src.triggers.github_poller import GitHubIssue, GitHubPoller

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("npc_daemon")


class AgentTeamDaemon:
    """Production daemon running Slack Socket Mode, GitHub Poller, and Task Pipeline."""

    def __init__(self, mode: str | None = None):
        load_dotenv()
        agy_bin = find_agy_binary()
        self.mode = mode or ("agy" if agy_bin else "standalone")
        self.db_path = os.path.abspath("storage/state.db")
        init_db(self.db_path)

        # Environment variables
        self.bot_token = os.getenv("SLACK_BOT_TOKEN")
        self.app_token = os.getenv("SLACK_APP_TOKEN")
        self.channel_id = os.getenv("SLACK_CHANNEL_ID")
        self.github_repo = os.getenv("GITHUB_REPO")
        self.target_repo_path = os.getenv("TARGET_REPO_PATH")

        # Core components
        self.bot: SlackWarRoomBot | None = None
        if self.bot_token and self.app_token:
            self.bot = SlackWarRoomBot(
                bot_token=self.bot_token,
                app_token=self.app_token,
                default_channel=self.channel_id,
                db_path=self.db_path,
            )

        self.poller: GitHubPoller | None = None
        if self.github_repo:
            self.poller = GitHubPoller(
                repo=self.github_repo,
                poll_interval=15.0,
                db_path=self.db_path,
            )

        self.pipeline = E2EPipeline(
            target_repo_path=self.target_repo_path,
            poller=self.poller,
            bot=self.bot,
            db_path=self.db_path,
            mode=self.mode,
        )

        self.task_queue: asyncio.Queue[GitHubIssue] = asyncio.Queue()
        self.is_running = False
        self._tasks: list[asyncio.Task] = []

    async def _queue_worker(self) -> None:
        """Processes issues sequentially to respect subscription budget quotas."""
        logger.info("Sequential task worker ready. Waiting for tasks...")
        while self.is_running:
            try:
                issue = await self.task_queue.get()
                logger.info("Dequeued issue #%s: %s", issue.number, issue.title)
                try:
                    res = await self.pipeline.execute_issue(issue)
                    logger.info("Finished issue #%s with status: %s", issue.number, res.status)
                except Exception as err:
                    logger.error("Unhandled error processing issue #%s: %s", issue.number, err, exc_info=True)
                finally:
                    self.task_queue.task_done()
            except asyncio.CancelledError:
                break

    async def _on_issue_detected(self, issue: GitHubIssue) -> None:
        """Callback invoked by poller when a new 'agent-ready' issue is found."""
        logger.info("Enqueuing agent-ready issue #%s into processing queue", issue.number)
        await self.task_queue.put(issue)

    async def start(self) -> None:
        """Starts all daemon background services."""
        self.is_running = True
        logger.info("=" * 65)
        logger.info("🤖 Antigravity Multi-Agent SWE Team Daemon Booting...")
        logger.info("Target Repository: %s", self.target_repo_path or "Default fixture")
        logger.info("GitHub Poller:     %s", self.github_repo or "Disabled (No GITHUB_REPO)")
        logger.info("Slack War-Room:    %s", self.channel_id or "Disabled (No SLACK_CHANNEL_ID)")
        logger.info("=" * 65)

        # 1. Start Slack Bot Socket Mode if enabled
        if self.bot:
            logger.info("Connecting Slack Socket Mode listener...")
            self._tasks.append(asyncio.create_task(self.bot.start()))

        # 2. Start Task Queue Worker
        self._tasks.append(asyncio.create_task(self._queue_worker()))

        # 3. Start GitHub Issue Poller loop if enabled
        if self.poller:
            logger.info("Starting GitHub Poller background loop...")
            self._tasks.append(
                asyncio.create_task(self.poller.poll_loop(self._on_issue_detected))
            )

        logger.info("Daemon active. Awaiting issues or Slack triggers.")

    async def stop(self) -> None:
        """Gracefully shuts down all services."""
        logger.info("Initiating daemon graceful shutdown...")
        self.is_running = False

        if self.poller:
            self.poller.stop()

        for t in self._tasks:
            t.cancel()

        if self.bot:
            await self.bot.stop()

        logger.info("Daemon cleanly stopped.")


async def main():
    daemon = AgentTeamDaemon()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Received interrupt signal.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    await daemon.start()
    await stop_event.wait()
    await daemon.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
