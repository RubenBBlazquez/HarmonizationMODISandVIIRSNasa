"""
Enriquece coordenadas con la cobertura del suelo usando SIGPAC (recintos del FEGA).

Columnas añadidas (nombres en las constantes de abajo): ``land_cover``,
``land_cover_confidence``, ``land_cover_source``, ``sigpac_use``, ``is_agricultural`` e
``is_forest``. Los puntos que no caen en ningún recinto quedan con los campos vacíos.

Si un punto cae en varios recintos (p. ej. en el límite entre bosque y cultivo), ``land_cover`` y
``sigpac_use`` contienen todos los valores distintos separados por ``|`` (ordenados, p. ej.
``crops|trees`` y ``FO|TA``) y las banderas ``is_agricultural`` / ``is_forest`` pueden ser ambas
``True``.

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
COL_SIGPAC_USO = "sigpac_use"
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

POSTGIS_CONNECT_TIMEOUT_S = 5
POSTGIS_CHUNK_POINTS = 50_000  # puntos por consulta
POSTGIS_QUERY = """
    SELECT p.i, r.uso_sigpac
    FROM unnest(%s::int[], %s::float8[], %s::float8[]) AS p(i, lon, lat)
    JOIN LATERAL (
        SELECT DISTINCT uso_sigpac FROM fire.sigpac_recintos
        WHERE ST_Intersects(geom, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326))
    ) r ON true
"""

SOURCE_SIGPAC = "sigpac"
OTHER = "other"
MULTI_SEPARATOR = "|"  # separa varios valores en land_cover / sigpac_use

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

    Cada capa se cruza en su propio CRS. Si un punto cae en varios recintos (de la misma capa o
    de capas distintas) se conservan todos sus códigos de uso distintos.

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
        Lista de códigos de uso por posición (solo los puntos con recinto).
    """
    layers = find_sigpac_layers(folder)
    if not layers:
        LOGGER.warning(f"no hay capas {SIGPAC_EXTENSIONS} en {folder}.")

    uses: dict[int, set[str]] = {}
    for path, layer in layers:
        if points.empty:
            break

        minx, miny, maxx, maxy = points.total_bounds
        area = gpd.GeoSeries(
            [box(minx, miny, maxx, maxy).buffer(BBOX_PADDING_DEG)], crs=points.crs
        )
        recintos = read_recintos(path, layer, area)
        if recintos is None:
            continue

        joined = gpd.sjoin(points.to_crs(recintos.crs), recintos, predicate="within")
        for position, code in joined["uso"].dropna().astype(str).items():
            uses.setdefault(int(position), set()).add(code)

    return _multi_use_series(uses, summary)


def _multi_use_series(uses: dict[int, set[str]], summary: Summary) -> pd.Series:
    """Convierte ``{posición: {códigos}}`` en una Serie de listas ordenadas (cuenta múltiples)."""
    series = pd.Series({i: sorted(codes) for i, codes in uses.items()}, dtype="object")
    summary.multiple_recintos = int(sum(len(codes) > 1 for codes in series))
    if summary.multiple_recintos:
        LOGGER.warning(
            f"{summary.multiple_recintos} puntos caen en varios usos; se guardan todos con "
            f"'{MULTI_SEPARATOR}'."
        )

    return series


def match_postgis(points: gpd.GeoDataFrame, url: str, summary: Summary) -> pd.Series:
    """
    Cruza los puntos con ``fire.sigpac_recintos`` en PostGIS (``ST_Intersects`` con índice GiST).

    Si la base de datos no está disponible se registra el error y no se asigna ningún punto.

    Parameters
    ----------
    points : gpd.GeoDataFrame
        Puntos válidos en EPSG:4326, indexados por su posición en el fichero.
    url : str
        URL de conexión a PostgreSQL/PostGIS.
    summary : Summary
        Se actualiza ``multiple_recintos``.

    Returns
    -------
    pd.Series
        Lista de códigos de uso por posición (solo los puntos con recinto).
    """
    try:
        import psycopg
    except ImportError:
        LOGGER.error("Falta el driver: pip install 'psycopg[binary]'. SIGPAC no se aplica.")
        return pd.Series(dtype="object")

    uses: dict[int, set[str]] = {}
    positions = points.index.to_list()
    lons = points.geometry.x.to_list()
    lats = points.geometry.y.to_list()
    try:
        with psycopg.connect(url, connect_timeout=POSTGIS_CONNECT_TIMEOUT_S) as connection:
            with connection.cursor() as cursor:
                for start in range(0, len(positions), POSTGIS_CHUNK_POINTS):
                    stop = start + POSTGIS_CHUNK_POINTS
                    cursor.execute(
                        POSTGIS_QUERY,
                        (positions[start:stop], lons[start:stop], lats[start:stop]),
                    )
                    for i, uso in cursor.fetchall():
                        if uso:
                            uses.setdefault(int(i), set()).add(str(uso))
    except Exception as error:  # BD caída o tabla ausente: el pipeline continúa sin SIGPAC
        LOGGER.error(f"no se pudo consultar SIGPAC en PostGIS: {error}")
        return pd.Series(dtype="object")

    return _multi_use_series(uses, summary)


# --- Composición del resultado -------------------------------------------------------------


def flags(land_cover: pd.Series, confidence: pd.Series, min_confidence: float) -> tuple:
    """
    ``is_agricultural`` e ``is_forest`` (booleanos; vacíos si no hay clase).

    ``land_cover`` puede traer varias clases separadas por ``|``: la bandera es ``True`` si la
    clase está entre ellas (un punto entre bosque y cultivo es ``True`` en ambas).
    """
    confident = confidence >= min_confidence
    no_data = land_cover.isna()
    classes = land_cover.fillna("").str.split(MULTI_SEPARATOR, regex=False)
    is_agricultural = (classes.map(lambda c: "crops" in c) & confident).astype("boolean")
    is_forest = (classes.map(lambda c: "trees" in c) & confident).astype("boolean")

    return is_agricultural.mask(no_data), is_forest.mask(no_data)


def enrich(
    df: pd.DataFrame,
    sigpac_dir: Path | None = None,
    sigpac_confidence: float = SIGPAC_CONFIDENCE,
    min_confidence: float = FLAG_MIN_CONFIDENCE,
    database_url: str | None = None,
) -> tuple[pd.DataFrame, Summary]:
    """
    Añade las columnas de cobertura del suelo a una copia de ``df``.

    Parameters
    ----------
    df : pd.DataFrame
        Puntos con ``lat`` / ``lon`` (o geometría).
    sigpac_dir : Path | None
        Carpeta con los recintos SIGPAC (shapefile o GeoPackage). Se ignora si hay
        ``database_url``.
    sigpac_confidence : float
        Confianza de los puntos resueltos con SIGPAC.
    min_confidence : float
        Umbral de confianza para ``is_agricultural`` / ``is_forest``.
    database_url : str | None
        Si se indica, los recintos se consultan en ``fire.sigpac_recintos`` (PostGIS).

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
    sigpac_use = pd.Series(None, index=lats.index, dtype="object")

    valid_index = lats.index[valid.to_numpy()]
    points = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(lons[valid], lats[valid]), index=valid_index, crs=4326
    )
    if database_url:
        uses = match_postgis(points, database_url, summary)
    elif sigpac_dir is not None:
        uses = match_sigpac(points, sigpac_dir, summary)
    else:
        raise LandCoverError("Indica 'database_url' o 'sigpac_dir'.")
    unknown = set()
    for position, codes in uses.items():
        normalized = [normalize_sigpac_use(code) for code in codes]
        cleaned = sorted({code.strip().upper() for code in codes})
        land_cover[position] = MULTI_SEPARATOR.join(sorted({cls for cls, _ in normalized}))
        sigpac_use[position] = MULTI_SEPARATOR.join(cleaned)
        unknown.update(
            code.strip().upper() for code, (_, known) in zip(codes, normalized) if not known
        )

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
        (COL_SIGPAC_USO, sigpac_use),
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
