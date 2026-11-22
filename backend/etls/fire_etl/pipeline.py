"""
ETL orchestration: extract -> transform -> load.

- :func:`fire_etl.extract.main.extract`: reads the FIRMS CSV files and the previous state.
- :func:`fire_etl.transform.main.transform`: standardization, geometries, observations, events
  and final data.
- :func:`fire_etl.load.main.load`: GeoParquet, ``clean_data`` and PostGIS.

Command-line usage::

    python -m fire_etl.pipeline --config config/default.yaml \\
        --input data/sample/MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt \\
        --input data/sample/J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt \\
        --output-dir output
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path

import pandas as pd

from fire_etl.config import PipelineConfig, load_config
from fire_etl.extract.main import ExtractResult, extract
from fire_etl.load.main import load
from fire_etl.transform.main import transform
from fire_etl.transform.tables import PipelineTables


def _run_etl(
    extracted: ExtractResult,
    config: PipelineConfig,
    output_dir: str | Path | None,
    clean_dir: str | Path | None,
) -> PipelineTables:
    result = transform(extracted.frames, config, extracted.previous)
    load(result.tables, result.clean, config, output_dir, clean_dir)

    return result.tables


def run_frames(
    frames: Iterable[pd.DataFrame],
    config: PipelineConfig | None = None,
    output_dir: str | Path | None = None,
    clean_dir: str | Path | None = None,
) -> PipelineTables:
    """
    Runs the ETL on already loaded FIRMS DataFrames and writes the outputs.

    This is the most direct route: it is enough to have the DataFrames (MODIS and/or VIIRS, as
    returned by :func:`fire_etl.extract.firms.reader.read_firms_csv` or by ``pd.read_csv``) and
    call this function.

    Parameters
    ----------
    frames : Iterable[pd.DataFrame]
        Raw FIRMS tables, in any order.
    config : PipelineConfig | None
        Configuration; defaults to the values of ``config/default.yaml``.
    output_dir : str | Path | None
        Output directory; defaults to ``config.output.directory``.
    clean_dir : str | Path | None
        Directory of the final data (``clean_data``); defaults to
        ``config.output.clean_data_directory``.

    Returns
    -------
    PipelineTables
        Tables written (``detections``, ``observations``, ``events``...).
    """
    config = config or PipelineConfig()
    extracted = extract(config, frames=frames, output_dir=output_dir)

    return _run_etl(extracted, config, output_dir, clean_dir)


def run(
    input_paths: Sequence[str | Path],
    config: PipelineConfig,
    output_dir: str | Path | None = None,
    clean_dir: str | Path | None = None,
) -> PipelineTables:
    """
    Runs the ETL on local FIRMS files and writes the outputs.

    Parameters
    ----------
    input_paths : Sequence[str | Path]
        FIRMS MODIS and/or VIIRS files.
    config : PipelineConfig
        Pipeline configuration.
    output_dir : str | Path | None
        Output directory; defaults to ``config.output.directory``.
    clean_dir : str | Path | None
        Directory of the final data; defaults to ``config.output.clean_data_directory``.

    Returns
    -------
    PipelineTables
        Tables written.
    """
    extracted = extract(config, input_paths=input_paths, output_dir=output_dir)

    return _run_etl(extracted, config, output_dir, clean_dir)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MODIS + VIIRS detection fusion (FIRMS)")
    parser.add_argument("--config", type=Path, default=None, help="YAML configuration file")
    parser.add_argument(
        "--input", type=Path, action="append", required=True, help="FIRMS CSV (repeatable)"
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory")
    parser.add_argument(
        "--no-previous-state", action="store_true", help="Ignore the previous outputs"
    )

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    config = load_config(args.config)
    if args.no_previous_state:
        config = replace(config, output=replace(config.output, use_previous_state=False))

    run(args.input, config, args.output_dir)


if __name__ == "__main__":
    main()
