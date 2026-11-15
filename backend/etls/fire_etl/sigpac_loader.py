"""
Carga de los recintos SIGPAC (FEGA) en PostGIS, un zip provincial cada vez.

El FEGA publica un ``.zip`` por provincia con un GeoPackage dentro (capa ``recinto``, EPSG:4258,
de 150 MB a 1,3 GB). Cada zip se procesa de forma independiente:

1. Se descomprime en un directorio temporal (se borra al terminar).
2. Se lee la capa ``recinto`` con geopandas por bloques (memoria acotada) y se reproyecta a
   EPSG:4326.
3. En **una transacción**: se borran los recintos de esa provincia, se copian los nuevos con
   ``COPY`` y se anota el zip en ``fire.sigpac_loads``. Si algo falla se deshace todo y la
   provincia conserva lo que tuviera. Recargar una provincia es seguro (la sustituye).

Los zips ya registrados en ``fire.sigpac_loads`` se saltan (salvo ``force``) antes de
descomprimirlos, así que el proceso se puede interrumpir y reanudar.

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

from fire_etl.sigpac_land_cover import normalize_sigpac_use

LOGGER = logging.getLogger(__name__)

SCHEMA = "fire"
TABLE = f"{SCHEMA}.sigpac_recintos"
LOADS_TABLE = f"{SCHEMA}.sigpac_loads"
LAYER = "recinto"
STORAGE_SRID = 4326
CHUNK_ROWS = 50_000  # recintos leídos y copiados por bloque

# Listado público del FEGA (un zip por provincia). Cambia el año aquí al publicarse uno nuevo.
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
# Orden de las columnas en la tabla (``land_cover`` se deriva de ``uso_sigpac``).
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
    """Zip o GeoPackage de SIGPAC no utilizable."""


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
    Resultado de procesar un zip.

    Attributes
    ----------
    zip_name : str
        Nombre del zip.
    provincia : int | None
        Código de provincia (``None`` si se saltó sin leerlo).
    n_recintos : int
        Recintos cargados (0 si se saltó).
    skipped : bool
        ``True`` si ya estaba cargado y no se ha forzado.
    """

    zip_name: str
    provincia: int | None
    n_recintos: int
    skipped: bool


def find_zips(paths: Iterable[Path]) -> list[Path]:
    """
    Expande rutas a una lista ordenada de zips (los directorios se buscan sin recursión).

    Raises
    ------
    SigpacLoadError
        Si una ruta no existe.
    """
    zips: list[Path] = []
    for path in paths:
        if path.is_dir():
            zips.extend(sorted(path.glob("*.zip")))
        elif path.is_file():
            zips.append(path)
        else:
            raise SigpacLoadError(f"No existe: {path}")

    return zips


def extract_gpkg(zip_path: Path, dest_dir: Path) -> Path:
    """
    Descomprime el GeoPackage de un zip de SIGPAC en ``dest_dir``.

    Raises
    ------
    SigpacLoadError
        Si el fichero no es un zip o no contiene exactamente un ``.gpkg``.
    """
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".gpkg")]
            if len(members) != 1:
                raise SigpacLoadError(
                    f"{zip_path.name}: se esperaba un .gpkg y hay {len(members)}."
                )

            # ``extract`` neutraliza rutas absolutas y ``..`` en el nombre del miembro.
            return Path(archive.extract(members[0], dest_dir))
    except zipfile.BadZipFile as error:
        raise SigpacLoadError(f"{zip_path.name}: no es un zip válido ({error}).") from error


def read_chunks(gpkg: Path, chunk_rows: int = CHUNK_ROWS) -> Iterator[gpd.GeoDataFrame]:
    """
    Lee la capa ``recinto`` por bloques, ya en EPSG:4326 y solo con las columnas útiles.

    Raises
    ------
    SigpacLoadError
        Si falta la capa, alguna columna o el CRS.
    """
    layers = set(pyogrio.list_layers(gpkg)[:, 0])
    if LAYER not in layers:
        raise SigpacLoadError(f"{gpkg.name}: no tiene la capa '{LAYER}'.")

    total = pyogrio.read_info(gpkg, layer=LAYER)["features"]
    for start in range(0, total, chunk_rows):
        chunk = gpd.read_file(
            gpkg,
            layer=LAYER,
            columns=list(SOURCE_COLUMNS),
            rows=slice(start, min(start + chunk_rows, total)),
        )
        if chunk.crs is None:
            raise SigpacLoadError(f"{gpkg.name}: la capa '{LAYER}' no tiene CRS.")

        yield chunk.to_crs(STORAGE_SRID)


def prepare_rows(chunk: gpd.GeoDataFrame) -> list[tuple[Any, ...]]:
    """
    Convierte un bloque en filas para ``COPY`` (geometría como EWKB hexadecimal).

    ``land_cover`` se deriva del uso SIGPAC con ``normalize_sigpac_use``; el uso original se
    conserva.
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
    """``True`` si el zip consta en ``fire.sigpac_loads``."""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT 1 FROM {LOADS_TABLE} WHERE source_file = %s", (zip_name,))

        return cursor.fetchone() is not None


def _copy_statement() -> str:
    return f"COPY {TABLE} ({', '.join(TABLE_COLUMNS)}) FROM STDIN"


def load_gpkg(connection: _Connection, gpkg: Path, zip_name: str) -> LoadResult:
    """
    Sustituye en una transacción los recintos de la provincia del GeoPackage.

    Raises
    ------
    SigpacLoadError
        Si el GeoPackage está vacío o mezcla provincias.
    """
    chunks = read_chunks(gpkg)
    first = next(chunks, None)
    if first is None or first.empty:
        raise SigpacLoadError(f"{zip_name}: la capa '{LAYER}' está vacía.")

    provincia = int(first["provincia"].iloc[0])
    total = 0
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {TABLE} WHERE provincia = %s", (provincia,))
        for chunk in _prepend(first, chunks):
            if set(chunk["provincia"].unique()) != {provincia}:
                raise SigpacLoadError(f"{zip_name}: mezcla varias provincias.")

            with cursor.copy(_copy_statement()) as copy:
                for row in prepare_rows(chunk):
                    copy.write_row(row)

            total += len(chunk)
            LOGGER.info("  provincia %02d: %s recintos copiados", provincia, f"{total:,}")

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
    Procesa un zip: lo salta si ya está cargado; si no, lo descomprime, lo carga y limpia.

    Parameters
    ----------
    connection : _Connection
        Conexión psycopg abierta (sin transacción pendiente).
    zip_path : Path
        Zip provincial del FEGA.
    force : bool
        Recarga aunque conste como cargado.
    work_dir : Path | None
        Carpeta donde descomprimir (por defecto, una temporal del sistema).
    """
    if not force and is_loaded(connection, zip_path.name):
        LOGGER.info("%s: ya cargado, se salta (usa --force para recargar).", zip_path.name)

        return LoadResult(zip_path.name, None, 0, skipped=True)

    with tempfile.TemporaryDirectory(prefix="sigpac_", dir=work_dir) as tmp:
        LOGGER.info("%s: descomprimiendo...", zip_path.name)
        gpkg = extract_gpkg(zip_path, Path(tmp))
        result = load_gpkg(connection, gpkg, zip_path.name)

    LOGGER.info("%s: %s recintos cargados.", zip_path.name, f"{result.n_recintos:,}")

    return result


@dataclass(frozen=True)
class RemoteZip:
    """Zip publicado por el FEGA: nombre ya decodificado, URL de descarga y tamaño en bytes."""

    name: str
    url: str
    size: int | None


def list_remote_zips(base_url: str = SIGPAC_BASE_URL) -> list[RemoteZip]:
    """
    Lista los zips del directorio público del FEGA (listado HTML de IIS).

    Raises
    ------
    SigpacLoadError
        Si no se puede leer el listado o no contiene zips.
    """
    try:
        with urllib.request.urlopen(base_url, timeout=DOWNLOAD_TIMEOUT_S) as response:
            html = response.read().decode("utf-8", errors="replace")
    except OSError as error:
        raise SigpacLoadError(f"No se pudo leer el listado {base_url}: {error}") from error

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
        raise SigpacLoadError(f"No hay zips en {base_url}.")

    return remote


def download_zip(remote: RemoteZip, dest_dir: Path) -> Path:
    """
    Descarga un zip a ``dest_dir`` (con reintentos). Si ya está completo, no lo vuelve a bajar.

    Se escribe en ``<nombre>.part`` y se renombra al terminar, de modo que nunca queda un zip
    a medias con el nombre final.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / remote.name
    if target.exists() and (remote.size is None or target.stat().st_size == remote.size):
        return target

    partial = target.with_name(target.name + ".part")
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            LOGGER.info("%s: descargando (%s MB)...", remote.name, (remote.size or 0) // 10**6)
            with urllib.request.urlopen(remote.url, timeout=DOWNLOAD_TIMEOUT_S) as response:
                with partial.open("wb") as handle:
                    shutil.copyfileobj(response, handle, DOWNLOAD_BLOCK)

            if remote.size is not None and partial.stat().st_size != remote.size:
                raise OSError("descarga incompleta")

            return partial.replace(target)
        except OSError as error:
            LOGGER.warning(
                "%s: intento %d/%d falló (%s)", remote.name, attempt, DOWNLOAD_RETRIES, error
            )
            if attempt == DOWNLOAD_RETRIES:
                partial.unlink(missing_ok=True)
                raise SigpacLoadError(f"{remote.name}: no se pudo descargar ({error}).") from error

            time.sleep(2**attempt)

    raise AssertionError("inalcanzable")


def load_remote(
    connection: _Connection,
    remote: RemoteZip,
    dest_dir: Path,
    force: bool = False,
    keep_zip: bool = False,
) -> LoadResult:
    """
    Descarga (si hace falta) y carga un zip del FEGA; después borra el zip para ahorrar disco.

    Los ya cargados se saltan **antes** de descargar nada.
    """
    if not force and is_loaded(connection, remote.name):
        LOGGER.info("%s: ya cargado, se salta (usa --force para recargar).", remote.name)

        return LoadResult(remote.name, None, 0, skipped=True)

    zip_path = download_zip(remote, dest_dir)
    result = load_zip(connection, zip_path, force=True)
    if not keep_zip:
        zip_path.unlink(missing_ok=True)

    return result
