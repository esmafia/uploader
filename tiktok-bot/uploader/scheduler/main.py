"""Scheduler service entrypoint.

Runs inside its own container (see scheduler/Dockerfile). Reads the same
SQLite DB as the API, picks up due ScheduledUpload rows, and executes uploads
through the shared tiktok_uploader package. Serial by design
(SCHEDULER_CONCURRENCY=1 in env).
"""
from __future__ import annotations

import logging
import os

from api.db import init_db
from scheduler import worker
from tiktok_uploader.Config import Config


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config_path = os.getenv("TIKTOK_CONFIG_PATH", "./config.txt")
    if os.path.exists(config_path):
        Config.load(config_path)

    init_db()
    poll = float(os.getenv("SCHEDULER_POLL_SECONDS", "30"))
    worker.loop(poll_interval=poll)


if __name__ == "__main__":
    main()
