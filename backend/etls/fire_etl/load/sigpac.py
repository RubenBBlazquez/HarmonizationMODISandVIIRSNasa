"""
Loading of the SIGPAC parcels (FEGA) into PostGIS, one provincial zip at a time.

The FEGA publishes one ``.zip`` per province with a GeoPackage inside (``recinto`` layer,
EPSG:4258, from 150 MB to 1.3 GB). Each zip is processed independently:

1. It is unzipped into a temporary directory (deleted when finished).
2. The ``recinto`` layer is read with geopandas in chunks (bounded memory) and reprojected to
   EPSG:4326.
3. In **one transaction**: the parcels of that province are deleted, the new ones are copied
   with ``COPY`` and the zip is recorded in ``fire.sigpac_loads``. If anything fails everything
   is rolled back and the province keeps what it had. Reloading a province is safe (it
   replaces it).

Zips already recorded in ``fire.sigpac_loads`` are skipped (unless ``force``) before unzipping
them, so the process can be interrupted and resumed.

Land cover: SIGPAC (FEGA / Ministerio de Agricultura, Pesca y Alimentación), CC BY 4.0.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urljoin

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely

from fire_etl.transform.sigpac_land_cover import normalize_sigpac_use

LOGGER = logging.getLogger(__name__)

SCHEMA = "fire"
TABLE = f"{SCHEMA}.sigpac_recintos"
LOADS_TABLE = f"{SCHEMA}.sigpac_loads"
LAYER = "recinto"
STORAGE_SRID = 4326
CHUNK_ROWS = 50_000  # parcels read and copied per chunk

# Public FEGA listing (one zip per province). Change the year here when a new one is published.
SIGPAC_BASE_URL = "https://sigpac-hubcloud.es/geopackages/2026/recintos/"
DOWNLOAD_TIMEOUT_S = 120
DOWNLOAD_RETRIES = 3
DOWNLOAD_BLOCK = 1024 * 1024
ZIP_LINK = re.compile(r'HREF="([^"]+\.zip)"', re.IGNORECASE)

INTEGER_COLUMNS = (
    "provincia",
    "municipio",
    "agregado",
    "zona",
    "poligono",
    "parcela",
    "recinto",
    "pendiente_media",
    "altitud",
    "coef_regadio",
    "region",
)
OTHER_COLUMNS = ("dn_surface", "csp", "uso_sigpac", "incidencias")
SOURCE_COLUMNS = (*INTEGER_COLUMNS, *OTHER_COLUMNS)
# Order of the columns in the table (``land_cover`` is derived from ``uso_sigpac``).
TABLE_COLUMNS = (
    "provincia",
    "municipio",
    "agregado",
    "zona",
    "poligono",
    "parcela",
    "recinto",
    "dn_surface",
    "pendiente_media",
    "altitud",
    "csp",
    "coef_regadio",
    "uso_sigpac",
    "land_cover",
    "incidencias",
    "region",
    "geom",
)


class SigpacLoadError(RuntimeError):
    """SIGPAC zip or GeoPackage that cannot be used."""


class _Copy(Protocol):
    def write_row(self, row: Sequence[Any]) -> None: ...


class _Cursor(Protocol):
    def execute(self, query: str, params: Sequence[Any] | None = None) -> Any: ...

    def fetchone(self) -> Any: ...

    def copy(self, query: str) -> AbstractContextManager[_Copy]: ...


class _Connection(Protocol):
    def transaction(self) -> AbstractContextManager[Any]: ...

    def cursor(self) -> AbstractContextManager[_Cursor]: ...


@dataclass(frozen=True)
class LoadResult:
    """
    Result of processing a zip.

    Attributes
    ----------
    zip_name : str
        Name of the zip.
    provincia : int | None
        Province code (``None`` if it was skipped without reading it).
    n_recintos : int
        Parcels loaded (0 if skipped).
    skipped : bool
        ``True`` if it was already loaded and not forced.
    """

    zip_name: str
    provincia: int | None
    n_recintos: int
    skipped: bool


def find_zips(paths: Iterable[Path]) -> list[Path]:
    """
    Expands paths into a sorted list of zips (directories are searched without recursion).

    Raises
    ------
    SigpacLoadError
        If a path does not exist.
    """
    zips: list[Path] = []
    for path in paths:
        if path.is_dir():
            zips.extend(sorted(path.glob("*.zip")))
        elif path.is_file():
            zips.append(path)
        else:
            raise SigpacLoadError(f"Does not exist: {path}")

    return zips


def extract_gpkg(zip_path: Path, dest_dir: Path) -> Path:
    """
    Unzips the GeoPackage of a SIGPAC zip into ``dest_dir``.

    Raises
    ------
    SigpacLoadError
        If the file is not a zip or does not contain exactly one ``.gpkg``.
    """
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".gpkg")]
            if len(members) != 1:
                raise SigpacLoadError(
                    f"{zip_path.name}: expected one .gpkg and found {len(members)}."
                )

            # ``extract`` neutralizes absolute paths and ``..`` in the member name.
            return Path(archive.extract(members[0], dest_dir))
    except zipfile.BadZipFile as error:
        raise SigpacLoadError(f"{zip_path.name}: not a valid zip ({error}).") from error


def read_chunks(gpkg: Path, chunk_rows: int = CHUNK_ROWS) -> Iterator[gpd.GeoDataFrame]:
    """
    Reads the ``recinto`` layer in chunks, already in EPSG:4326 and with only the useful columns.

    Raises
    ------
    SigpacLoadError
        If the layer, a column or the CRS is missing.
    """
    layers = set(pyogrio.list_layers(gpkg)[:, 0])
    if LAYER not in layers:
        raise SigpacLoadError(f"{gpkg.name}: does not have the '{LAYER}' layer.")

    total = pyogrio.read_info(gpkg, layer=LAYER)["features"]
    for start in range(0, total, chunk_rows):
        chunk = gpd.read_file(
            gpkg,
            layer=LAYER,
            columns=list(SOURCE_COLUMNS),
            rows=slice(start, min(start + chunk_rows, total)),
        )
        if chunk.crs is None:
            raise SigpacLoadError(f"{gpkg.name}: the '{LAYER}' layer has no CRS.")

        yield chunk.to_crs(STORAGE_SRID)


def prepare_rows(chunk: gpd.GeoDataFrame) -> list[tuple[Any, ...]]:
    """
    Converts a chunk into rows for ``COPY`` (geometry as hexadecimal EWKB).

    ``land_cover`` is derived from the SIGPAC use with ``normalize_sigpac_use``; the original use
    is kept.
    """
    frame = pd.DataFrame(chunk.drop(columns="geometry"))
    for column in INTEGER_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").round().astype("Int64")

    uso = frame["uso_sigpac"].astype("string").str.strip().str.upper()
    cover = {code: normalize_sigpac_use(code)[0] for code in uso.dropna().unique()}
    frame["uso_sigpac"] = uso
    frame["land_cover"] = uso.map(cover)
    frame["incidencias"] = frame["incidencias"].astype("string")
    geometries = shapely.set_srid(chunk.geometry.to_numpy(), STORAGE_SRID)
    frame["geom"] = shapely.to_wkb(geometries, hex=True, include_srid=True)

    ordered = frame[list(TABLE_COLUMNS)].astype(object)
    ordered = ordered.where(ordered.notna(), None)

    return [tuple(row) for row in ordered.itertuples(index=False, name=None)]


def is_loaded(connection: _Connection, zip_name: str) -> bool:
    """``True`` if the zip is recorded in ``fire.sigpac_loads``."""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT 1 FROM {LOADS_TABLE} WHERE source_file = %s", (zip_name,))

        return cursor.fetchone() is not None


def _copy_statement() -> str:
    return f"COPY {TABLE} ({', '.join(TABLE_COLUMNS)}) FROM STDIN"


def load_gpkg(connection: _Connection, gpkg: Path, zip_name: str) -> LoadResult:
    """
    Replaces, in one transaction, the parcels of the GeoPackage's province.

    Raises
    ------
    SigpacLoadError
        If the GeoPackage is empty or mixes provinces.
    """
    chunks = read_chunks(gpkg)
    first = next(chunks, None)
    if first is None or first.empty:
        raise SigpacLoadError(f"{zip_name}: the '{LAYER}' layer is empty.")

    provincia = int(first["provincia"].iloc[0])
    total = 0
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {TABLE} WHERE provincia = %s", (provincia,))
        for chunk in _prepend(first, chunks):
            if set(chunk["provincia"].unique()) != {provincia}:
                raise SigpacLoadError(f"{zip_name}: mixes several provinces.")

            with cursor.copy(_copy_statement()) as copy:
                for row in prepare_rows(chunk):
                    copy.write_row(row)

            total += len(chunk)
            LOGGER.info("  province %02d: %s parcels copied", provincia, f"{total:,}")

        cursor.execute(
            f"INSERT INTO {LOADS_TABLE} (source_file, provincia, n_recintos) "
            "VALUES (%s, %s, %s) ON CONFLICT (source_file) DO UPDATE SET "
            "provincia = EXCLUDED.provincia, n_recintos = EXCLUDED.n_recintos, "
            "loaded_at = now()",
            (zip_name, provincia, total),
        )

    return LoadResult(zip_name, provincia, total, skipped=False)


def _prepend(first: gpd.GeoDataFrame, rest: Iterator[gpd.GeoDataFrame]) -> Iterator:
    yield first
    yield from rest


def load_zip(
    connection: _Connection, zip_path: Path, force: bool = False, work_dir: Path | None = None
) -> LoadResult:
    """
    Processes a zip: skips it if already loaded; otherwise unzips it, loads it and cleans up.

    Parameters
    ----------
    connection : _Connection
        Open psycopg connection (with no pending transaction).
    zip_path : Path
        Provincial FEGA zip.
    force : bool
        Reload even if it is recorded as loaded.
    work_dir : Path | None
        Folder to unzip into (by default, a system temporary one).
    """
    if not force and is_loaded(connection, zip_path.name):
        LOGGER.info("%s: already loaded, skipping (use --force to reload).", zip_path.name)

        return LoadResult(zip_path.name, None, 0, skipped=True)

    with tempfile.TemporaryDirectory(prefix="sigpac_", dir=work_dir) as tmp:
        LOGGER.info("%s: descomprimiendo...", zip_path.name)
        gpkg = extract_gpkg(zip_path, Path(tmp))
        result = load_gpkg(connection, gpkg, zip_path.name)

    LOGGER.info("%s: %s parcels loaded.", zip_path.name, f"{result.n_recintos:,}")

    return result


@dataclass(frozen=True)
class RemoteZip:
    """Zip published by the FEGA: decoded name, download URL and size in bytes."""

    name: str
    url: str
    size: int | None


def list_remote_zips(base_url: str = SIGPAC_BASE_URL) -> list[RemoteZip]:
    """
    Lists the zips of the FEGA public directory (IIS HTML listing).

    Raises
    ------
    SigpacLoadError
        If the listing cannot be read or contains no zips.
    """
    try:
        with urllib.request.urlopen(base_url, timeout=DOWNLOAD_TIMEOUT_S) as response:
            html = response.read().decode("utf-8", errors="replace")
    except OSError as error:
        raise SigpacLoadError(f"Could not read the listing {base_url}: {error}") from error

    remote = []
    for line in html.split("<br>"):
        match = ZIP_LINK.search(line)
        if match:
            size = re.search(r"\s(\d{6,})\s+<A", line, re.IGNORECASE)
            href = match.group(1)
            remote.append(
                RemoteZip(
                    unquote(href.rsplit("/", 1)[-1]),
                    urljoin(base_url, href),
                    int(size.group(1)) if size else None,
                )
            )

    if not remote:
        raise SigpacLoadError(f"No zips in {base_url}.")

    return remote


def download_zip(remote: RemoteZip, dest_dir: Path) -> Path:
    """
    Downloads a zip into ``dest_dir`` (with retries). If it is already complete, it is not
    downloaded again.

    It is written to ``<name>.part`` and renamed when finished, so a half-downloaded zip with the
    final name never remains.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / remote.name
    if target.exists() and (remote.size is None or target.stat().st_size == remote.size):
        return target

    partial = target.with_name(target.name + ".part")
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            LOGGER.info("%s: downloading (%s MB)...", remote.name, (remote.size or 0) // 10**6)
            with urllib.request.urlopen(remote.url, timeout=DOWNLOAD_TIMEOUT_S) as response:
                with partial.open("wb") as handle:
                    shutil.copyfileobj(response, handle, DOWNLOAD_BLOCK)

            if remote.size is not None and partial.stat().st_size != remote.size:
                raise OSError("incomplete download")

            return partial.replace(target)
        except OSError as error:
            LOGGER.warning(
                "%s: attempt %d/%d failed (%s)", remote.name, attempt, DOWNLOAD_RETRIES, error
            )
            if attempt == DOWNLOAD_RETRIES:
                partial.unlink(missing_ok=True)
                raise SigpacLoadError(f"{remote.name}: could not download ({error}).") from error

            time.sleep(2**attempt)

    raise AssertionError("unreachable")


def load_remote(
    connection: _Connection,
    remote: RemoteZip,
    dest_dir: Path,
    force: bool = False,
    keep_zip: bool = False,
) -> LoadResult:
    """
    Downloads (if needed) and loads a FEGA zip; then deletes the zip to save disk space.

    Those already loaded are skipped **before** downloading anything.
    """
    if not force and is_loaded(connection, remote.name):
        LOGGER.info("%s: already loaded, skipping (use --force to reload).", remote.name)

        return LoadResult(remote.name, None, 0, skipped=True)

    zip_path = download_zip(remote, dest_dir)
    result = load_zip(connection, zip_path, force=True)
    if not keep_zip:
        zip_path.unlink(missing_ok=True)

    return result
