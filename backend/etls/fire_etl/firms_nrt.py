"""
Descarga de los ficheros casi en tiempo real (NRT) de NASA FIRMS para el año en curso.

El archivo anual por país (:mod:`fire_etl.firms_download`) solo llega hasta el último año completo.
El NRT (``nrt3.modaps.eosdis.nasa.gov/archive/FIRMS/<fuente>/<región>/``) tiene un fichero por día
(``..._NRT_AAAADDD.txt``, con el día del año), pero **solo guarda los últimos ~60 días**: lo que
queda entre el fin del archivo anual y el inicio del NRT no lo cubre ninguna de las dos fuentes.

- El listado de las carpetas es público; **los ficheros exigen un token de Earthdata**
  (``Authorization: Bearer``), que se lee de una variable de entorno y nunca se escribe en
  ficheros ni en el log.
- No se siguen redirecciones: si el token no es válido o ha caducado, el servidor redirige a la
  página de login y la descarga se detiene, sin enviar el token a otro servidor.
- Los ficheros de los últimos días crecen durante el día, así que se vuelven a bajar siempre;
  los más antiguos, una vez descargados, no.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fire_etl.config import NrtConfig
from fire_etl.firms_download import (
    DOWNLOADED,
    MISSING,
    USER_AGENT,
    fetch_to_file,
    with_retries,
)

LOGGER = logging.getLogger(__name__)

FILE_NAME_PATTERN = re.compile(r'data-name="([^"/]+_(\d{4})(\d{3})\.txt)"')
REDIRECT_OR_DENIED = frozenset({301, 302, 303, 307, 308, 401, 403})
LISTING_TIMEOUT_S = 30
PROGRESS_EVERY = 25  # cada cuántos ficheros se informa del avance


class NrtAuthError(RuntimeError):
    """El token de Earthdata no es válido o ha caducado (el servidor pide iniciar sesión)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """No sigue redirecciones, para no reenviar el token a otro servidor."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Devuelve ``None``: la redirección se trata como un error HTTP."""
        return None


@dataclass(frozen=True)
class NrtItem:
    """
    Un fichero NRT diario.

    Attributes
    ----------
    source : str
        Carpeta del sensor (``modis-c6.1``, ``noaa-20-viirs-c2``...).
    name : str
        Nombre del fichero.
    day : date
        Día UTC al que corresponde (según el día del año del nombre).
    url : str
        Dirección completa.
    path : Path
        Destino local.
    """

    source: str
    name: str
    day: date
    url: str
    path: Path


@dataclass
class NrtResult:
    """Resultado de la descarga del NRT."""

    downloaded: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    token_missing: bool = False
    auth_failed: bool = False
    first_day: date | None = None
    last_day: date | None = None


def parse_listing(html: str) -> list[tuple[str, date]]:
    """
    Ficheros ``..._AAAADDD.txt`` de un listado de carpeta, con su día.

    Returns
    -------
    list[tuple[str, date]]
        ``(nombre, día)`` sin repetidos, ordenados por día.
    """
    found = {}
    for name, year, day_of_year in FILE_NAME_PATTERN.findall(html):
        try:
            found[name] = date(int(year), 1, 1) + timedelta(days=int(day_of_year) - 1)
        except ValueError:
            continue

    return sorted(found.items(), key=lambda entry: (entry[1], entry[0]))


def list_remote(config: NrtConfig, source: str) -> list[tuple[str, date]]:
    """Ficheros disponibles en la carpeta de una fuente (el listado es público)."""
    url = f"{config.base_url.rstrip('/')}/{source}/{config.region}/"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=LISTING_TIMEOUT_S) as response:
        return parse_listing(response.read().decode("utf-8", errors="replace"))


def first_uncovered_day(today: date | None = None) -> date:
    """Primer día que el archivo anual no cubre: el 1 de enero del año en curso."""
    reference = today or datetime.now(timezone.utc).date()

    return date(reference.year, 1, 1)


def plan_nrt(
    config: NrtConfig,
    remote: dict[str, list[tuple[str, date]]],
    directory: str | Path | None = None,
    today: date | None = None,
) -> list[NrtItem]:
    """
    Ficheros NRT a tener en disco: los de fechas que el archivo anual no cubre.

    Parameters
    ----------
    config : NrtConfig
        Fuentes y región.
    remote : dict[str, list[tuple[str, date]]]
        Ficheros disponibles por fuente (de :func:`list_remote`).
    directory : str | Path | None
        Carpeta destino; por defecto ``config.directory``.
    today : date | None
        Fecha de referencia (solo para tests).

    Returns
    -------
    list[NrtItem]
        Ordenados por fuente y día.
    """
    target = Path(directory or config.directory)
    first = first_uncovered_day(today)
    base = config.base_url.rstrip("/")
    items = []
    for source in config.sources:
        for name, day in remote.get(source, []):
            if day >= first:
                url = f"{base}/{source}/{config.region}/{name}"
                items.append(NrtItem(source, name, day, url, target / name))

    return items


def needs_download(item: NrtItem, refetch_days: int, today: date) -> bool:
    """``True`` si falta el fichero o es de los últimos días (sigue creciendo)."""
    if not (item.path.exists() and item.path.stat().st_size > 0):
        return True

    return item.day >= today - timedelta(days=refetch_days)


def _fetch(item: NrtItem, config: NrtConfig, token: str) -> str:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        return fetch_to_file(
            item.url,
            item.path,
            config.timeout_s,
            headers={"Authorization": f"Bearer {token}"},
            opener=opener,
        )
    except urllib.error.HTTPError as error:
        if error.code in REDIRECT_OR_DENIED:
            raise NrtAuthError(
                f"El servidor rechazó el token (HTTP {error.code}): caducado o no válido."
            ) from error

        raise


def resolve_token(config: NrtConfig, base_dir: str | Path | None = None) -> str:
    """
    Token de Earthdata: la variable de entorno ``config.token_env`` y, si no existe, el fichero
    local ``config.token_file`` (primera línea que no esté vacía ni empiece por ``#``).

    Parameters
    ----------
    config : NrtConfig
        Nombre de la variable y ruta del fichero.
    base_dir : str | Path | None
        Carpeta respecto a la que se resuelve una ruta relativa de ``token_file``.

    Returns
    -------
    str
        El token, o cadena vacía si no hay ninguno. Nunca se registra en el log.
    """
    from_env = os.environ.get(config.token_env, "").strip()
    if from_env:
        return from_env

    path = Path(config.token_file)
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""

    for line in lines:
        value = line.strip()
        if value and not value.startswith("#"):
            return value

    return ""


def download_nrt(
    config: NrtConfig,
    directory: str | Path | None = None,
    today: date | None = None,
    base_dir: str | Path | None = None,
) -> NrtResult:
    """
    Descarga los ficheros NRT que falten (y los de los últimos días, que se actualizan).

    El token sale de la variable de entorno o del fichero local (ver :func:`resolve_token`); sin
    él no se descarga nada y se avisa. Si el servidor lo rechaza se detiene todo. Un fichero que
    falla por otro motivo no detiene el resto.

    Parameters
    ----------
    config : NrtConfig
        Fuentes, región y token.
    directory : str | Path | None
        Carpeta destino; por defecto ``config.directory``.
    today : date | None
        Fecha de referencia (solo para tests).
    base_dir : str | Path | None
        Carpeta respecto a la que se resuelve ``config.token_file`` si es relativa.

    Returns
    -------
    NrtResult
        Ficheros descargados, ya existentes, inexistentes y fallidos.
    """
    result = NrtResult()
    if not config.enabled:
        return result

    token = resolve_token(config, base_dir)
    if not token:
        result.token_missing = True
        LOGGER.warning(
            "NRT omitido: define el token de Earthdata en la variable de entorno %s o en el "
            "fichero local %s.",
            config.token_env,
            config.token_file,
        )
        return result

    reference = today or datetime.now(timezone.utc).date()
    remote: dict[str, list[tuple[str, date]]] = {}
    for source in config.sources:
        try:
            remote[source] = list_remote(config, source)
        except (urllib.error.URLError, TimeoutError) as error:
            LOGGER.error("No se pudo listar %s/%s: %s", source, config.region, error)
            result.failed[f"{source}/{config.region}/"] = str(error)

    items = plan_nrt(config, remote, directory, reference)
    days = [item.day for item in items]
    result.first_day, result.last_day = (min(days), max(days)) if days else (None, None)

    pending = []
    for item in items:
        if needs_download(item, config.refetch_days, reference):
            pending.append(item)
        else:
            result.skipped.append(item.path)

    LOGGER.info(
        "NRT: %s ficheros (%s a %s): %s por descargar, %s ya en disco.%s",
        len(items),
        result.first_day,
        result.last_day,
        len(pending),
        len(result.skipped),
        " La primera vez tarda unos minutos (~1 s por fichero)." if len(pending) > 50 else "",
    )

    for index, item in enumerate(pending, start=1):
        if index % PROGRESS_EVERY == 0:
            LOGGER.info("NRT: descargados %s de %s ficheros...", index - 1, len(pending))

        try:
            status = with_retries(
                lambda item=item: _fetch(item, config, token),
                config.retries,
                config.retry_wait_s,
                item.url,
            )
        except NrtAuthError as error:
            LOGGER.error("%s Se detiene la descarga del NRT.", error)
            result.auth_failed = True
            break
        except Exception as error:  # un fichero fallido no detiene el resto
            LOGGER.error("No se pudo descargar %s: %s", item.url, error)
            result.failed[item.url] = str(error)
            continue

        if status == MISSING:
            result.missing.append(item.url)
        elif status == DOWNLOADED:
            result.downloaded.append(item.path)

    LOGGER.info(
        "NRT terminado: %s descargados, %s inexistentes, %s fallidos.",
        len(result.downloaded),
        len(result.missing),
        len(result.failed),
    )

    return result


def nrt_files(directory: str | Path) -> list[Path]:
    """Ficheros NRT ya en disco (aunque esta ejecución no haya podido descargar)."""
    return sorted(Path(directory).glob("*.txt"))

