"""Parlameme — entry point."""

import logging
import os
import sys


def setup_logging():
    """Configure structured logging."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    fmt = "%(asctime)s %(levelname)-8s %(name)-30s %(message)s"
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=fmt,
        stream=sys.stderr,
    )
    # Quieten noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("nicegui").setLevel(logging.WARNING)


if __name__ == "__main__":
    setup_logging()
    from server.app import main

    main()
