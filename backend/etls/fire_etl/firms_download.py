"""
Descarga del archivo anual de NASA FIRMS (MODIS y VIIRS) para un país.

Usa el archivo público ``<base_url>/<fuente>/<año>/<fuente>_<año>_<país>.csv``: no necesita cuenta
ni token y cada CSV ya viene recortado a España. Las descargas son idempotentes (un fichero ya
descargado no se vuelve a pedir) y atómicas (se escribe en ``.part`` y se renombra al terminar).

El año en curso todavía no está en el archivo; para los datos recientes habrá que usar los
ficheros NRT o la API (ver README).
"""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fire_etl.config import DownloadConfig

LOGGER = logging.getLogger(__name__)

USER_AGENT = "fire-etl"
CHUNK_BYTES = 1 << 20
CSV_START = b"latitude,"
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR = 500
DOWNLOADED, SKIPPED, MISSING = "downloaded", "skipped", "missing"


class UnexpectedContentError(RuntimeError):
    """El servidor respondió algo que no es un CSV de FIRMS (p. ej. una página de login)."""


@dataclass(frozen=True)
class DownloadItem:
    """
    Un fichero anual por descargar.

    Attributes
    ----------
    source : str
        Fuente de FIRMS (``modis``, ``viirs-snpp``, ``viirs-jpss1``).
    year : int
        Año.
    url : str
        URL completa.
    path : Path
        Destino local.
    """

    source: str
    year: int
    url: str
    path: Path


@dataclass
class DownloadResult:
    """Resultado de la descarga, por categoría."""

    downloaded: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def available(self) -> list[Path]:
        """Ficheros presentes en disco tras la descarga (nuevos y ya existentes)."""
        return sorted([*self.downloaded, *self.skipped])


def last_complete_year(today: datetime | None = None) -> int:
    """Último año completo: el año en curso aún no está en el archivo de FIRMS."""
    return (today or datetime.now(timezone.utc)).year - 1


def build_url(config: DownloadConfig, source: str, year: int) -> str:
    """URL del CSV anual de una fuente."""
    base = config.base_url.rstrip("/")

    return f"{base}/{source}/{year}/{source}_{year}_{config.country}.csv"


def plan_downloads(
    config: DownloadConfig, directory: str | Path | None = None, today: datetime | None = None
) -> list[DownloadItem]:
    """
    Lista los ficheros a descargar: cada fuente desde su primer año con datos.

    Parameters
    ----------
    config : DownloadConfig
        Fuentes, rango de años y país.
    directory : str | Path | None
        Carpeta destino; por defecto ``config.directory``.
    today : datetime | None
        Fecha de referencia para el último año completo (solo para tests).

    Returns
    -------
    list[DownloadItem]
        Ordenados por fuente y año.
    """
    target = Path(directory or config.directory)
    last_complete = last_complete_year(today)
    end_year = last_complete if config.end_year is None else min(config.end_year, last_complete)
    if config.end_year is not None and config.end_year > last_complete:
        LOGGER.warning(
            "download.end_year=%s supera el último año completo (%s): el archivo anual no tiene "
            "el año en curso (se cubre con el NRT).",
            config.end_year,
            last_complete,
        )
    items = []
    for source, first_year in config.sources.items():
        for year in range(max(config.start_year, first_year), end_year + 1):
            name = f"{source}_{year}_{config.country}.csv"
            url = build_url(config, source, year)
            items.append(DownloadItem(source, year, url, target / name))

    return items


def fetch_to_file(
    url: str,
    destination: Path,
    timeout_s: float,
    headers: dict[str, str] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> str:
    """
    Descarga un CSV de FIRMS de forma atómica (``.part`` + renombrado).

    Parameters
    ----------
    url : str
        Dirección del fichero.
    destination : Path
        Fichero final.
    timeout_s : float
        Tiempo máximo de la petición.
    headers : dict[str, str] | None
        Cabeceras extra (p. ej. ``Authorization``).
    opener : urllib.request.OpenerDirector | None
        Cliente HTTP alternativo (p. ej. uno que no siga redirecciones).

    Returns
    -------
    str
        ``DOWNLOADED`` o ``MISSING`` (404).

    Raises
    ------
    UnexpectedContentError
        Si la respuesta no es un CSV de FIRMS (p. ej. una página de login).
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    open_url = opener.open if opener is not None else urllib.request.urlopen
    partial = destination.with_name(destination.name + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open_url(request, timeout=timeout_s) as response:
            first = response.read(CHUNK_BYTES)
            if not first.startswith(CSV_START):
                raise UnexpectedContentError(f"{url} no devuelve un CSV de FIRMS")

            with partial.open("wb") as handle:
                handle.write(first)
                while chunk := response.read(CHUNK_BYTES):
                    handle.write(chunk)
    except urllib.error.HTTPError as error:
        partial.unlink(missing_ok=True)
        if error.code == HTTP_NOT_FOUND:
            return MISSING

        raise
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    os.replace(partial, destination)

    return DOWNLOADED


def _fetch(item: DownloadItem, config: DownloadConfig) -> str:
    """Descarga un fichero anual de forma atómica."""
    return fetch_to_file(item.url, item.path, config.timeout_s)


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= HTTP_SERVER_ERROR

    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))


def download_item(item: DownloadItem, config: DownloadConfig) -> str:
    """
    Descarga un fichero con reintentos. Un 404 no se reintenta (el año no existe).

    Returns
    -------
    str
        ``downloaded``, ``skipped`` (ya existía) o ``missing`` (404).

    Raises
    ------
    Exception
        El último error si se agotan los reintentos.
    """
    if item.path.exists() and item.path.stat().st_size > 0:
        return SKIPPED

    def action() -> str:
        return _fetch(item, config)

    return with_retries(action, config.retries, config.retry_wait_s, item.url)


def with_retries(action: Callable[[], str], retries: int, wait_s: float, label: str) -> str:
    """
    Ejecuta ``action`` reintentando los errores de red o de servidor (no los demás).

    Raises
    ------
    Exception
        El último error si se agotan los reintentos, o el primero que no sea reintentable.
    """
    for attempt in range(1, retries + 1):
        try:
            return action()
        except Exception as error:
            if attempt == retries or not _is_retryable(error):
                raise

            LOGGER.warning("Reintento %s/%s de %s: %s", attempt, retries, label, error)
            time.sleep(wait_s * attempt)

    raise AssertionError("inalcanzable")


def download_all(
    config: DownloadConfig, directory: str | Path | None = None, today: datetime | None = None
) -> DownloadResult:
    """
    Descarga todos los ficheros anuales que falten (MODIS y VIIRS de España).

    Un fallo en un fichero no detiene el resto: queda en ``failed`` con su motivo.

    Parameters
    ----------
    config : DownloadConfig
        Fuentes, rango de años y país.
    directory : str | Path | None
        Carpeta destino; por defecto ``config.directory``.
    today : datetime | None
        Fecha de referencia para el último año completo (solo para tests).

    Returns
    -------
    DownloadResult
        Ficheros descargados, ya existentes, inexistentes en el servidor y fallidos.
    """
    result = DownloadResult()
    plan = plan_downloads(config, directory, today)
    missing_files = sum(1 for item in plan if not item.path.exists())
    LOGGER.info(
        "Archivo anual FIRMS: %s ficheros previstos, %s por descargar.%s",
        len(plan),
        missing_files,
        " La primera vez tarda unos minutos." if missing_files > 10 else "",
    )
    for item in plan:
        try:
            status = download_item(item, config)
        except Exception as error:  # un fichero fallido no detiene el resto
            LOGGER.error("No se pudo descargar %s: %s", item.url, error)
            result.failed[item.url] = str(error)
            continue

        if status == MISSING:
            LOGGER.warning("No existe en el archivo: %s", item.url)
            result.missing.append(item.url)
        elif status == SKIPPED:
            result.skipped.append(item.path)
        else:
            LOGGER.info("Descargado %s", item.path.name)
            result.downloaded.append(item.path)

    return result
