"""
Enriches coordinates with land cover using SIGPAC (FEGA parcels, "recintos").

Added columns (names in the constants below): ``land_cover``, ``land_cover_confidence``,
``land_cover_source``, ``sigpac_use``, ``is_agricultural`` and ``is_forest``. Points that do not
fall in any parcel keep empty fields.

If a point falls in several parcels (e.g. on the boundary between forest and cropland),
``land_cover`` and ``sigpac_use`` contain all the distinct values separated by ``|`` (sorted,
e.g. ``crops|trees`` and ``FO|TA``) and the ``is_agricultural`` / ``is_forest`` flags can both
be ``True``.

Land cover: SIGPAC (FEGA / Ministerio de Agricultura, Pesca y Alimentación)
Check the SIGPAC reuse conditions in the FEGA legal notice before publishing.

It is invoked from the main pipeline (``land_cover`` section of the configuration).
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

# --- Column names --------------------------------------------------------------------------
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

# --- Parameters -----------------------------------------------------------------------------
SIGPAC_CONFIDENCE = 0.95  # confidence assigned to the points that fall in a SIGPAC parcel
FLAG_MIN_CONFIDENCE = 0.0  # optional threshold for is_agricultural / is_forest
SIGPAC_USE_FIELDS = ("uso_sigpac", "uso")  # possible names of the use field (lowercase)
SIGPAC_EXTENSIONS = (".shp", ".gpkg")
BBOX_PADDING_DEG = 0.01  # bbox margin with which the parcels are read

POSTGIS_CONNECT_TIMEOUT_S = 5
POSTGIS_CHUNK_POINTS = 50_000  # points per query
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
MULTI_SEPARATOR = "|"  # separates several values in land_cover / sigpac_use

# --- Normalization of SIGPAC uses (editable) ----------------------------------------------
SIGPAC_TO_LAND_COVER = {
    # Herbaceous crops
    "TA": "crops",  # arable land
    "TH": "crops",  # vegetable garden
    "IV": "crops",  # greenhouses and crops under plastic
    # Woody crops
    "OV": "crops",  # olive grove
    "VI": "crops",  # vineyard
    "FY": "crops",  # fruit trees
    "FS": "crops",  # nuts
    "CI": "crops",  # citrus
    "CF": "crops",  # citrus-fruit trees
    "CS": "crops",  # citrus-nuts
    "CV": "crops",  # citrus-vineyard
    "FF": "crops",  # fruit trees-nuts
    "FL": "crops",  # nuts and olive grove
    "FV": "crops",  # nuts and vineyard
    "OC": "crops",  # olive grove-citrus
    "OF": "crops",  # olive grove-fruit trees
    "VF": "crops",  # vineyard-fruit trees
    "VO": "crops",  # vineyard-olive grove
    # Forest and pasture
    "FO": "trees",  # forest
    "PA": "shrub_and_scrub",  # wooded pasture
    "PR": "shrub_and_scrub",  # shrub pasture
    "PS": "grass",  # grassland
    # Artificial, water and unproductive
    "ZU": "built",  # urban area
    "ED": "built",  # buildings
    "CA": "built",  # roads
    "AG": "water",  # streams and water surfaces
    "IM": "bare",  # unproductive
    # No direct equivalent
    "EP": OTHER,  # landscape element
    "ZC": OTHER,  # concentrated zone
    "ZV": OTHER,  # censored zone
}

ATTRIBUTIONS = ("Land cover: SIGPAC (FEGA / Ministerio de Agricultura, Pesca y Alimentación)",)
SIGPAC_NOTICE = (
    "Check the SIGPAC reuse conditions in the FEGA legal notice "
    "before redistributing this data."
)
LOGGER = logging.getLogger(__name__)


class LandCoverError(ValueError):
    """Invalid input for the enrichment."""


@dataclass
class Summary:
    """Counts of the process, for the console and the metadata."""

    total: int = 0
    invalid: int = 0
    sigpac: int = 0
    no_data: int = 0
    multiple_recintos: int = 0
    unknown_sigpac_codes: list[str] = field(default_factory=list)


# --- Input and validation -------------------------------------------------------------------


def read_coordinates(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """
    Returns numeric ``(lat, lon)`` without adding columns.

    Uses ``LAT_COLUMN`` / ``LON_COLUMN`` or, if they do not exist, the geometry (in EPSG:4326).
    """
    if LAT_COLUMN in df.columns and LON_COLUMN in df.columns:
        lats = pd.to_numeric(df[LAT_COLUMN], errors="coerce")
        lons = pd.to_numeric(df[LON_COLUMN], errors="coerce")

        return lats, lons

    if isinstance(df, gpd.GeoDataFrame):
        points = df.geometry.to_crs(4326) if df.crs is not None else df.geometry

        return points.y, points.x

    raise LandCoverError(f"The columns '{LAT_COLUMN}' and '{LON_COLUMN}' are missing.")


def valid_coordinates(lats: pd.Series, lons: pd.Series) -> pd.Series:
    """Mask of points with numeric, in-range lat/lon."""
    return lats.between(-90, 90) & lons.between(-180, 180)


def rename_colliding_columns(df: pd.DataFrame, names: Sequence[str]) -> pd.DataFrame:
    """
    Renames with ``_prev`` the existing columns that match ``names``.

    Raises
    ------
    LandCoverError
        If ``<name>_prev`` already exists (nothing is overwritten).
    """
    renames = {}
    for name in names:
        if name not in df.columns:
            continue

        if name + PREV_SUFFIX in df.columns:
            raise LandCoverError(
                f"'{name}' and '{name}{PREV_SUFFIX}' both exist: rename them first."
            )

        renames[name] = name + PREV_SUFFIX
        LOGGER.warning(f"the column '{name}' already existed; renamed to '{name}{PREV_SUFFIX}'.")

    return df.rename(columns=renames)


# --- SIGPAC ---------------------------------------------------------------------------------


def find_sigpac_layers(folder: Path) -> list[tuple[Path, str | None]]:
    """
    Lists the SIGPAC layers of a folder (searches recursively).

    Returns
    -------
    list[tuple[Path, str | None]]
        ``(file, layer)``; the layer is ``None`` for shapefiles.
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
    """Returns the real name of the SIGPAC use field (case-insensitive)."""
    by_lower = {column.lower(): column for column in columns}
    for candidate in SIGPAC_USE_FIELDS:
        if candidate in by_lower:
            return by_lower[candidate]

    return None


def normalize_sigpac_use(code: str) -> tuple[str, bool]:
    """
    Normalizes a SIGPAC use code.

    Returns
    -------
    tuple[str, bool]
        ``(class, recognized)``; unknown codes return ``("other", False)``.
    """
    normalized = code.strip().upper()
    if normalized in SIGPAC_TO_LAND_COVER:
        return SIGPAC_TO_LAND_COVER[normalized], True

    return OTHER, False


def read_recintos(path: Path, layer: str | None, area: gpd.GeoSeries) -> gpd.GeoDataFrame | None:
    """
    Reads the parcels of a layer that touch ``area`` and checks their CRS and their use field.

    Returns
    -------
    gpd.GeoDataFrame | None
        Parcels with a single ``uso`` column, or ``None`` if the layer is not usable.
    """
    label = f"{path.name}" + (f":{layer}" if layer else "")
    try:
        recintos = gpd.read_file(path, layer=layer, bbox=area)
    except Exception as error:  # corrupt layer or unreadable format: skipped
        LOGGER.warning(f"could not read {label}: {error}")
        return None

    if recintos.empty:
        return None

    if recintos.crs is None:
        LOGGER.warning(f"{label} has no CRS defined; skipping it.")
        return None

    use_field = find_use_field(recintos.columns)
    if use_field is None:
        LOGGER.warning(f"{label} has no use field ({', '.join(SIGPAC_USE_FIELDS)}).")
        return None

    recintos = recintos[[use_field, "geometry"]].rename(columns={use_field: "uso"})

    return recintos


def match_sigpac(points: gpd.GeoDataFrame, folder: Path, summary: Summary) -> pd.Series:
    """
    Joins the points with the SIGPAC parcels (``sjoin`` with ``within``).

    Each layer is joined in its own CRS. If a point falls in several parcels (of the same layer or
    of different layers) all its distinct use codes are kept.

    Parameters
    ----------
    points : gpd.GeoDataFrame
        Valid points in EPSG:4326, indexed by their position in the file.
    folder : Path
        Folder with the SIGPAC layers.
    summary : Summary
        ``multiple_recintos`` is updated.

    Returns
    -------
    pd.Series
        List of use codes per position (only the points with a parcel).
    """
    layers = find_sigpac_layers(folder)
    if not layers:
        LOGGER.warning(f"no layers {SIGPAC_EXTENSIONS} in {folder}.")

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
    """Converts ``{position: {codes}}`` into a Series of sorted lists (counts multiples)."""
    series = pd.Series({i: sorted(codes) for i, codes in uses.items()}, dtype="object")
    summary.multiple_recintos = int(sum(len(codes) > 1 for codes in series))
    if summary.multiple_recintos:
        LOGGER.warning(
            f"{summary.multiple_recintos} points fall in several uses; all are stored with "
            f"'{MULTI_SEPARATOR}'."
        )

    return series


def match_postgis(points: gpd.GeoDataFrame, url: str, summary: Summary) -> pd.Series:
    """
    Joins the points with ``fire.sigpac_recintos`` in PostGIS (``ST_Intersects`` with GiST index).

    If the database is not available the error is logged and no point is assigned.

    Parameters
    ----------
    points : gpd.GeoDataFrame
        Valid points in EPSG:4326, indexed by their position in the file.
    url : str
        PostgreSQL/PostGIS connection URL.
    summary : Summary
        ``multiple_recintos`` is updated.

    Returns
    -------
    pd.Series
        List of use codes per position (only the points with a parcel).
    """
    try:
        import psycopg
    except ImportError:
        LOGGER.error("Missing driver: pip install 'psycopg[binary]'. SIGPAC is not applied.")
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
    except Exception as error:
        # database down or table missing: the pipeline continues without SIGPAC
        LOGGER.error(f"could not query SIGPAC in PostGIS: {error}")
        return pd.Series(dtype="object")

    return _multi_use_series(uses, summary)


# --- Composition of the result -------------------------------------------------------------


def flags(land_cover: pd.Series, confidence: pd.Series, min_confidence: float) -> tuple:
    """
    ``is_agricultural`` and ``is_forest`` (booleans; empty if there is no class).

    ``land_cover`` can contain several classes separated by ``|``: the flag is ``True`` if the
    class is among them (a point between forest and cropland is ``True`` in both).
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
    Adds the land cover columns to a copy of ``df``.

    Parameters
    ----------
    df : pd.DataFrame
        Points with ``lat`` / ``lon`` (or geometry).
    sigpac_dir : Path | None
        Folder with the SIGPAC parcels (shapefile or GeoPackage). Ignored if there is a
        ``database_url``.
    sigpac_confidence : float
        Confidence of the points resolved with SIGPAC.
    min_confidence : float
        Confidence threshold for ``is_agricultural`` / ``is_forest``.
    database_url : str | None
        If given, the parcels are queried in ``fire.sigpac_recintos`` (PostGIS).

    Returns
    -------
    tuple[pd.DataFrame, Summary]
        Enriched data and summary of the process.
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
        raise LandCoverError("Provide 'database_url' or 'sigpac_dir'.")
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
        enriched[name] = values.set_axis(enriched.index)  # keeps the type (boolean, float)

    return enriched, summary


# --- Summary --------------------------------------------------------------------------------


def log_summary(summary: Summary) -> None:
    """Logs the summary of the enrichment and the SIGPAC attribution."""
    unknown = ", ".join(summary.unknown_sigpac_codes) or "none"
    LOGGER.info(
        "SIGPAC land cover: %d detections, %d with a parcel, %d without data, %d invalid "
        "coordinates; unrecognized codes (-> %s): %s",
        summary.total,
        summary.sigpac,
        summary.no_data,
        summary.invalid,
        OTHER,
        unknown,
    )
    LOGGER.info("%s. %s", ATTRIBUTIONS[0], SIGPAC_NOTICE)
