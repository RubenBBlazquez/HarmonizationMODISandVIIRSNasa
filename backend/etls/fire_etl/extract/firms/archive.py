"""
Download of the NASA FIRMS annual archive (MODIS and VIIRS) for a country.

Uses the public archive ``<base_url>/<source>/<year>/<source>_<year>_<country>.csv``: it needs
no account or token and each CSV already comes clipped to Spain. Downloads are idempotent (an
already downloaded file is not requested again) and atomic (written to ``.part`` and renamed
when finished).

The current year is not in the archive yet; for recent data use the NRT files or the API (see
README).
"""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fire_etl.config import DownloadConfig

LOGGER = logging.getLogger(__name__)

USER_AGENT = "fire-etl"
CHUNK_BYTES = 1 << 20
CSV_START = b"latitude,"
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR = 500
DOWNLOADED, SKIPPED, MISSING = "downloaded", "skipped", "missing"


class UnexpectedContentError(RuntimeError):
    """The server answered with something that is not a FIRMS CSV (e.g. a login page)."""


@dataclass(frozen=True)
class DownloadItem:
    """
    An annual file to download.

    Attributes
    ----------
    source : str
        FIRMS source (``modis``, ``viirs-snpp``, ``viirs-jpss1``).
    year : int
        Year.
    url : str
        Full URL.
    path : Path
        Local destination.
    """

    source: str
    year: int
    url: str
    path: Path


@dataclass
class DownloadResult:
    """Download result, by category."""

    downloaded: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def available(self) -> list[Path]:
        """Files on disk after the download (new and already existing)."""
        return sorted([*self.downloaded, *self.skipped])


def last_complete_year(today: datetime | None = None) -> int:
    """Last complete year: the current year is not in the FIRMS archive yet."""
    return (today or datetime.now(timezone.utc)).year - 1


def build_url(config: DownloadConfig, source: str, year: int) -> str:
    """URL of the annual CSV of a source."""
    base = config.base_url.rstrip("/")

    return f"{base}/{source}/{year}/{source}_{year}_{config.country}.csv"


def plan_downloads(
    config: DownloadConfig, directory: str | Path | None = None, today: datetime | None = None
) -> list[DownloadItem]:
    """
    Lists the files to download: each source from its first year with data.

    Parameters
    ----------
    config : DownloadConfig
        Sources, year range and country.
    directory : str | Path | None
        Destination folder; defaults to ``config.directory``.
    today : datetime | None
        Reference date for the last complete year (tests only).

    Returns
    -------
    list[DownloadItem]
        Sorted by source and year.
    """
    target = Path(directory or config.directory)
    last_complete = last_complete_year(today)
    end_year = last_complete if config.end_year is None else min(config.end_year, last_complete)
    if config.end_year is not None and config.end_year > last_complete:
        LOGGER.warning(
            "download.end_year=%s exceeds the last complete year (%s): the annual archive does "
            "not have the current year (it is covered by the NRT).",
            config.end_year,
            last_complete,
        )
    items = []
    for source, first_year in config.sources.items():
        for year in range(max(config.start_year, first_year), end_year + 1):
            name = f"{source}_{year}_{config.country}.csv"
            url = build_url(config, source, year)
            items.append(DownloadItem(source, year, url, target / name))

    return items


def fetch_to_file(
    url: str,
    destination: Path,
    timeout_s: float,
    headers: dict[str, str] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> str:
    """
    Downloads a FIRMS CSV atomically (``.part`` + rename).

    Parameters
    ----------
    url : str
        File address.
    destination : Path
        Final file.
    timeout_s : float
        Maximum request time.
    headers : dict[str, str] | None
        Extra headers (e.g. ``Authorization``).
    opener : urllib.request.OpenerDirector | None
        Alternative HTTP client (e.g. one that does not follow redirects).

    Returns
    -------
    str
        ``DOWNLOADED`` or ``MISSING`` (404).

    Raises
    ------
    UnexpectedContentError
        If the response is not a FIRMS CSV (e.g. a login page).
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    open_url = opener.open if opener is not None else urllib.request.urlopen
    partial = destination.with_name(destination.name + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open_url(request, timeout=timeout_s) as response:
            first = response.read(CHUNK_BYTES)
            if not first.startswith(CSV_START):
                raise UnexpectedContentError(f"{url} does not return a FIRMS CSV")

            with partial.open("wb") as handle:
                handle.write(first)
                while chunk := response.read(CHUNK_BYTES):
                    handle.write(chunk)
    except urllib.error.HTTPError as error:
        partial.unlink(missing_ok=True)
        if error.code == HTTP_NOT_FOUND:
            return MISSING

        raise
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    os.replace(partial, destination)

    return DOWNLOADED


def _fetch(item: DownloadItem, config: DownloadConfig) -> str:
    """Downloads an annual file atomically."""
    return fetch_to_file(item.url, item.path, config.timeout_s)


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= HTTP_SERVER_ERROR

    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))


def download_item(item: DownloadItem, config: DownloadConfig) -> str:
    """
    Downloads a file with retries. A 404 is not retried (the year does not exist).

    Returns
    -------
    str
        ``downloaded``, ``skipped`` (already existed) or ``missing`` (404).

    Raises
    ------
    Exception
        The last error if the retries are exhausted.
    """
    if item.path.exists() and item.path.stat().st_size > 0:
        return SKIPPED

    def action() -> str:
        return _fetch(item, config)

    return with_retries(action, config.retries, config.retry_wait_s, item.url)


def with_retries(action: Callable[[], str], retries: int, wait_s: float, label: str) -> str:
    """
    Runs ``action`` retrying network or server errors (not the others).

    Raises
    ------
    Exception
        The last error if the retries are exhausted, or the first non-retryable one.
    """
    for attempt in range(1, retries + 1):
        try:
            return action()
        except Exception as error:
            if attempt == retries or not _is_retryable(error):
                raise

            LOGGER.warning("Retry %s/%s of %s: %s", attempt, retries, label, error)
            time.sleep(wait_s * attempt)

    raise AssertionError("unreachable")


def download_all(
    config: DownloadConfig, directory: str | Path | None = None, today: datetime | None = None
) -> DownloadResult:
    """
    Downloads all missing annual files (MODIS and VIIRS for Spain).

    A failure in one file does not stop the rest: it is left in ``failed`` with its reason.

    Parameters
    ----------
    config : DownloadConfig
        Sources, year range and country.
    directory : str | Path | None
        Destination folder; defaults to ``config.directory``.
    today : datetime | None
        Reference date for the last complete year (tests only).

    Returns
    -------
    DownloadResult
        Downloaded files, already existing ones, ones missing on the server and failed ones.
    """
    result = DownloadResult()
    plan = plan_downloads(config, directory, today)
    missing_files = sum(1 for item in plan if not item.path.exists())
    LOGGER.info(
        "FIRMS annual archive: %s files expected, %s to download.%s",
        len(plan),
        missing_files,
        " The first time takes a few minutes." if missing_files > 10 else "",
    )
    for item in plan:
        try:
            status = download_item(item, config)
        except Exception as error:  # a failed file does not stop the rest
            LOGGER.error("Could not download %s: %s", item.url, error)
            result.failed[item.url] = str(error)
            continue

        if status == MISSING:
            LOGGER.warning("Not in the archive: %s", item.url)
            result.missing.append(item.url)
        elif status == SKIPPED:
            result.skipped.append(item.path)
        else:
            LOGGER.info("Downloaded %s", item.path.name)
            result.downloaded.append(item.path)

    return result
