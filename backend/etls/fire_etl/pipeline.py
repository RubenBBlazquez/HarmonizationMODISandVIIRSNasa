"""
Orquestación del pipeline: ingesta -> geometrías -> observaciones -> eventos -> salidas.

Uso por línea de comandos::

    python -m fire_etl.pipeline --config config/default.yaml \\
        --input data/sample/MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt \\
        --input data/sample/J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt \\
        --output-dir output
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path

import pandas as pd

from fire_etl.clean_data import build_clean_data, write_clean_data
from fire_etl.config import PipelineConfig, load_config
from fire_etl.events import PreviousState, build_events
from fire_etl.fusion import assign_time_windows, build_observations
from fire_etl.geometry import build_detection_geometries
from fire_etl.ingest import IngestResult, ingest_frames, read_firms_csv
from fire_etl.outputs import PipelineTables, read_previous_state, write_tables
from fire_etl.postgres_loader import load_to_postgres
from fire_etl.sigpac_land_cover import enrich, log_summary

LOGGER = logging.getLogger(__name__)


class NoDetectionsError(ValueError):
    """No quedan detecciones válidas que procesar."""


@contextmanager
def log_stage(name: str) -> Iterator[None]:
    """Registra el inicio y el fin (con el tiempo) de una etapa lenta del pipeline."""
    LOGGER.info("Iniciando: %s...", name)
    started = time.perf_counter()
    yield
    LOGGER.info("Hecho: %s (%.1f s)", name, time.perf_counter() - started)


def merge_with_previous(new: pd.DataFrame, previous: PreviousState | None) -> pd.DataFrame:
    """
    Une las detecciones nuevas con las ya ingeridas, sin duplicar ``detection_id``.

    Parameters
    ----------
    new : pd.DataFrame
        Detecciones de esta ejecución.
    previous : PreviousState | None
        Estado previo.

    Returns
    -------
    pd.DataFrame
        Detecciones únicas ordenadas por ``acq_datetime`` y ``detection_id``.
    """
    if previous is None or previous.detections.empty:
        return new

    combined = pd.concat([previous.detections, new], ignore_index=True)
    combined = combined.drop_duplicates("detection_id", keep="first")

    return combined.sort_values(["acq_datetime", "detection_id"], ignore_index=True)


def process_detections(
    ingest: IngestResult, config: PipelineConfig, previous: PreviousState | None = None
) -> PipelineTables:
    """
    Ejecuta el procesamiento completo sobre detecciones ya ingeridas (sin E/S).

    Parameters
    ----------
    ingest : IngestResult
        Resultado de la ingesta.
    config : PipelineConfig
        Configuración del pipeline.
    previous : PreviousState | None
        Estado previo para conservar ``event_id`` y deduplicar entre ejecuciones.

    Returns
    -------
    PipelineTables
        Tablas finales en el CRS métrico.

    Raises
    ------
    NoDetectionsError
        Si no hay detecciones válidas dentro del área de interés.
    """
    detections = merge_with_previous(ingest.detections, previous)
    with log_stage(f"geometrías y filtro de zona ({len(detections):,} detecciones)"):
        geometries = build_detection_geometries(
            detections, config.geometry, config.area_of_interest
        )

    if geometries.detections.empty:
        raise NoDetectionsError("No hay detecciones válidas dentro del área de interés")

    located = geometries.detections
    if config.land_cover.sigpac_dir:
        located, land_cover_summary = enrich(
            located,
            Path(config.land_cover.sigpac_dir),
            sigpac_confidence=config.land_cover.sigpac_confidence,
            min_confidence=config.land_cover.min_confidence,
        )
        log_summary(land_cover_summary)
    else:
        LOGGER.warning(
            "Cobertura del suelo desactivada (land_cover.sigpac_dir sin definir): land_cover, "
            "land_cover_confidence, sigpac_uso, is_agricultural e is_forest saldrán vacíos."
        )

    is_valid = located["is_valid"].astype(bool)
    if not is_valid.any():
        raise NoDetectionsError("Ninguna detección es válida (todas marcadas con skip_reason)")

    with log_stage(f"observaciones ({int(is_valid.sum()):,} detecciones válidas)"):
        windowed = assign_time_windows(located[is_valid], config.windows.mode)
        observations = build_observations(windowed, config.confidence)

    with log_stage("eventos (enlace entre ventanas, energía y biomasa)"):
        events = build_events(observations, config, previous)

    # Las no válidas se conservan en la tabla de detecciones, sin observación ni evento.
    skipped = located[~is_valid].assign(observation_id=None, is_representative=False)
    all_detections = pd.concat([observations.detections, skipped], ignore_index=True)
    all_detections = all_detections.sort_values(
        ["acq_datetime", "detection_id"], ignore_index=True
    )

    discarded = ingest.discarded
    if geometries.outside_aoi:
        outside = pd.DataFrame(
            {"source": ["ALL"], "reason": ["outside_aoi"], "count": [geometries.outside_aoi]}
        )
        discarded = pd.concat([discarded, outside], ignore_index=True)

    run_parameters = {
        "parameters": config.algorithm_parameters(),
        "config": config.to_dict(),
        "metric_crs_resolved": geometries.metric_crs.to_string(),
        "counts": {
            "detections": len(all_detections),
            "skipped_detections": int((~is_valid).sum()),
            "observations": len(events.observations),
            "fire_events": len(events.events),
            "event_merges": len(events.merges),
            "discarded": int(discarded["count"].sum()) if not discarded.empty else 0,
        },
    }

    return PipelineTables(
        detections=all_detections,
        observations=events.observations,
        events=events.events,
        event_detections=events.event_detections,
        merges=events.merges,
        discarded=discarded,
        run_parameters=run_parameters,
    )


def run_frames(
    frames: Iterable[pd.DataFrame],
    config: PipelineConfig | None = None,
    output_dir: str | Path | None = None,
    clean_dir: str | Path | None = None,
) -> PipelineTables:
    """
    Procesa DataFrames FIRMS ya cargados y escribe las salidas.

    Es la vía más directa: basta con tener los DataFrames (MODIS y/o VIIRS, tal cual salen de
    :func:`fire_etl.ingest.read_firms_csv` o de ``pd.read_csv``) y llamar a esta función.

    Parameters
    ----------
    frames : Iterable[pd.DataFrame]
        Tablas FIRMS crudas, en cualquier orden.
    config : PipelineConfig | None
        Configuración; por defecto los valores de ``config/default.yaml``.
    output_dir : str | Path | None
        Directorio de salida; por defecto ``config.output.directory``.
    clean_dir : str | Path | None
        Directorio de los datos finales (``clean_data``); por defecto
        ``config.output.clean_data_directory``.

    Returns
    -------
    PipelineTables
        Tablas escritas (``detections``, ``observations``, ``events``...).
    """
    config = config or PipelineConfig()
    directory = Path(output_dir or config.output.directory)
    previous = read_previous_state(directory) if config.output.use_previous_state else None

    with log_stage("ingesta y validación de los ficheros FIRMS"):
        ingest = ingest_frames(frames, config)

    tables = process_detections(ingest, config, previous)
    with log_stage("escritura de GeoParquet"):
        write_tables(tables, directory, config.geometry.storage_crs)

    LOGGER.info("Salidas escritas en %s: %s", directory, tables.run_parameters["counts"])

    clean_directory = Path(clean_dir or config.output.clean_data_directory)
    with log_stage("datos finales (clean_data)"):
        clean = build_clean_data(tables, config)
        write_clean_data(clean, clean_directory)
    LOGGER.info("Datos finales escritos en %s: %s", clean_directory, clean.metadata["counts"])

    # Después de los ficheros: el GeoParquet es el respaldo si la base de datos falla.
    load_to_postgres(tables, config)

    return tables


def run(
    input_paths: Sequence[str | Path],
    config: PipelineConfig,
    output_dir: str | Path | None = None,
    clean_dir: str | Path | None = None,
) -> PipelineTables:
    """
    Ingiere ficheros locales, procesa y escribe las salidas.

    Parameters
    ----------
    input_paths : Sequence[str | Path]
        Ficheros FIRMS MODIS y/o VIIRS.
    config : PipelineConfig
        Configuración del pipeline.
    output_dir : str | Path | None
        Directorio de salida; por defecto ``config.output.directory``.
    clean_dir : str | Path | None
        Directorio de los datos finales; por defecto ``config.output.clean_data_directory``.

    Returns
    -------
    PipelineTables
        Tablas escritas.
    """
    frames = (read_firms_csv(path) for path in input_paths)

    return run_frames(frames, config, output_dir, clean_dir)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fusión de detecciones MODIS + VIIRS (FIRMS)")
    parser.add_argument("--config", type=Path, default=None, help="YAML de configuración")
    parser.add_argument(
        "--input", type=Path, action="append", required=True, help="CSV FIRMS (repetible)"
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="Directorio de salida")
    parser.add_argument(
        "--no-previous-state", action="store_true", help="Ignora las salidas previas"
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
