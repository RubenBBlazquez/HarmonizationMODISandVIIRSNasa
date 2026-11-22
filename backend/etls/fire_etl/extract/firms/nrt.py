"""
Download of the NASA FIRMS near-real-time (NRT) files for the current year.

The annual archive per country (:mod:`fire_etl.extract.firms.archive`) only goes up to the last
complete year.
The NRT (``nrt3.modaps.eosdis.nasa.gov/archive/FIRMS/<source>/<region>/``) has one file per day
(``..._NRT_YYYYDDD.txt``, with the day of the year), but **only keeps the last ~60 days**: what
lies between the end of the annual archive and the start of the NRT is covered by neither source.

- The folder listing is public; **the files require an Earthdata token**
  (``Authorization: Bearer``), which is read from an environment variable and is never written
  to files or to the log.
- Redirects are not followed: if the token is invalid or has expired, the server redirects to
  the login page and the download stops, without sending the token to another server.
- The files of the last few days grow during the day, so they are always downloaded again;
  older ones, once downloaded, are not.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fire_etl.config import NrtConfig
from fire_etl.extract.firms.archive import (
    DOWNLOADED,
    MISSING,
    USER_AGENT,
    fetch_to_file,
    with_retries,
)

LOGGER = logging.getLogger(__name__)

FILE_NAME_PATTERN = re.compile(r'data-name="([^"/]+_(\d{4})(\d{3})\.txt)"')
REDIRECT_OR_DENIED = frozenset({301, 302, 303, 307, 308, 401, 403})
LISTING_TIMEOUT_S = 30
PROGRESS_EVERY = 25  # how many files between progress reports


class NrtAuthError(RuntimeError):
    """The Earthdata token is invalid or has expired (the server asks to log in)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Does not follow redirects, so the token is not forwarded to another server."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Returns ``None``: the redirect is treated as an HTTP error."""
        return None


@dataclass(frozen=True)
class NrtItem:
    """
    A daily NRT file.

    Attributes
    ----------
    source : str
        Sensor folder (``modis-c6.1``, ``noaa-20-viirs-c2``...).
    name : str
        File name.
    day : date
        UTC day it corresponds to (from the day of the year in the name).
    url : str
        Full address.
    path : Path
        Local destination.
    """

    source: str
    name: str
    day: date
    url: str
    path: Path


@dataclass
class NrtResult:
    """Result of the NRT download."""

    downloaded: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    token_missing: bool = False
    auth_failed: bool = False
    first_day: date | None = None
    last_day: date | None = None


def parse_listing(html: str) -> list[tuple[str, date]]:
    """
    ``..._YYYYDDD.txt`` files of a folder listing, with their day.

    Returns
    -------
    list[tuple[str, date]]
        ``(name, day)`` without duplicates, sorted by day.
    """
    found = {}
    for name, year, day_of_year in FILE_NAME_PATTERN.findall(html):
        try:
            found[name] = date(int(year), 1, 1) + timedelta(days=int(day_of_year) - 1)
        except ValueError:
            continue

    return sorted(found.items(), key=lambda entry: (entry[1], entry[0]))


def list_remote(config: NrtConfig, source: str) -> list[tuple[str, date]]:
    """Files available in the folder of a source (the listing is public)."""
    url = f"{config.base_url.rstrip('/')}/{source}/{config.region}/"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=LISTING_TIMEOUT_S) as response:
        return parse_listing(response.read().decode("utf-8", errors="replace"))


def first_uncovered_day(today: date | None = None) -> date:
    """First day not covered by the annual archive: January 1st of the current year."""
    reference = today or datetime.now(timezone.utc).date()

    return date(reference.year, 1, 1)


def plan_nrt(
    config: NrtConfig,
    remote: dict[str, list[tuple[str, date]]],
    directory: str | Path | None = None,
    today: date | None = None,
) -> list[NrtItem]:
    """
    NRT files to have on disk: those of the dates the annual archive does not cover.

    Parameters
    ----------
    config : NrtConfig
        Sources and region.
    remote : dict[str, list[tuple[str, date]]]
        Files available per source (from :func:`list_remote`).
    directory : str | Path | None
        Destination folder; defaults to ``config.directory``.
    today : date | None
        Reference date (tests only).

    Returns
    -------
    list[NrtItem]
        Sorted by source and day.
    """
    target = Path(directory or config.directory)
    first = first_uncovered_day(today)
    base = config.base_url.rstrip("/")
    items = []
    for source in config.sources:
        for name, day in remote.get(source, []):
            if day >= first:
                url = f"{base}/{source}/{config.region}/{name}"
                items.append(NrtItem(source, name, day, url, target / name))

    return items


def needs_download(item: NrtItem, refetch_days: int, today: date) -> bool:
    """``True`` if the file is missing or is one of the last few days (still growing)."""
    if not (item.path.exists() and item.path.stat().st_size > 0):
        return True

    return item.day >= today - timedelta(days=refetch_days)


def _fetch(item: NrtItem, config: NrtConfig, token: str) -> str:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        return fetch_to_file(
            item.url,
            item.path,
            config.timeout_s,
            headers={"Authorization": f"Bearer {token}"},
            opener=opener,
        )
    except urllib.error.HTTPError as error:
        if error.code in REDIRECT_OR_DENIED:
            raise NrtAuthError(
                f"The server rejected the token (HTTP {error.code}): expired or invalid."
            ) from error

        raise


def resolve_token(config: NrtConfig, base_dir: str | Path | None = None) -> str:
    """
    Earthdata token: the ``config.token_env`` environment variable and, if it does not exist, the
    local file ``config.token_file`` (first line that is neither empty nor starts with ``#``).

    Parameters
    ----------
    config : NrtConfig
        Variable name and file path.
    base_dir : str | Path | None
        Folder against which a relative ``token_file`` path is resolved.

    Returns
    -------
    str
        The token, or an empty string if there is none. It is never logged.
    """
    from_env = os.environ.get(config.token_env, "").strip()
    if from_env:
        return from_env

    path = Path(config.token_file)
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""

    for line in lines:
        value = line.strip()
        if value and not value.startswith("#"):
            return value

    return ""


def download_nrt(
    config: NrtConfig,
    directory: str | Path | None = None,
    today: date | None = None,
    base_dir: str | Path | None = None,
) -> NrtResult:
    """
    Downloads the missing NRT files (and those of the last few days, which are updated).

    The token comes from the environment variable or the local file (see :func:`resolve_token`);
    without it nothing is downloaded and a warning is shown. If the server rejects it, everything
    stops. A file that fails for another reason does not stop the rest.

    Parameters
    ----------
    config : NrtConfig
        Sources, region and token.
    directory : str | Path | None
        Destination folder; defaults to ``config.directory``.
    today : date | None
        Reference date (tests only).
    base_dir : str | Path | None
        Folder against which ``config.token_file`` is resolved if it is relative.

    Returns
    -------
    NrtResult
        Downloaded files, already existing ones, missing ones and failed ones.
    """
    result = NrtResult()
    if not config.enabled:
        return result

    token = resolve_token(config, base_dir)
    if not token:
        result.token_missing = True
        LOGGER.warning(
            "NRT skipped: set the Earthdata token in the environment variable %s or in the "
            "local file %s.",
            config.token_env,
            config.token_file,
        )
        return result

    reference = today or datetime.now(timezone.utc).date()
    remote: dict[str, list[tuple[str, date]]] = {}
    for source in config.sources:
        try:
            remote[source] = list_remote(config, source)
        except (urllib.error.URLError, TimeoutError) as error:
            LOGGER.error("Could not list %s/%s: %s", source, config.region, error)
            result.failed[f"{source}/{config.region}/"] = str(error)

    items = plan_nrt(config, remote, directory, reference)
    days = [item.day for item in items]
    result.first_day, result.last_day = (min(days), max(days)) if days else (None, None)

    pending = []
    for item in items:
        if needs_download(item, config.refetch_days, reference):
            pending.append(item)
        else:
            result.skipped.append(item.path)

    LOGGER.info(
        "NRT: %s files (%s to %s): %s to download, %s already on disk.%s",
        len(items),
        result.first_day,
        result.last_day,
        len(pending),
        len(result.skipped),
        " The first time takes a few minutes (~1 s per file)." if len(pending) > 50 else "",
    )

    for index, item in enumerate(pending, start=1):
        if index % PROGRESS_EVERY == 0:
            LOGGER.info("NRT: downloaded %s of %s files...", index - 1, len(pending))

        try:
            status = with_retries(
                lambda item=item: _fetch(item, config, token),
                config.retries,
                config.retry_wait_s,
                item.url,
            )
        except NrtAuthError as error:
            LOGGER.error("%s Stopping the NRT download.", error)
            result.auth_failed = True
            break
        except Exception as error:  # a failed file does not stop the rest
            LOGGER.error("Could not download %s: %s", item.url, error)
            result.failed[item.url] = str(error)
            continue

        if status == MISSING:
            result.missing.append(item.url)
        elif status == DOWNLOADED:
            result.downloaded.append(item.path)

    LOGGER.info(
        "NRT finished: %s downloaded, %s missing, %s failed.",
        len(result.downloaded),
        len(result.missing),
        len(result.failed),
    )

    return result


def nrt_files(directory: str | Path) -> list[Path]:
    """NRT files already on disk (even if this run could not download any)."""
    return sorted(Path(directory).glob("*.txt"))
