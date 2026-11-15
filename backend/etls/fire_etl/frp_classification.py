"""Categorización de la potencia radiativa de fuego (FRP, en MW) en clases baja/media/alta."""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

# --- Umbrales (editar aquí) ------------------------------------------------------------------
UMBRALES_FRP_MW: dict[str, dict[str, float]] = {
    "VIIRS": {
        "min_dia": 15.0,  # < 15.0 MW se descarta de día
        "min_noche": 6.0,  # < 6.0 MW se descarta de noche
        "limite_baja": 35.0,  # [min, 35.0] -> Clase 1 (Baja)
        "limite_media": 70.0,  # (35.0, 70.0] -> Clase 2 (Media) | > 70.0 -> Clase 3 (Alta)
    },
    "MODIS": {
        "min_dia": 30.0,  # < 30.0 MW se descarta de día
        "min_noche": 12.0,  # < 12.0 MW se descarta de noche
        "limite_baja": 75.0,  # [min, 75.0] -> Clase 1 (Baja)
        "limite_media": 175.0,  # (75.0, 175.0] -> Clase 2 (Media) | > 175.0 -> Clase 3 (Alta)
    },
}

CATEGORIA_BAJA = "baja"
CATEGORIA_MEDIA = "media"
CATEGORIA_ALTA = "alta"
CATEGORIA_DESCARTADO = "descartado"
CATEGORIA_DESCONOCIDO = "desconocido"


def _normalizar_instrumento(serie: pd.Series) -> pd.Series:
    """Normaliza variantes ("viirs", "VIIRS_SNPP", "MODIS_NRT"...) a VIIRS/MODIS o NaN."""
    texto = serie.astype("string").str.strip().str.upper()
    resultado = pd.Series(np.nan, index=serie.index, dtype="object")
    for nombre in UMBRALES_FRP_MW:
        resultado[texto.str.contains(nombre, regex=False).fillna(False).to_numpy(bool)] = nombre
    return resultado


def clasificar_frp(
    datos: pd.DataFrame | Sequence[Mapping[str, Any]],
    col_instrumento: str = "instrumento",
    col_frp: str = "frp",
    col_daynight: str = "daynight",
    umbrales: Mapping[str, Mapping[str, float]] | None = None,
) -> pd.DataFrame:
    """
    Añade ``frp_clase`` y ``frp_categoria`` a cada detección según su FRP.

    Reglas (por instrumento; el mínimo depende de ``daynight``: "D" día, "N" noche):

    - ``frp < mínimo``: descartado (clase nula).
    - ``mínimo <= frp <= limite_baja``: clase 1, "baja".
    - ``limite_baja < frp <= limite_media``: clase 2, "media".
    - ``frp > limite_media``: clase 3, "alta".

    Las filas con instrumento/daynight no reconocidos, o FRP nulo o negativo, reciben la
    categoría "desconocido" (clase nula) y se avisa con un ``UserWarning``.

    Parameters
    ----------
    datos : pandas.DataFrame or sequence of mapping
        Detecciones. El resto de columnas se conservan sin cambios.
    col_instrumento, col_frp, col_daynight : str
        Nombres de las columnas de entrada.
    umbrales : mapping, optional
        Umbrales por instrumento; por defecto ``UMBRALES_FRP_MW``.

    Returns
    -------
    pandas.DataFrame
        Copia de la entrada con ``frp_clase`` (``Int64``, nulo si no aplica) y
        ``frp_categoria``.
    """
    umbrales = umbrales or UMBRALES_FRP_MW
    df = pd.DataFrame(datos).copy()

    instrumento = _normalizar_instrumento(df[col_instrumento])
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

    minimo = np.where(es_dia, _umbral("min_dia"), _umbral("min_noche"))
    lim_baja = _umbral("limite_baja")
    lim_media = _umbral("limite_media")
    valor = frp.to_numpy(float)

    # Con comparaciones contra NaN el resultado es False; `valido` decide el caso final.
    clase = np.select(
        [valor < minimo, valor <= lim_baja, valor <= lim_media],
        [0, 1, 2],
        default=3,
    )
    clase = np.where(valido, clase, -1)
    categorias = np.array(
        [CATEGORIA_DESCARTADO, CATEGORIA_BAJA, CATEGORIA_MEDIA, CATEGORIA_ALTA], dtype=object
    )
    df["frp_categoria"] = np.where(valido, categorias[np.clip(clase, 0, 3)], CATEGORIA_DESCONOCIDO)
    df["frp_clase"] = pd.array(np.where(clase > 0, clase, 0), dtype="Int64")
    df.loc[clase <= 0, "frp_clase"] = pd.NA

    n_invalidas = int((~valido).sum())
    if n_invalidas:
        warnings.warn(
            f"{n_invalidas} fila(s) con instrumento, daynight o FRP inválidos "
            f"-> categoría '{CATEGORIA_DESCONOCIDO}'.",
            UserWarning,
            stacklevel=2,
        )
    return df
