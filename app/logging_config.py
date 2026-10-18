from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stdout,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    # httpx logs full request URLs at INFO. The raw Bot API getFile URL contains
    # the bot token in its path, so avoid leaking credentials into Vultr logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
