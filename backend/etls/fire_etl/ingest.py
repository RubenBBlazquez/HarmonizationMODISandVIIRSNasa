"""
Ingesta y normalización de CSV de NASA FIRMS (MODIS y VIIRS) a un esquema común.

El sensor se detecta por las columnas del fichero (``brightness`` para MODIS, ``bright_ti4``
para VIIRS), no por su nombre ni su extensión: FIRMS publica ficheros NRT como ``.txt``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd

from fire_etl.confidence import normalize_modis_confidence, normalize_viirs_confidence
from fire_etl.config import ExclusionZone, PipelineConfig, ValidationConfig
from fire_etl.frp_classification import CATEGORY_DISCARDED, CATEGORY_LOW, classify_frp

LOGGER = logging.getLogger(__name__)

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

# Columnas propias de cada sensor -> columnas del esquema común.
SENSOR_COLUMNS = {
    "MODIS": {"brightness": "brightness", "bright_t31": "brightness_2"},
    "VIIRS": {"bright_ti4": "brightness", "bright_ti5": "brightness_2"},
}

DETECTION_COLUMNS = (
    "detection_id",
    "source",
    "satellite",
    "acq_datetime",
    "acq_date",
    "lat",
    "lon",
    "area_width_km",
    "area_height_km",
    "frp",
    "biomass_kg_s",
    "frp_class",
    "frp_category",
    "is_valid",
    "skip_reason",
    "daynight",
    "confidence_raw",
    "confidence_norm",
    "brightness",
    "brightness_2",
    "version",
)

COORDINATE_DECIMALS = 5
BELOW_FRP_REASON = "below_frp_threshold"


class Source(StrEnum):
    """Sensor de origen de la detección."""

    MODIS = "MODIS"
    VIIRS = "VIIRS"


class UnknownFirmsFormatError(ValueError):
    """El fichero no tiene el formato de columnas de FIRMS MODIS o VIIRS."""


@dataclass(frozen=True)
class IngestResult:
    """
    Resultado de la ingesta.

    Attributes
    ----------
    detections : pd.DataFrame
        Detecciones válidas y deduplicadas en el esquema común (``DETECTION_COLUMNS``).
    discarded : pd.DataFrame
        Filas descartadas con columnas ``source``, ``reason`` y ``count``.
    """

    detections: pd.DataFrame
    discarded: pd.DataFrame


def detect_source(columns: Iterable[str]) -> Source:
    """
    Detecta el sensor a partir de las columnas del fichero.

    Parameters
    ----------
    columns : Iterable[str]
        Nombres de columna (insensible a mayúsculas).

    Returns
    -------
    Source
        ``MODIS`` o ``VIIRS``.

    Raises
    ------
    UnknownFirmsFormatError
        Si faltan columnas obligatorias o no se reconoce el sensor.
    """
    names = {column.strip().lower() for column in columns}
    missing = set(REQUIRED_COLUMNS) - names
    if missing:
        raise UnknownFirmsFormatError(f"Faltan columnas FIRMS: {sorted(missing)}")

    if "bright_ti4" in names:
        return Source.VIIRS

    if "brightness" in names:
        return Source.MODIS

    raise UnknownFirmsFormatError("No se reconoce el sensor (falta brightness/bright_ti4)")


def read_firms_csv(path: str | Path) -> pd.DataFrame:
    """
    Lee un CSV de FIRMS conservando los valores originales como texto.

    Parameters
    ----------
    path : str | Path
        Ruta del fichero (``.csv`` o ``.txt``).

    Returns
    -------
    pd.DataFrame
        Columnas en minúsculas, todos los valores como texto.
    """
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    frame.columns = [column.strip().lower() for column in frame.columns]

    return frame


def parse_acq_datetime(acq_date: pd.Series, acq_time: pd.Series) -> pd.Series:
    """
    Combina ``acq_date`` y ``acq_time`` en un datetime UTC tz-aware.

    Acepta ``acq_time`` como ``"HH:MM"`` (ficheros NRT) o como entero ``HHMM`` sin ceros a la
    izquierda (API de FIRMS, p. ej. ``304`` = 03:04).

    Parameters
    ----------
    acq_date : pd.Series
        Fechas ``YYYY-MM-DD``.
    acq_time : pd.Series
        Horas UTC.

    Returns
    -------
    pd.Series
        ``datetime64[ns, UTC]``; ``NaT`` si no se puede interpretar.
    """
    raw_time = acq_time.astype("string").str.strip().str.replace(":", "", regex=False)
    is_hhmm = raw_time.str.fullmatch(r"\d{1,4}").fillna(False).astype(bool)
    hhmm = raw_time.where(is_hhmm, None).str.zfill(4)
    combined = acq_date.astype("string").str.strip() + " " + hhmm

    return pd.to_datetime(combined, format="%Y-%m-%d %H%M", errors="coerce", utc=True)


def build_detection_ids(detections: pd.DataFrame) -> pd.Series:
    """
    Genera un ``detection_id`` determinista (SHA-1) por detección.

    La clave es ``source|satellite|acq_datetime ISO|lat|lon`` con coordenadas redondeadas a
    ``COORDINATE_DECIMALS`` decimales, de modo que reingerir el mismo fichero produce los mismos
    identificadores.

    Parameters
    ----------
    detections : pd.DataFrame
        Detecciones con ``source``, ``satellite``, ``acq_datetime``, ``lat`` y ``lon``.

    Returns
    -------
    pd.Series
        Identificadores hexadecimales de 40 caracteres.
    """
    keys = (
        detections["source"].astype(str)
        + "|"
        + detections["satellite"].astype(str)
        + "|"
        + detections["acq_datetime"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        + "|"
        + detections["lat"].map(lambda value: f"{value:.{COORDINATE_DECIMALS}f}")
        + "|"
        + detections["lon"].map(lambda value: f"{value:.{COORDINATE_DECIMALS}f}")
    )

    return keys.map(lambda key: hashlib.sha1(key.encode("utf-8")).hexdigest())


def normalize_firms(raw: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """
    Convierte un CSV FIRMS crudo al esquema común (sin validar ni deduplicar).

    Parameters
    ----------
    raw : pd.DataFrame
        Tabla tal como la devuelve :func:`read_firms_csv`.
    config : PipelineConfig
        Configuración (umbrales de confianza y coeficiente de biomasa).

    Returns
    -------
    pd.DataFrame
        Columnas de ``DETECTION_COLUMNS`` salvo ``detection_id``; los valores no
        interpretables quedan como nulos para que la validación los descarte.
    """
    raw = raw.rename(columns=lambda column: column.strip().lower())
    source = detect_source(raw.columns)
    confidence_cfg = config.confidence

    normalized = pd.DataFrame(index=raw.index)
    normalized["source"] = source.value
    satellite = raw["satellite"].astype("string").str.strip()
    normalized["satellite"] = satellite.replace(config.validation.satellite_aliases)
    normalized["acq_datetime"] = parse_acq_datetime(raw["acq_date"], raw["acq_time"])
    normalized["acq_date"] = normalized["acq_datetime"].dt.date
    normalized["lat"] = pd.to_numeric(raw["latitude"], errors="coerce")
    normalized["lon"] = pd.to_numeric(raw["longitude"], errors="coerce")
    # El área de la detección se guarda como ancho y alto en km, genéricos (sin depender del
    # sensor). Hoy salen del tamaño nominal de píxel de cada sensor (``geometry.pixel_size_km``);
    # no se usan ``scan`` y ``track`` de FIRMS.
    pixel_size_km = config.geometry.pixel_size_km[source.value]
    normalized["area_width_km"] = pixel_size_km
    normalized["area_height_km"] = pixel_size_km
    normalized["frp"] = pd.to_numeric(raw["frp"], errors="coerce")
    normalized["biomass_kg_s"] = normalized["frp"] * config.biomass.coefficient_kg_per_mj
    normalized["daynight"] = raw["daynight"].astype("string").str.strip().str.upper()
    normalized["confidence_raw"] = raw["confidence"].astype("string").str.strip()

    if source is Source.MODIS:
        normalized["confidence_norm"] = normalize_modis_confidence(
            raw["confidence"], confidence_cfg.modis_low_max, confidence_cfg.modis_nominal_max
        )
    else:
        normalized["confidence_norm"] = normalize_viirs_confidence(
            raw["confidence"], confidence_cfg.viirs_mapping
        )

    for raw_column, common_column in SENSOR_COLUMNS[source.value].items():
        values = raw[raw_column] if raw_column in raw else pd.Series(np.nan, index=raw.index)
        normalized[common_column] = pd.to_numeric(values, errors="coerce")

    version = raw["version"] if "version" in raw else pd.Series(pd.NA, index=raw.index)
    normalized["version"] = version.astype("string")
    # ``type`` solo viene en los ficheros anuales (no en los NRT): sirve para marcar, no se guarda.
    firms_type = raw["type"] if "type" in raw else pd.Series(np.nan, index=raw.index)
    normalized["firms_type"] = pd.to_numeric(firms_type, errors="coerce")

    return normalized


def _in_zone(detections: pd.DataFrame, zone: ExclusionZone) -> pd.Series:
    min_lon, min_lat, max_lon, max_lat = zone.bbox
    inside = detections["lon"].between(min_lon, max_lon) & detections["lat"].between(
        min_lat, max_lat
    )
    if zone.start is not None:
        inside &= detections["acq_date"] >= zone.start

    if zone.end is not None:
        inside &= detections["acq_date"] <= zone.end

    return inside


def source_skip_reasons(detections: pd.DataFrame, rules: ValidationConfig) -> pd.Series:
    """
    Motivo por el que una detección no es un incendio de vegetación (``None`` si lo es).

    Primero manda el ``type`` de FIRMS (volcán, fuente estática, mar) y después las zonas de
    exclusión configuradas.

    Parameters
    ----------
    detections : pd.DataFrame
        Detecciones normalizadas, con ``firms_type``, ``lat``, ``lon`` y ``acq_date``.
    rules : ValidationConfig
        Tipos y zonas de exclusión.

    Returns
    -------
    pd.Series
        Motivo por fila (objeto, ``None`` si no se marca).
    """
    by_type = detections["firms_type"].map(rules.non_wildfire_types).astype("object")
    reasons = by_type.where(by_type.notna(), None)
    for zone in rules.exclusion_zones:
        free = reasons.isna() & _in_zone(detections, zone)
        reasons = reasons.mask(free, zone.reason)

    return reasons


def invalid_reasons(detections: pd.DataFrame, rules: ValidationConfig) -> pd.Series:
    """
    Devuelve el primer motivo de invalidez de cada fila (``None`` si es válida).

    Parameters
    ----------
    detections : pd.DataFrame
        Detecciones normalizadas.
    rules : ValidationConfig
        Rangos válidos.

    Returns
    -------
    pd.Series
        Motivo por fila.
    """
    checks = {
        "missing_satellite": detections["satellite"].isna() | (detections["satellite"] == ""),
        "invalid_datetime": detections["acq_datetime"].isna(),
        "invalid_lat": ~detections["lat"].between(*rules.lat_range),
        "invalid_lon": ~detections["lon"].between(*rules.lon_range),
        "invalid_frp": ~(detections["frp"] >= rules.frp_min),
        "invalid_daynight": ~detections["daynight"].isin(rules.allowed_daynight).fillna(False),
        "invalid_confidence": detections["confidence_norm"].isna(),
    }
    conditions = [mask.fillna(True).to_numpy(dtype=bool) for mask in checks.values()]
    reasons = np.select(conditions, list(checks), default="")

    reasons = pd.Series(reasons, index=detections.index, dtype="object")

    return reasons.where(reasons != "", None)


def keep_forest_detections(
    detections: pd.DataFrame, rules: ValidationConfig
) -> tuple[pd.DataFrame, int]:
    """
    Rescata las detecciones en bosque que se marcaron por FRP por debajo del mínimo.

    En bosque el riesgo es alto aunque el foco sea pequeño: pasan a ser válidas con categoría
    ``low``. Las de agricultura u otros usos siguen marcadas. Necesita ``is_forest`` (SIGPAC); sin
    esa columna no hace nada.

    Parameters
    ----------
    detections : pd.DataFrame
        Detecciones con ``is_valid``, ``skip_reason`` y, si hay cobertura del suelo, ``is_forest``.
    rules : ValidationConfig
        Reglas de validación (``keep_forest_below_frp``).

    Returns
    -------
    tuple[pd.DataFrame, int]
        Detecciones actualizadas (copia) y cuántas se rescataron.
    """
    if not rules.keep_forest_below_frp or "is_forest" not in detections:
        return detections, 0

    forest = detections["is_forest"].fillna(False).astype(bool)
    rescued = (detections["skip_reason"] == BELOW_FRP_REASON) & forest
    count = int(rescued.sum())
    if count == 0:
        return detections, 0

    updated = detections.copy()
    updated.loc[rescued, "is_valid"] = True
    updated.loc[rescued, "skip_reason"] = None
    updated.loc[rescued, "frp_category"] = CATEGORY_LOW
    updated.loc[rescued, "frp_class"] = 1

    return updated, count


def summarize_discards(sources: pd.Series, reasons: pd.Series) -> pd.DataFrame:
    """Agrupa los descartes por sensor y motivo."""
    discarded = pd.DataFrame({"source": sources, "reason": reasons}).dropna(subset=["reason"])
    if discarded.empty:
        return pd.DataFrame({"source": [], "reason": [], "count": []})

    return discarded.value_counts(["source", "reason"]).rename("count").reset_index()


def ingest_frames(raw_frames: Iterable[pd.DataFrame], config: PipelineConfig) -> IngestResult:
    """
    Normaliza, valida y deduplica una o varias tablas FIRMS.

    Parameters
    ----------
    raw_frames : Iterable[pd.DataFrame]
        Tablas FIRMS crudas (MODIS y/o VIIRS, en cualquier orden).
    config : PipelineConfig
        Configuración del pipeline.

    Returns
    -------
    IngestResult
        Detecciones válidas y resumen de descartes.
    """
    normalized = pd.concat(
        [normalize_firms(frame, config) for frame in raw_frames], ignore_index=True
    )
    reasons = invalid_reasons(normalized, config.validation)
    discards = [summarize_discards(normalized["source"], reasons)]

    valid = normalized[reasons.isna()].copy()
    valid["detection_id"] = build_detection_ids(valid)

    duplicated = valid["detection_id"].duplicated(keep="first")
    discards.append(summarize_discards(valid["source"], duplicated.map({True: "duplicate"})))
    valid = valid[~duplicated]

    valid = classify_frp(
        valid, instrument_col="source", umbrales=config.validation.frp_thresholds_mw
    )
    noise = valid["frp_category"] == CATEGORY_DISCARDED
    if not config.validation.apply_frp_thresholds:
        noise = pd.Series(False, index=valid.index)

    not_wildfire = pd.Series(None, index=valid.index, dtype="object")
    if config.validation.apply_source_filters:
        not_wildfire = source_skip_reasons(valid, config.validation)

    # No se descartan: se marcan y los pasos siguientes las saltan (is_valid / skip_reason).
    # Si hay varios motivos, manda el de fuente (volcán, industria, mar) sobre el FRP bajo.
    frp_reason = pd.Series(
        np.where(noise, BELOW_FRP_REASON, None), index=valid.index, dtype="object"
    )
    skip_reason = not_wildfire.where(not_wildfire.notna(), frp_reason)
    valid["is_valid"] = skip_reason.isna()
    valid["skip_reason"] = skip_reason
    for reason, count in skip_reason.value_counts().items():
        LOGGER.info("Detecciones marcadas como no válidas (%s): %s", reason, count)

    discarded = pd.concat(discards, ignore_index=True)
    for row in discarded.itertuples(index=False):
        LOGGER.warning("Descartadas %s filas %s: %s", row.count, row.source, row.reason)

    LOGGER.info("Detecciones ingeridas (válidas o marcadas): %s", len(valid))
    detections = valid.loc[:, list(DETECTION_COLUMNS)].sort_values(
        ["acq_datetime", "detection_id"], ignore_index=True
    )

    return IngestResult(detections=detections, discarded=discarded)


def ingest_files(paths: Iterable[str | Path], config: PipelineConfig) -> IngestResult:
    """
    Lee e ingiere ficheros FIRMS locales.

    Parameters
    ----------
    paths : Iterable[str | Path]
        Ficheros MODIS y/o VIIRS.
    config : PipelineConfig
        Configuración del pipeline.

    Returns
    -------
    IngestResult
        Detecciones válidas y resumen de descartes.
    """
    return ingest_frames((read_firms_csv(path) for path in paths), config)
