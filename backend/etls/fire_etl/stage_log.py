"""Logging of the start and end (with timing) of the slow stages of the ETL."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

LOGGER = logging.getLogger(__name__)


@contextmanager
def log_stage(name: str) -> Iterator[None]:
    """
    Logs the start and end (with timing) of a slow pipeline stage.

    Parameters
    ----------
    name : str
        Description of the stage for the log.

    Yields
    ------
    None
        Runs the ``with`` block between the two messages.
    """
    LOGGER.info("Starting: %s...", name)
    started = time.perf_counter()
    yield
    LOGGER.info("Done: %s (%.1f s)", name, time.perf_counter() - started)
