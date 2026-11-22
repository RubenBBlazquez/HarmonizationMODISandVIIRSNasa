"""
Loading of the country borders (Natural Earth 1:50m) into ``fire.countries``.

The API uses them to filter fires by country (``?country=ESP``) and the globe of the frontend
to draw and select countries. The table is a reference table: the pipeline load does not empty
it, and reloading it replaces every country in one transaction.

The key of each country is Natural Earth's ``ADM0_A3`` (``ESP``, ``PRT``, ``FRA``...), which is
always informed (``ISO_A3`` is ``-99`` for France or Norway).

Borders: Natural Earth (public domain).
"""

from __future__ import annotations

import logging
import urllib.request
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol

import geopandas as gpd
import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

LOGGER = logging.getLogger(__name__)

TABLE = "fire.countries"
STORAGE_SRID = 4326
NATURAL_EARTH_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip"
DOWNLOAD_TIMEOUT_S = 120
# Natural Earth column -> table column.
COLUMNS = {"ADM0_A3": "code", "NAME": "name", "NAME_ES": "name_es", "CONTINENT": "continent"}
INSERT_SQL = (
    f"INSERT INTO {TABLE} (code, name, name_es, continent, geom) "
    f"VALUES (%s, %s, %s, %s, ST_Multi(ST_GeomFromWKB(%s, {STORAGE_SRID})))"
)


class CountriesLoadError(RuntimeError):
    """The borders file cannot be read or does not have the expected columns."""


class _Cursor(Protocol):
    def execute(self, query: str, params: Sequence[Any] | None = None) -> Any: ...

    def executemany(self, query: str, params_seq: Sequence[Sequence[Any]]) -> Any: ...


class _Connection(Protocol):
    def transaction(self) -> AbstractContextManager[Any]: ...

    def cursor(self) -> AbstractContextManager[_Cursor]: ...


def download_borders(destination: Path, url: str = NATURAL_EARTH_URL) -> Path:
    """
    Downloads the Natural Earth zip unless it already exists (atomic: ``.part`` + rename).

    Parameters
    ----------
    destination : Path
        Path of the zip.
    url : str
        Natural Earth URL.

    Returns
    -------
    Path
        ``destination``.
    """
    if destination.exists():
        LOGGER.info("Using the already downloaded %s", destination)
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    LOGGER.info("Downloading %s", url)
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310
        partial.write_bytes(response.read())
    partial.replace(destination)

    return destination


def read_borders(path: Path) -> gpd.GeoDataFrame:
    """
    Reads the Natural Earth countries (zip or shapefile) in EPSG:4326 with the table columns.

    Raises
    ------
    CountriesLoadError
        If a column of ``COLUMNS`` is missing.
    """
    source = f"zip://{path}" if path.suffix == ".zip" else str(path)
    frame = gpd.read_file(source)
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise CountriesLoadError(f"{path}: missing columns {sorted(missing)}")

    frame = frame.to_crs(STORAGE_SRID) if frame.crs else frame.set_crs(STORAGE_SRID)

    return frame[[*COLUMNS, "geometry"]].rename(columns=COLUMNS)


def as_multipolygon(geometry: BaseGeometry) -> MultiPolygon | None:
    """Valid multipolygon of a border, or ``None`` if nothing polygonal is left."""
    if geometry is None or geometry.is_empty:
        return None

    valid = shapely.make_valid(geometry)
    if isinstance(valid, Polygon):
        return MultiPolygon([valid])

    if isinstance(valid, MultiPolygon):
        return valid

    # Repairing can produce a collection with lines or points: only the polygons are kept.
    parts = getattr(valid, "geoms", ())
    polygons = [
        polygon
        for part in parts
        for polygon in (part.geoms if isinstance(part, MultiPolygon) else [part])
        if isinstance(polygon, Polygon) and not polygon.is_empty
    ]

    return MultiPolygon(polygons) if polygons else None


def _text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and value != value):  # NaN
        return None

    text = str(value).strip()

    return text or None


def prepare_rows(frame: gpd.GeoDataFrame) -> list[tuple[Any, ...]]:
    """
    Rows ``(code, name, name_es, continent, wkb)`` ready to insert.

    Countries without a code or without a polygonal geometry are skipped; if a code is repeated
    the first one is kept.
    """
    rows: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    for record in frame.itertuples(index=False):
        code = _text(record.code)
        geometry = as_multipolygon(record.geometry)
        if code is None or code in seen or geometry is None:
            LOGGER.warning("Skipping country without code or geometry: %s", record.name)
            continue

        seen.add(code)
        name = _text(record.name) or code
        rows.append(
            (code, name, _text(record.name_es), _text(record.continent), shapely.to_wkb(geometry))
        )

    return rows


def load_countries(connection: _Connection, rows: Sequence[tuple[Any, ...]]) -> int:
    """
    Replaces the content of ``fire.countries`` with ``rows`` in one transaction.

    Returns
    -------
    int
        Number of countries loaded.
    """
    if not rows:
        raise CountriesLoadError("No countries to load")

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {TABLE}")
        cursor.executemany(INSERT_SQL, rows)

    LOGGER.info("Loaded %s countries into %s", len(rows), TABLE)

    return len(rows)
