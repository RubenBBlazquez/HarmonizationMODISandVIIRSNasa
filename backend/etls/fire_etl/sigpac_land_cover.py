"""
Enriquece coordenadas con la cobertura del suelo usando SIGPAC (recintos del FEGA).

Columnas añadidas (nombres en las constantes de abajo): ``land_cover``,
``land_cover_confidence``, ``land_cover_source``, ``sigpac_uso``, ``is_agricultural`` e
``is_forest``. Los puntos que no caen en ningún recinto quedan con los campos vacíos.

Land cover: SIGPAC (FEGA / Ministerio de Agricultura, Pesca y Alimentación)
Revisa las condiciones de reutilización de SIGPAC en el aviso legal del FEGA antes de publicar.

Se invoca desde el pipeline principal (sección ``land_cover`` de la configuración).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

# --- Nombres de columnas -------------------------------------------------------------------
COL_LAND_COVER = "land_cover"
COL_CONFIDENCE = "land_cover_confidence"
COL_SOURCE = "land_cover_source"
COL_SIGPAC_USO = "sigpac_uso"
COL_IS_AGRICULTURAL = "is_agricultural"
COL_IS_FOREST = "is_forest"
NEW_COLUMNS = (
    COL_LAND_COVER,
    COL_CONFIDENCE,
    COL_SOURCE,
    COL_SIGPAC_USO,
    COL_IS_AGRICULTURAL,
    COL_IS_FOREST,
)
PREV_SUFFIX = "_prev"
LAT_COLUMN = "lat"
LON_COLUMN = "lon"

# --- Parámetros -----------------------------------------------------------------------------
SIGPAC_CONFIDENCE = 0.95  # confianza asignada a los puntos que caen en un recinto SIGPAC
FLAG_MIN_CONFIDENCE = 0.0  # umbral opcional para is_agricultural / is_forest
SIGPAC_USE_FIELDS = ("uso_sigpac", "uso")  # nombres posibles del campo de uso (sin mayúsculas)
SIGPAC_EXTENSIONS = (".shp", ".gpkg")
BBOX_PADDING_DEG = 0.01  # margen del bbox con el que se leen los recintos

SOURCE_SIGPAC = "sigpac"
OTHER = "other"

# --- Normalización de usos SIGPAC (editable) ----------------------------------------------
SIGPAC_TO_LAND_COVER = {
    # Cultivos herbáceos
    "TA": "crops",  # tierra arable
    "TH": "crops",  # huerta
    "IV": "crops",  # invernaderos y cultivos bajo plástico
    # Cultivos leñosos
    "OV": "crops",  # olivar
    "VI": "crops",  # viñedo
    "FY": "crops",  # frutales
    "FS": "crops",  # frutos secos
    "CI": "crops",  # cítricos
    "CF": "crops",  # cítricos-frutales
    "CS": "crops",  # cítricos-frutos secos
    "CV": "crops",  # cítricos-viñedo
    "FF": "crops",  # frutales-frutos secos
    "FL": "crops",  # frutos secos y olivar
    "FV": "crops",  # frutos secos y viñedo
    "OC": "crops",  # olivar-cítricos
    "OF": "crops",  # olivar-frutal
    "VF": "crops",  # viñedo-frutal
    "VO": "crops",  # viñedo-olivar
    # Forestal y pastos
    "FO": "trees",  # forestal
    "PA": "shrub_and_scrub",  # pasto arbolado
    "PR": "shrub_and_scrub",  # pasto arbustivo
    "PS": "grass",  # pastizal
    # Artificial, agua e improductivo
    "ZU": "built",  # zona urbana
    "ED": "built",  # edificaciones
    "CA": "built",  # viales
    "AG": "water",  # corrientes y superficies de agua
    "IM": "bare",  # improductivo
    # Sin equivalente directo
    "EP": OTHER,  # elemento del paisaje
    "ZC": OTHER,  # zona concentrada
    "ZV": OTHER,  # zona censurada
}

ATTRIBUTIONS = ("Land cover: SIGPAC (FEGA / Ministerio de Agricultura, Pesca y Alimentación)",)
SIGPAC_NOTICE = (
    "Revisa las condiciones de reutilización de SIGPAC en el aviso legal del FEGA "
    "antes de redistribuir estos datos."
)
LOGGER = logging.getLogger(__name__)


class LandCoverError(ValueError):
    """Entrada no válida para el enriquecimiento."""


@dataclass
class Summary:
    """Recuentos del proceso, para la consola y los metadatos."""

    total: int = 0
    invalid: int = 0
    sigpac: int = 0
    no_data: int = 0
    multiple_recintos: int = 0
    unknown_sigpac_codes: list[str] = field(default_factory=list)


# --- Entrada y validación -------------------------------------------------------------------


def read_coordinates(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """
    Devuelve ``(lat, lon)`` numéricos sin añadir columnas.

    Usa ``LAT_COLUMN`` / ``LON_COLUMN`` o, si no existen, la geometría (en EPSG:4326).
    """
    if LAT_COLUMN in df.columns and LON_COLUMN in df.columns:
        lats = pd.to_numeric(df[LAT_COLUMN], errors="coerce")
        lons = pd.to_numeric(df[LON_COLUMN], errors="coerce")

        return lats, lons

    if isinstance(df, gpd.GeoDataFrame):
        points = df.geometry.to_crs(4326) if df.crs is not None else df.geometry

        return points.y, points.x

    raise LandCoverError(f"Faltan las columnas '{LAT_COLUMN}' y '{LON_COLUMN}'.")


def valid_coordinates(lats: pd.Series, lons: pd.Series) -> pd.Series:
    """Máscara de puntos con lat/lon numéricos y en rango."""
    return lats.between(-90, 90) & lons.between(-180, 180)


def rename_colliding_columns(df: pd.DataFrame, names: Sequence[str]) -> pd.DataFrame:
    """
    Renombra con ``_prev`` las columnas existentes que coinciden con ``names``.

    Raises
    ------
    LandCoverError
        Si ``<nombre>_prev`` ya existe (no se sobrescribe nada).
    """
    renames = {}
    for name in names:
        if name not in df.columns:
            continue

        if name + PREV_SUFFIX in df.columns:
            raise LandCoverError(f"Existen '{name}' y '{name}{PREV_SUFFIX}': renómbralas antes.")

        renames[name] = name + PREV_SUFFIX
        LOGGER.warning(f"la columna '{name}' ya existía; renombrada a '{name}{PREV_SUFFIX}'.")

    return df.rename(columns=renames)


# --- SIGPAC ---------------------------------------------------------------------------------


def find_sigpac_layers(folder: Path) -> list[tuple[Path, str | None]]:
    """
    Lista las capas SIGPAC de una carpeta (busca de forma recursiva).

    Returns
    -------
    list[tuple[Path, str | None]]
        ``(fichero, capa)``; la capa es ``None`` en los shapefiles.
    """
    layers: list[tuple[Path, str | None]] = []
    for path in sorted(folder.rglob("*")):
        suffix = path.suffix.lower()
        if suffix == ".shp":
            layers.append((path, None))
        elif suffix == ".gpkg":
            layers.extend((path, name) for name in gpd.list_layers(path)["name"])

    return layers


def find_use_field(columns: Sequence[str]) -> str | None:
    """Devuelve el nombre real del campo de uso SIGPAC (sin distinguir mayúsculas)."""
    by_lower = {column.lower(): column for column in columns}
    for candidate in SIGPAC_USE_FIELDS:
        if candidate in by_lower:
            return by_lower[candidate]

    return None


def normalize_sigpac_use(code: str) -> tuple[str, bool]:
    """
    Normaliza un código de uso SIGPAC.

    Returns
    -------
    tuple[str, bool]
        ``(clase, reconocido)``; los códigos desconocidos devuelven ``("other", False)``.
    """
    normalized = code.strip().upper()
    if normalized in SIGPAC_TO_LAND_COVER:
        return SIGPAC_TO_LAND_COVER[normalized], True

    return OTHER, False


def read_recintos(path: Path, layer: str | None, area: gpd.GeoSeries) -> gpd.GeoDataFrame | None:
    """
    Lee los recintos de una capa que tocan ``area`` y comprueba su CRS y su campo de uso.

    Returns
    -------
    gpd.GeoDataFrame | None
        Recintos con una única columna ``uso`` o ``None`` si la capa no es utilizable.
    """
    label = f"{path.name}" + (f":{layer}" if layer else "")
    try:
        recintos = gpd.read_file(path, layer=layer, bbox=area)
    except Exception as error:  # capa corrupta o formato no legible: se salta
        LOGGER.warning(f"no se pudo leer {label}: {error}")
        return None

    if recintos.empty:
        return None

    if recintos.crs is None:
        LOGGER.warning(f"{label} no tiene CRS definido; se omite.")
        return None

    use_field = find_use_field(recintos.columns)
    if use_field is None:
        LOGGER.warning(f"{label} no tiene campo de uso ({', '.join(SIGPAC_USE_FIELDS)}).")
        return None

    recintos = recintos[[use_field, "geometry"]].rename(columns={use_field: "uso"})

    return recintos


def match_sigpac(points: gpd.GeoDataFrame, folder: Path, summary: Summary) -> pd.Series:
    """
    Cruza los puntos con los recintos SIGPAC (``sjoin`` con ``within``).

    Cada capa se cruza en su propio CRS. Un punto ya asignado no se vuelve a buscar en las capas
    siguientes; si cae en varios recintos se queda el primero y se cuenta el aviso.

    Parameters
    ----------
    points : gpd.GeoDataFrame
        Puntos válidos en EPSG:4326, indexados por su posición en el fichero.
    folder : Path
        Carpeta con las capas SIGPAC.
    summary : Summary
        Se actualiza ``multiple_recintos``.

    Returns
    -------
    pd.Series
        Código de uso por posición (solo los puntos con recinto).
    """
    layers = find_sigpac_layers(folder)
    if not layers:
        LOGGER.warning(f"no hay capas {SIGPAC_EXTENSIONS} en {folder}.")

    uses: dict[int, str] = {}
    remaining = points
    for path, layer in layers:
        if remaining.empty:
            break

        minx, miny, maxx, maxy = remaining.total_bounds
        area = gpd.GeoSeries(
            [box(minx, miny, maxx, maxy).buffer(BBOX_PADDING_DEG)], crs=points.crs
        )
        recintos = read_recintos(path, layer, area)
        if recintos is None:
            continue

        joined = gpd.sjoin(remaining.to_crs(recintos.crs), recintos, predicate="within")
        duplicated = joined.index.duplicated(keep="first")
        summary.multiple_recintos += int(duplicated.sum())

        first = joined.loc[~duplicated, "uso"].dropna().astype(str)
        uses.update(first.to_dict())
        remaining = remaining.drop(index=first.index)

    if summary.multiple_recintos:
        LOGGER.warning(
            f"{summary.multiple_recintos} puntos caen en varios recintos; se usa el 1.º."
        )

    return pd.Series(uses, dtype="object")


# --- Composición del resultado -------------------------------------------------------------


def flags(land_cover: pd.Series, confidence: pd.Series, min_confidence: float) -> tuple:
    """``is_agricultural`` e ``is_forest`` (booleanos; vacíos si no hay clase)."""
    confident = confidence >= min_confidence
    no_data = land_cover.isna()
    is_agricultural = ((land_cover == "crops") & confident).astype("boolean")
    is_forest = ((land_cover == "trees") & confident).astype("boolean")

    return is_agricultural.mask(no_data), is_forest.mask(no_data)


def enrich(
    df: pd.DataFrame,
    sigpac_dir: Path,
    sigpac_confidence: float = SIGPAC_CONFIDENCE,
    min_confidence: float = FLAG_MIN_CONFIDENCE,
) -> tuple[pd.DataFrame, Summary]:
    """
    Añade las columnas de cobertura del suelo a una copia de ``df``.

    Parameters
    ----------
    df : pd.DataFrame
        Puntos con ``lat`` / ``lon`` (o geometría).
    sigpac_dir : Path
        Carpeta con los recintos SIGPAC (shapefile o GeoPackage).
    sigpac_confidence : float
        Confianza de los puntos resueltos con SIGPAC.
    min_confidence : float
        Umbral de confianza para ``is_agricultural`` / ``is_forest``.

    Returns
    -------
    tuple[pd.DataFrame, Summary]
        Datos enriquecidos y resumen del proceso.
    """
    summary = Summary(total=len(df))
    lats, lons = read_coordinates(df)
    lats, lons = lats.reset_index(drop=True), lons.reset_index(drop=True)
    valid = valid_coordinates(lats, lons)
    summary.invalid = int((~valid).sum())

    land_cover = pd.Series(None, index=lats.index, dtype="object")
    confidence = pd.Series(np.nan, index=lats.index, dtype="float64")
    source = pd.Series(None, index=lats.index, dtype="object")
    sigpac_uso = pd.Series(None, index=lats.index, dtype="object")

    valid_index = lats.index[valid.to_numpy()]
    points = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(lons[valid], lats[valid]), index=valid_index, crs=4326
    )
    uses = match_sigpac(points, sigpac_dir, summary)
    unknown = set()
    for position, code in uses.items():
        land_cover[position], known = normalize_sigpac_use(code)
        sigpac_uso[position] = code.strip().upper()
        if not known:
            unknown.add(sigpac_uso[position])

    confidence[uses.index] = sigpac_confidence
    source[uses.index] = SOURCE_SIGPAC
    summary.sigpac = len(uses)
    summary.unknown_sigpac_codes = sorted(unknown)

    summary.no_data = summary.total - summary.invalid - summary.sigpac

    is_agricultural, is_forest = flags(land_cover, confidence, min_confidence)

    enriched = rename_colliding_columns(df, NEW_COLUMNS)
    for name, values in (
        (COL_LAND_COVER, land_cover),
        (COL_CONFIDENCE, confidence),
        (COL_SOURCE, source),
        (COL_SIGPAC_USO, sigpac_uso),
        (COL_IS_AGRICULTURAL, is_agricultural),
        (COL_IS_FOREST, is_forest),
    ):
        enriched[name] = values.set_axis(enriched.index)  # conserva el tipo (boolean, float)

    return enriched, summary


# --- Resumen --------------------------------------------------------------------------------


def log_summary(summary: Summary) -> None:
    """Registra el resumen del enriquecimiento y la atribución de SIGPAC."""
    unknown = ", ".join(summary.unknown_sigpac_codes) or "ninguno"
    LOGGER.info(
        "Cobertura SIGPAC: %d detecciones, %d con recinto, %d sin datos, %d coordenadas "
        "inválidas; códigos no reconocidos (-> %s): %s",
        summary.total,
        summary.sigpac,
        summary.no_data,
        summary.invalid,
        OTHER,
        unknown,
    )
    LOGGER.info("%s. %s", ATTRIBUTIONS[0], SIGPAC_NOTICE)
