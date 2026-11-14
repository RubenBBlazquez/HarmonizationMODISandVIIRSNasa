"""
Normalización y fusión de la confianza de detecciones MODIS y VIIRS.

Las categorías fusionadas (``low``, ``medium``, ``high``, ``very_high``) son propias de este
algoritmo y **no** equivalen a las categorías oficiales de FIRMS.

Matriz completa (filas: nivel MODIS de la observación; columnas: nivel VIIRS; ``—`` = sensor
ausente). ``*`` = low aislada (un único sensor), que se marca y opcionalmente se descarta.

=============  ==========  ==========  ==========  ===========
MODIS \\ VIIRS  —           low         nominal     high
=============  ==========  ==========  ==========  ===========
—              (inválido)  low *       medium      medium [1]
low            low *       low         medium      medium
nominal        medium      medium      high        high
high           medium [1]  medium      high        very_high
=============  ==========  ==========  ==========  ===========

[1] Configurable con ``single_sensor_high_level`` (``medium`` por defecto: sin corroboración
entre sensores una observación nunca supera ``medium``).

Criterios:

- La corroboración entre sensores (MODIS + VIIRS) es la única vía a ``high`` / ``very_high``.
- Si algún sensor coincidente es ``low``, la observación queda en ``medium`` como máximo: un
  sensor la respalda y el otro la ve débilmente.
- Dos satélites del mismo sensor (Aqua + Terra, S-NPP + NOAA-20) no cuentan como corroboración.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd


class ConfidenceLevel(StrEnum):
    """Confianza normalizada de una detección individual."""

    LOW = "low"
    NOMINAL = "nominal"
    HIGH = "high"


class FusedConfidence(StrEnum):
    """Confianza fusionada de una observación (categorías propias del algoritmo)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


LEVEL_RANK = {ConfidenceLevel.LOW: 0, ConfidenceLevel.NOMINAL: 1, ConfidenceLevel.HIGH: 2}
FUSED_RANK = {level: rank for rank, level in enumerate(FusedConfidence)}


class MissingSensorConfidenceError(ValueError):
    """Se pidió fusionar una observación sin confianza de ningún sensor."""


@dataclass(frozen=True)
class FusionOutcome:
    """
    Resultado de fusionar la confianza de una observación.

    Attributes
    ----------
    level : FusedConfidence
        Confianza fusionada.
    is_isolated_low : bool
        ``True`` si la observación tiene un único sensor y es ``low``.
    """

    level: FusedConfidence
    is_isolated_low: bool


def fuse_confidence(
    modis: ConfidenceLevel | str | None,
    viirs: ConfidenceLevel | str | None,
    single_sensor_high_level: FusedConfidence | str = FusedConfidence.MEDIUM,
) -> FusionOutcome:
    """
    Calcula la confianza fusionada de una observación (función pura).

    Parameters
    ----------
    modis : ConfidenceLevel | str | None
        Nivel MODIS de la observación (máximo de sus detecciones MODIS) o ``None`` si no hay.
    viirs : ConfidenceLevel | str | None
        Nivel VIIRS de la observación o ``None`` si no hay.
    single_sensor_high_level : FusedConfidence | str
        Nivel asignado a un único sensor con confianza ``high`` (``medium`` o ``high``).

    Returns
    -------
    FusionOutcome
        Nivel fusionado y marca de low aislada.

    Raises
    ------
    MissingSensorConfidenceError
        Si ambos sensores son ``None``.
    """
    modis_level = _as_level(modis)
    viirs_level = _as_level(viirs)
    single_high = FusedConfidence(single_sensor_high_level)

    if modis_level is None and viirs_level is None:
        raise MissingSensorConfidenceError("Una observación necesita al menos un sensor")

    if modis_level is None or viirs_level is None:
        only = modis_level if modis_level is not None else viirs_level
        return _single_sensor(only, single_high)

    levels = {modis_level, viirs_level}

    if levels == {ConfidenceLevel.LOW}:
        return FusionOutcome(FusedConfidence.LOW, is_isolated_low=False)

    if ConfidenceLevel.LOW in levels:
        return FusionOutcome(FusedConfidence.MEDIUM, is_isolated_low=False)

    if levels == {ConfidenceLevel.HIGH}:
        return FusionOutcome(FusedConfidence.VERY_HIGH, is_isolated_low=False)

    return FusionOutcome(FusedConfidence.HIGH, is_isolated_low=False)


def _single_sensor(level: ConfidenceLevel, single_high: FusedConfidence) -> FusionOutcome:
    if level is ConfidenceLevel.LOW:
        return FusionOutcome(FusedConfidence.LOW, is_isolated_low=True)

    if level is ConfidenceLevel.NOMINAL:
        return FusionOutcome(FusedConfidence.MEDIUM, is_isolated_low=False)

    return FusionOutcome(single_high, is_isolated_low=False)


def _as_level(value: ConfidenceLevel | str | None) -> ConfidenceLevel | None:
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return None

    return ConfidenceLevel(value)


def max_level(levels: Iterable[str]) -> str | None:
    """
    Devuelve el nivel más alto de un conjunto de niveles normalizados.

    Parameters
    ----------
    levels : Iterable[str]
        Niveles ``low`` / ``nominal`` / ``high``; se ignoran los nulos.

    Returns
    -------
    str | None
        Nivel máximo o ``None`` si no hay niveles.
    """
    present = [ConfidenceLevel(level) for level in levels if isinstance(level, str)]
    if not present:
        return None

    return str(max(present, key=LEVEL_RANK.__getitem__))


def max_fused(levels: Iterable[str]) -> str | None:
    """Devuelve la confianza fusionada más alta (``None`` si no hay valores)."""
    present = [FusedConfidence(level) for level in levels if isinstance(level, str)]
    if not present:
        return None

    return str(max(present, key=FUSED_RANK.__getitem__))


def normalize_modis_confidence(values: pd.Series, low_max: int, nominal_max: int) -> pd.Series:
    """
    Normaliza la confianza numérica MODIS (0-100) a low/nominal/high.

    Parameters
    ----------
    values : pd.Series
        Valores originales (texto o numéricos).
    low_max : int
        Máximo incluido del nivel ``low``.
    nominal_max : int
        Máximo incluido del nivel ``nominal``.

    Returns
    -------
    pd.Series
        Niveles normalizados; ``None`` si el valor no es numérico o está fuera de 0-100.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.between(0, 100)
    levels = pd.cut(
        numeric,
        bins=[-np.inf, low_max, nominal_max, np.inf],
        labels=[level.value for level in ConfidenceLevel],
        right=True,
    ).astype("object")

    return levels.where(valid, None)


def normalize_viirs_confidence(values: pd.Series, mapping: dict[str, str]) -> pd.Series:
    """
    Normaliza la confianza categórica VIIRS (``l``/``n``/``h`` o palabra completa).

    Parameters
    ----------
    values : pd.Series
        Valores originales.
    mapping : dict[str, str]
        Correspondencia en minúsculas entre valor original y nivel.

    Returns
    -------
    pd.Series
        Niveles normalizados; ``None`` si el valor no está en ``mapping``.
    """
    normalized = values.astype("string").str.strip().str.lower().map(mapping)

    return normalized.astype("object").where(normalized.notna(), None)
