"""
Load: writes the result of the transformation to its destinations.

1. GeoParquet with the complete tables: it is the pipeline state and the backup.
2. Final data (``clean_data``) as GeoJSON and JSON for the map and the chatbot.
3. PostGIS, if configured. It goes last: if the database fails, the files are already
   written and the error is only logged.

Loading the SIGPAC parcels into PostGIS is a separate process (:mod:`fire_etl.load.sigpac`).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fire_etl.config import PipelineConfig
from fire_etl.load.clean_data import write_clean_data
from fire_etl.load.geoparquet import write_tables
from fire_etl.load.postgres import load_to_postgres
from fire_etl.stage_log import log_stage
from fire_etl.transform.clean_data import CleanData
from fire_etl.transform.tables import PipelineTables

LOGGER = logging.getLogger(__name__)


def load(
    tables: PipelineTables,
    clean: CleanData,
    config: PipelineConfig,
    output_dir: str | Path | None = None,
    clean_dir: str | Path | None = None,
) -> None:
    """
    Writes the tables as GeoParquet, the final data and, if possible, to PostGIS.

    Parameters
    ----------
    tables : PipelineTables
        Complete tables in the metric CRS.
    clean : CleanData
        Final data for the map and the chatbot.
    config : PipelineConfig
        Pipeline configuration (storage CRS, directories and database).
    output_dir : str | Path | None
        Directory of the tables; defaults to ``config.output.directory``.
    clean_dir : str | Path | None
        Directory of the final data; defaults to ``config.output.clean_data_directory``.
    """
    directory = Path(output_dir or config.output.directory)
    with log_stage("writing GeoParquet"):
        write_tables(tables, directory, config.geometry.storage_crs)
    LOGGER.info("Outputs written to %s: %s", directory, tables.run_parameters["counts"])

    clean_directory = Path(clean_dir or config.output.clean_data_directory)
    with log_stage("writing final data (clean_data)"):
        write_clean_data(clean, clean_directory)
    LOGGER.info("Final data written to %s: %s", clean_directory, clean.metadata["counts"])

    # After the files: the GeoParquet is the backup if the database fails.
    load_to_postgres(tables, config)
