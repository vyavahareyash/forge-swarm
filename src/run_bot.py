"""
Interactive Slack Socket Mode War-Room Daemon.

Runs the live bot listener in the background, responding to:
- Thread replies (steering context injection + Atlas acknowledgment + :brain: reaction)
- @mentions in #agent-ops
- Block Kit button approvals
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.slack.bot import SlackWarRoomBot
from src.storage.db import init_db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    load_dotenv()
    db_path = os.path.abspath("storage/state.db")
    init_db(db_path)

    bot_token = os.getenv("SLACK_BOT_TOKEN")
    app_token = os.getenv("SLACK_APP_TOKEN")
    channel_id = os.getenv("SLACK_CHANNEL_ID")

    if not (bot_token and app_token):
        logger.error("Missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN in .env")
        sys.exit(1)

    bot = SlackWarRoomBot(
        bot_token=bot_token,
        app_token=app_token,
        default_channel=channel_id,
        db_path=db_path,
    )

    logger.info("Starting live Slack War-Room Bot listener...")
    logger.info("Ready for messages in Slack (#agent-ops)!")

    # Start socket mode
    await bot.start()

    # Keep alive
    try:
        while True:
            await asyncio.sleep(1)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Stopping bot...")
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
