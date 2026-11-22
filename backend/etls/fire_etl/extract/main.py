"""
Extract: obtains the input data of the ETL.

Reads the NASA FIRMS CSV files (local files and/or already loaded DataFrames) and the previous
state of the pipeline (the GeoParquet outputs of the previous run), which is used to keep the
``event_id`` values and avoid duplicating detections between runs.

The download of the files lives in :mod:`fire_etl.extract.firms.archive` and
:mod:`fire_etl.extract.firms.nrt`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

import pandas as pd

from fire_etl.config import PipelineConfig
from fire_etl.extract.firms.reader import read_firms_csv
from fire_etl.load.geoparquet import read_previous_state
from fire_etl.transform.processing.events import PreviousState


@dataclass(frozen=True)
class ExtractResult:
    """
    Input data of the ETL.

    Attributes
    ----------
    frames : Iterable[pd.DataFrame]
        Raw FIRMS tables (MODIS and/or VIIRS). They are read lazily: they can only be
        traversed once.
    previous : PreviousState | None
        State of the previous run, or ``None`` if there is none or it is disabled.
    """

    frames: Iterable[pd.DataFrame]
    previous: PreviousState | None


def extract(
    config: PipelineConfig,
    input_paths: Sequence[str | Path] = (),
    frames: Iterable[pd.DataFrame] = (),
    output_dir: str | Path | None = None,
) -> ExtractResult:
    """
    Gathers the input FIRMS tables and the previous state of the pipeline.

    Parameters
    ----------
    config : PipelineConfig
        Pipeline configuration (``output.use_previous_state`` and ``output.directory``).
    input_paths : Sequence[str | Path]
        FIRMS MODIS and/or VIIRS files (``.csv`` or ``.txt``).
    frames : Iterable[pd.DataFrame]
        Already loaded FIRMS tables (as returned by
        :func:`fire_etl.extract.firms.reader.read_firms_csv` or by ``pd.read_csv``).
    output_dir : str | Path | None
        Directory of the previous outputs; defaults to ``config.output.directory``.

    Returns
    -------
    ExtractResult
        FIRMS tables (``frames`` followed by those read from ``input_paths``) and previous state.
    """
    directory = Path(output_dir or config.output.directory)
    previous = read_previous_state(directory) if config.output.use_previous_state else None
    raw_frames = chain(frames, (read_firms_csv(path) for path in input_paths))

    return ExtractResult(frames=raw_frames, previous=previous)
