"""Categorización de la potencia radiativa de fuego (FRP, en MW) en clases low/medium/high."""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from fire_etl.config import _default_frp_thresholds_mw

CATEGORY_LOW = "low"
CATEGORY_MEDIUM = "medium"
CATEGORY_HIGH = "high"
CATEGORY_DISCARDED = "discarded"
CATEGORY_UNKNOWN = "unknown"


def _normalizar_instrumento(
    serie: pd.Series, umbrales: Mapping[str, Mapping[str, float]]
) -> pd.Series:
    """Normaliza variantes ("viirs", "VIIRS_SNPP", "MODIS_NRT"...) a VIIRS/MODIS o NaN."""
    texto = serie.astype("string").str.strip().str.upper()
    resultado = pd.Series(np.nan, index=serie.index, dtype="object")
    for nombre in umbrales:
        resultado[texto.str.contains(nombre, regex=False).fillna(False).to_numpy(bool)] = nombre
    return resultado


def classify_frp(
    datos: pd.DataFrame | Sequence[Mapping[str, Any]],
    instrument_col: str = "instrument",
    col_frp: str = "frp",
    col_daynight: str = "daynight",
    umbrales: Mapping[str, Mapping[str, float]] | None = None,
) -> pd.DataFrame:
    """
    Añade ``frp_class`` y ``frp_category`` a cada detección según su FRP.

    Reglas (por instrumento; el mínimo depende de ``daynight``: "D" día, "N" noche):

    - ``frp < mínimo``: discarded (clase 0).
    - ``mínimo <= frp <= limit_low``: clase 1, "low".
    - ``limit_low < frp <= limit_medium``: clase 2, "medium".
    - ``frp > limit_medium``: clase 3, "high".

    Las filas con instrumento/daynight no reconocidos, o FRP nulo o negativo, reciben la
    categoría "unknown" (clase nula) y se avisa con un ``UserWarning``.

    Parameters
    ----------
    datos : pandas.DataFrame or sequence of mapping
        Detecciones. El resto de columnas se conservan sin cambios.
    instrument_col, col_frp, col_daynight : str
        Nombres de las columnas de entrada.
    umbrales : mapping, optional
        Umbrales por instrumento; por defecto los de :class:`ValidationConfig`.

    Returns
    -------
    pandas.DataFrame
        Copia de la entrada con ``frp_class`` (``Int64``; 0 = descartado, nulo si desconocido) y
        ``frp_category``.
    """
    umbrales = umbrales or _default_frp_thresholds_mw()
    df = pd.DataFrame(datos).copy()

    instrumento = _normalizar_instrumento(df[instrument_col], umbrales)
    daynight = df[col_daynight].astype("string").str.strip().str.upper()
    frp = pd.to_numeric(df[col_frp], errors="coerce")

    es_dia = (daynight == "D").to_numpy(bool)
    es_noche = (daynight == "N").to_numpy(bool)
    valido = instrumento.notna().to_numpy(bool) & (es_dia | es_noche)
    valido &= (frp >= 0).to_numpy(bool)  # NaN y negativos quedan fuera

    # Umbrales por fila (vectorizado): NaN donde el instrumento no es reconocido.
    def _umbral(clave: str) -> np.ndarray:
        mapa = {nombre: u[clave] for nombre, u in umbrales.items()}
        return instrumento.map(mapa).astype(float).to_numpy()

    minimo = np.where(es_dia, _umbral("min_day"), _umbral("min_night"))
    lim_baja = _umbral("limit_low")
    lim_media = _umbral("limit_medium")
    valor = frp.to_numpy(float)

    # Con comparaciones contra NaN el resultado es False; `valido` decide el caso final.
    clase = np.select(
        [valor < minimo, valor <= lim_baja, valor <= lim_media],
        [0, 1, 2],
        default=3,
    )
    clase = np.where(valido, clase, -1)
    categorias = np.array(
        [CATEGORY_DISCARDED, CATEGORY_LOW, CATEGORY_MEDIUM, CATEGORY_HIGH], dtype=object
    )
    df["frp_category"] = np.where(valido, categorias[np.clip(clase, 0, 3)], CATEGORY_UNKNOWN)
    df["frp_class"] = pd.array(np.where(clase >= 0, clase, 0), dtype="Int64")
    df.loc[clase < 0, "frp_class"] = pd.NA  # solo desconocidos; descartados = 0

    n_invalidas = int((~valido).sum())
    if n_invalidas:
        warnings.warn(
            f"{n_invalidas} fila(s) con instrumento, daynight o FRP inválidos "
            f"-> categoría '{CATEGORY_UNKNOWN}'.",
            UserWarning,
            stacklevel=2,
        )
    return df
