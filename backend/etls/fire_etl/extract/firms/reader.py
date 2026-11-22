"""
Reading of NASA FIRMS CSV files and sensor detection (MODIS or VIIRS).

The sensor is detected from the file columns (``brightness`` for MODIS, ``bright_ti4`` for
VIIRS), not from its name or extension: FIRMS publishes NRT files as ``.txt``.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = (
    "latitude",
    "longitude",
    "acq_date",
    "acq_time",
    "satellite",
    "confidence",
    "frp",
    "daynight",
)


class Source(StrEnum):
    """Sensor the detection comes from."""

    MODIS = "MODIS"
    VIIRS = "VIIRS"


class UnknownFirmsFormatError(ValueError):
    """The file does not have the FIRMS MODIS or VIIRS column format."""


def detect_source(columns: Iterable[str]) -> Source:
    """
    Detects the sensor from the file columns.

    Parameters
    ----------
    columns : Iterable[str]
        Column names (case-insensitive).

    Returns
    -------
    Source
        ``MODIS`` or ``VIIRS``.

    Raises
    ------
    UnknownFirmsFormatError
        If required columns are missing or the sensor is not recognized.
    """
    names = {column.strip().lower() for column in columns}
    missing = set(REQUIRED_COLUMNS) - names
    if missing:
        raise UnknownFirmsFormatError(f"Missing FIRMS columns: {sorted(missing)}")

    if "bright_ti4" in names:
        return Source.VIIRS

    if "brightness" in names:
        return Source.MODIS

    raise UnknownFirmsFormatError("Sensor not recognized (brightness/bright_ti4 missing)")


def read_firms_csv(path: str | Path) -> pd.DataFrame:
    """
    Reads a FIRMS CSV keeping the original values as text.

    Parameters
    ----------
    path : str | Path
        File path (``.csv`` or ``.txt``).

    Returns
    -------
    pd.DataFrame
        Lowercase columns, all values as text.
    """
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    frame.columns = [column.strip().lower() for column in frame.columns]

    return frame
