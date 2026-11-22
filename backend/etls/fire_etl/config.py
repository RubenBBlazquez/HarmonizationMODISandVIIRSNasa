"""
Configuración del pipeline de fusión MODIS + VIIRS.

Los parámetros del algoritmo se definen como dataclasses inmutables cuyos valores por defecto
coinciden con ``backend/etls/config/default.yaml``. Un fichero YAML puede sobrescribir cualquier
subconjunto de claves con :func:`load_config`.

"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import date, datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path
from typing import Any

import yaml


class WindowMode(StrEnum):
    """Modo de partición temporal de las detecciones."""

    DAY_UTC = "day_utc"
    HOURS_12 = "hours_12"
    DATE_DAYNIGHT = "date_daynight"


class InvalidConfigError(ValueError):
    """La configuración contiene claves o valores no válidos."""


def _default_viirs_mapping() -> dict[str, str]:
    return {
        "l": "low",
        "low": "low",
        "n": "nominal",
        "nominal": "nominal",
        "h": "high",
        "high": "high",
    }


@dataclass(frozen=True)
class AreaOfInterestConfig:
    """
    Área de interés en EPSG:4326.

    Attributes
    ----------
    bbox : tuple[float, float, float, float] | None
        ``(min_lon, min_lat, max_lon, max_lat)``.
    polygon_wkt : str | None
        Polígono en WKT. Excluyente con ``bbox``.
    """

    bbox: tuple[float, float, float, float] | None = None
    polygon_wkt: str | None = None

    def __post_init__(self) -> None:
        if self.bbox is not None and self.polygon_wkt is not None:
            raise InvalidConfigError("area_of_interest: usa bbox o polygon_wkt, no ambos")

        if self.bbox is not None:
            if len(self.bbox) != 4:
                raise InvalidConfigError("area_of_interest.bbox debe tener 4 valores")

            object.__setattr__(self, "bbox", tuple(float(value) for value in self.bbox))

    @property
    def is_defined(self) -> bool:
        return self.bbox is not None or self.polygon_wkt is not None


@dataclass(frozen=True)
class ExclusionZone:
    """
    Zona y fechas con fuentes térmicas que no son incendios forestales (p. ej. una erupción).

    Las detecciones dentro del recuadro y de las fechas se marcan como no válidas (no se
    descartan) con ``skip_reason = reason``.

    Attributes
    ----------
    name : str
        Nombre descriptivo (solo para el registro).
    bbox : tuple[float, float, float, float]
        ``(min_lon, min_lat, max_lon, max_lat)`` en EPSG:4326.
    start, end : date | None
        Fechas UTC, ambas incluidas; ``None`` = sin límite por ese lado.
    reason : str
        Valor de ``skip_reason`` de las detecciones marcadas.
    """

    name: str
    bbox: tuple[float, float, float, float]
    start: date | None = None
    end: date | None = None
    reason: str = "excluded_zone"

    def __post_init__(self) -> None:
        if len(self.bbox) != 4:
            raise InvalidConfigError(f"exclusion_zones[{self.name}].bbox debe tener 4 valores")

        object.__setattr__(self, "bbox", tuple(float(value) for value in self.bbox))
        for field_name in ("start", "end"):
            value = getattr(self, field_name)
            if isinstance(value, str):
                object.__setattr__(self, field_name, date.fromisoformat(value))

        if self.start and self.end and self.start > self.end:
            raise InvalidConfigError(f"exclusion_zones[{self.name}]: start posterior a end")


def _default_non_wildfire_types() -> dict[int, str]:
    return {1: "active_volcano", 2: "static_land_source", 3: "offshore_detection"}


def _default_frp_thresholds_mw() -> dict[str, dict[str, float]]:
    """Umbrales de FRP para ruido y categorías por instrumento, en MW."""
    return {
        "VIIRS": {
            "min_day": 3.7,
            "min_night": 0.7,
            "limit_low": 35.0,
            "limit_medium": 70.0,
        },
        "MODIS": {
            "min_day": 5.7,
            "min_night": 3.7,
            "limit_low": 75.0,
            "limit_medium": 175.0,
        },
    }


@dataclass(frozen=True)
class ValidationConfig:
    """Rangos válidos de los campos de entrada."""

    lat_range: tuple[float, float] = (-90.0, 90.0)
    lon_range: tuple[float, float] = (-180.0, 180.0)
    frp_min: float = 0.0
    allowed_daynight: tuple[str, ...] = ("D", "N")
    # Marca como no válidas (``is_valid=False``, ``skip_reason="below_frp_threshold"``) las
    # detecciones por debajo del FRP mínimo de su instrumento y de día/noche. Los umbrales están
    # en el YAML; los mínimos por defecto se ajustan a la sensibilidad publicada de píxeles
    # pequeños.
    apply_frp_thresholds: bool = True
    # Un foco en bosque (``is_forest``) se mantiene válido aunque su FRP esté por debajo del mínimo
    # (categoría "low"): el riesgo es alto y debe poder alertar. En agricultura se sigue
    # descartando por FRP bajo. Necesita la cobertura del suelo (SIGPAC) activada.
    keep_forest_below_frp: bool = True
    frp_thresholds_mw: dict[str, dict[str, float]] = field(
        default_factory=_default_frp_thresholds_mw
    )
    # Nombres de satélite que se unifican con los de los ficheros NRT (el archivo anual usa
    # "Aqua" / "Terra"; los NRT, "A" / "T"), para que el detection_id no duplique detecciones.
    satellite_aliases: dict[str, str] = field(default_factory=lambda: {"Aqua": "A", "Terra": "T"})
    # Marca como no válidas las detecciones que no son incendios de vegetación: el campo ``type``
    # de FIRMS (solo en los ficheros anuales) distingue 0 = incendio de vegetación presunto de
    # 1 = volcán activo, 2 = fuente terrestre estática (industria) y 3 = detección en el mar.
    apply_source_filters: bool = True
    non_wildfire_types: dict[int, str] = field(default_factory=_default_non_wildfire_types)
    # Zonas y fechas concretas que no son incendios forestales y que ``type`` no separa (p. ej. la
    # erupción de La Palma de 2021, clasificada casi toda como incendio de vegetación).
    exclusion_zones: tuple[ExclusionZone, ...] = ()

    def __post_init__(self) -> None:
        zones = tuple(
            zone if isinstance(zone, ExclusionZone) else ExclusionZone(**zone)
            for zone in self.exclusion_zones
        )
        object.__setattr__(self, "exclusion_zones", zones)

        required_keys = {"min_day", "min_night", "limit_low", "limit_medium"}
        expected_instruments = {"VIIRS", "MODIS"}
        if set(self.frp_thresholds_mw) != expected_instruments:
            raise InvalidConfigError(
                "validation.frp_thresholds_mw: se requieren exactamente VIIRS y MODIS"
            )

        thresholds: dict[str, dict[str, float]] = {}
        for instrument, values in self.frp_thresholds_mw.items():
            if set(values) != required_keys:
                raise InvalidConfigError(
                    f"validation.frp_thresholds_mw.{instrument}: claves {sorted(required_keys)}"
                )
            normalized = {key: float(value) for key, value in values.items()}
            if (
                not all(isfinite(value) for value in normalized.values())
                or normalized["min_day"] < 0
                or normalized["min_night"] < 0
                or normalized["min_day"] > normalized["limit_low"]
                or normalized["min_night"] > normalized["limit_low"]
                or normalized["limit_low"] > normalized["limit_medium"]
            ):
                raise InvalidConfigError(
                    f"validation.frp_thresholds_mw.{instrument}: límites inconsistentes"
                )
            thresholds[instrument] = normalized
        object.__setattr__(self, "frp_thresholds_mw", thresholds)


@dataclass(frozen=True)
class ConfidenceConfig:
    """
    Reglas de normalización y fusión de la confianza.

    Attributes
    ----------
    modis_low_max : int
        Valor máximo (incluido) de confianza MODIS considerado ``low``.
    modis_nominal_max : int
        Valor máximo (incluido) considerado ``nominal``; por encima es ``high``.
    viirs_mapping : dict[str, str]
        Correspondencia (en minúsculas) entre el valor VIIRS original y el nivel normalizado.
    single_sensor_high_level : str
        Nivel fusionado para una observación de un solo sensor con confianza alta.
    discard_isolated_low : bool
        Si es ``True``, las observaciones low aisladas no se enlazan a eventos.
    """

    modis_low_max: int = 29
    modis_nominal_max: int = 79
    viirs_mapping: dict[str, str] = field(default_factory=_default_viirs_mapping)
    single_sensor_high_level: str = "medium"
    discard_isolated_low: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.modis_low_max < self.modis_nominal_max < 100:
            raise InvalidConfigError("confidence: se requiere 0 <= low_max < nominal_max < 100")

        if self.single_sensor_high_level not in ("medium", "high"):
            raise InvalidConfigError("confidence.single_sensor_high_level: medium | high")

        invalid_levels = set(self.viirs_mapping.values()) - {"low", "nominal", "high"}
        if invalid_levels:
            raise InvalidConfigError(f"confidence.viirs_mapping: niveles {invalid_levels}")


@dataclass(frozen=True)
class BiomassConfig:
    """
    Estimación de biomasa a partir de la potencia radiativa (FRP).

    Attributes
    ----------
    coefficient_kg_per_mj : float
        Biomasa consumida por unidad de energía radiada (kg/MJ). Como el FRP está en MW
        (= MJ/s), ``FRP * coeficiente`` da una **tasa de combustión de biomasa en kg/s**.
    pass_gap_minutes : float
        Detecciones de un evento separadas menos de este tiempo son de la misma pasada de satélite.
    pass_window_hours : float
        Horas que se supone que representa una pasada (como máximo; se reparte a la mitad con la
        pasada vecina) al integrar el FRP en el tiempo para estimar la biomasa quemada.
    """

    coefficient_kg_per_mj: float = 0.368
    pass_gap_minutes: float = 30.0
    pass_window_hours: float = 6.0

    def __post_init__(self) -> None:
        if self.coefficient_kg_per_mj <= 0:
            raise InvalidConfigError("biomass.coefficient_kg_per_mj debe ser > 0")

        if self.pass_gap_minutes <= 0 or self.pass_window_hours <= 0:
            raise InvalidConfigError("biomass: pass_gap_minutes y pass_window_hours deben ser > 0")


@dataclass(frozen=True)
class GeometryConfig:
    """CRS de cálculo/almacenamiento, tamaño nominal del píxel y buffer del footprint."""

    metric_crs: str = "EPSG:25830"
    storage_crs: str = "EPSG:4326"
    footprint_buffer_m: float = 400.0
    # Lado nominal del píxel de cada sensor, en km: MODIS 1 km, VIIRS 375 m. Con él se rellenan
    # ``area_width_km`` y ``area_height_km`` de cada detección.
    pixel_size_km: dict[str, float] = field(default_factory=lambda: {"MODIS": 1.0, "VIIRS": 0.375})

    def __post_init__(self) -> None:
        if self.footprint_buffer_m < 0:
            raise InvalidConfigError("geometry.footprint_buffer_m debe ser >= 0")

        if set(self.pixel_size_km) != {"MODIS", "VIIRS"} or any(
            size <= 0 for size in self.pixel_size_km.values()
        ):
            raise InvalidConfigError(
                "geometry.pixel_size_km necesita MODIS y VIIRS con valores > 0 (km)"
            )


@dataclass(frozen=True)
class WindowConfig:
    """Partición temporal."""

    mode: WindowMode = WindowMode.DAY_UTC

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", WindowMode(self.mode))


@dataclass(frozen=True)
class LinkingConfig:
    """Enlace de observaciones entre ventanas consecutivas."""

    max_distance_m: float = 1000.0
    max_window_gap: int = 1

    def __post_init__(self) -> None:
        if self.max_distance_m < 0 or self.max_window_gap < 1:
            raise InvalidConfigError("linking: max_distance_m >= 0 y max_window_gap >= 1")


@dataclass(frozen=True)
class EventConfig:
    """Estado de los eventos."""

    inactive_after_hours: float = 48.0
    reference_time: datetime | None = None

    def __post_init__(self) -> None:
        if isinstance(self.reference_time, str):
            object.__setattr__(self, "reference_time", datetime.fromisoformat(self.reference_time))

        if self.reference_time is not None and self.reference_time.tzinfo is None:
            raise InvalidConfigError("events.reference_time debe incluir zona horaria (UTC)")


@dataclass(frozen=True)
class OutputConfig:
    """Destino de las salidas."""

    directory: str = "output"
    # Datos finales depurados para el mapa y el chatbot (GeoJSON + JSON).
    clean_data_directory: str = "clean_data"
    use_previous_state: bool = True


@dataclass(frozen=True)
class DatabaseConfig:
    """
    Carga de las tablas del pipeline en PostGIS (además de los GeoParquet).

    Attributes
    ----------
    enabled : bool
        Si es ``True``, inserta las tablas tras escribir los ficheros.
    url : str | None
        URL de conexión fija (PostgreSQL/PostGIS del ``docker-compose.yml``, solo desarrollo
        local).
    apply_schema : bool
        Reaplica el esquema SQL (idempotente) antes de cada carga.
    """

    enabled: bool = False
    url: str | None = "postgresql://fires:fires@localhost:5432/fires"
    apply_schema: bool = True


def _default_download_sources() -> dict[str, int]:
    return {"modis": 2000, "viirs-snpp": 2012, "viirs-jpss1": 2018}


@dataclass(frozen=True)
class DownloadConfig:
    """
    Descarga del archivo anual de NASA FIRMS por país (público, sin cuenta ni token).

    Attributes
    ----------
    base_url : str
        Raíz del archivo: ``<base_url>/<fuente>/<año>/<fuente>_<año>_<país>.csv``.
    country : str
        País tal como lo nombra FIRMS (``Spain``).
    sources : dict[str, int]
        Fuente -> primer año con datos (MODIS desde 2000, VIIRS S-NPP desde 2012, NOAA-20
        desde 2018).
    start_year : int
        Primer año a descargar.
    end_year : int | None
        Último año; ``None`` = último año completo (el año en curso aún no está en el archivo).
    directory : str
        Carpeta destino de los CSV.
    timeout_s, retries, retry_wait_s : float, int, float
        Tiempo máximo por petición, reintentos y espera (se multiplica por el intento).
    """

    base_url: str = "https://firms.modaps.eosdis.nasa.gov/data/country"
    country: str = "Spain"
    sources: dict[str, int] = field(default_factory=_default_download_sources)
    start_year: int = 2000
    end_year: int | None = None
    directory: str = "data/raw"
    timeout_s: float = 60.0
    retries: int = 3
    retry_wait_s: float = 2.0

    def __post_init__(self) -> None:
        if self.end_year is not None and self.end_year < self.start_year:
            raise InvalidConfigError("download.end_year debe ser >= start_year")

        if self.retries < 1 or self.timeout_s <= 0 or not self.sources:
            raise InvalidConfigError("download: retries >= 1, timeout_s > 0 y alguna fuente")


@dataclass(frozen=True)
class NrtConfig:
    """
    Descarga de los ficheros casi en tiempo real (NRT) de NASA FIRMS para el año en curso.

    El archivo anual (``download``) solo llega hasta el último año completo; el NRT cubre los
    últimos ~60 días. Necesita un token de Earthdata en la variable de entorno ``token_env``
    (el token **nunca** va en este fichero).

    Attributes
    ----------
    enabled : bool
        Si es ``False`` no se descarga el NRT.
    base_url : str
        Raíz del archivo NRT; los ficheros están en ``<base_url>/<fuente>/<región>/``.
    region : str
        Región de los ficheros NRT (``Europe``).
    sources : tuple[str, ...]
        Carpetas de cada sensor/satélite.
    token_env : str
        Nombre de la variable de entorno con el token (``Authorization: Bearer``). Tiene prioridad.
    token_file : str
        Fichero local con el token (una línea), **ignorado por git**, que se usa si no hay variable
        de entorno. Una ruta relativa se resuelve respecto a la carpeta ``etls``.
    directory : str
        Carpeta destino de los ficheros.
    refetch_days : int
        Los ficheros de los últimos días se vuelven a bajar siempre: crecen durante el día.
    timeout_s, retries, retry_wait_s : float, int, float
        Tiempo máximo por petición, reintentos y espera.
    """

    enabled: bool = True
    base_url: str = "https://nrt3.modaps.eosdis.nasa.gov/archive/FIRMS"
    region: str = "Europe"
    sources: tuple[str, ...] = (
        "modis-c6.1",
        "suomi-npp-viirs-c2",
        "noaa-20-viirs-c2",
        "noaa-21-viirs-c2",
    )
    token_env: str = "EARTHDATA_TOKEN"
    token_file: str = ".secrets/earthdata_token"
    directory: str = "data/raw/nrt"
    refetch_days: int = 2
    timeout_s: float = 60.0
    retries: int = 3
    retry_wait_s: float = 2.0

    def __post_init__(self) -> None:
        if self.refetch_days < 0 or self.retries < 1 or self.timeout_s <= 0:
            raise InvalidConfigError("nrt: refetch_days >= 0, retries >= 1 y timeout_s > 0")

        if not self.sources:
            raise InvalidConfigError("nrt.sources necesita al menos una carpeta")


@dataclass(frozen=True)
class LandCoverConfig:
    """
    Enriquecimiento opcional de las detecciones con cobertura del suelo (SIGPAC).

    Attributes
    ----------
    use_database : bool
        Si es ``True``, consulta los recintos en ``fire.sigpac_recintos`` (usa ``database.url``)
        y tiene prioridad sobre ``sigpac_dir``.
    sigpac_dir : str | None
        Carpeta con los recintos SIGPAC (shapefile / GeoPackage). Sin ``use_database``, ``None``
        desactiva el paso.
    sigpac_confidence : float
        Confianza asignada a los puntos resueltos con SIGPAC.
    min_confidence : float
        Umbral de confianza para ``is_agricultural`` / ``is_forest``.
    """

    use_database: bool = False
    sigpac_dir: str | None = None
    sigpac_confidence: float = 0.95
    min_confidence: float = 0.0


@dataclass(frozen=True)
class PipelineConfig:
    """Configuración completa del pipeline."""

    area_of_interest: AreaOfInterestConfig = field(default_factory=AreaOfInterestConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    confidence: ConfidenceConfig = field(default_factory=ConfidenceConfig)
    biomass: BiomassConfig = field(default_factory=BiomassConfig)
    geometry: GeometryConfig = field(default_factory=GeometryConfig)
    windows: WindowConfig = field(default_factory=WindowConfig)
    linking: LinkingConfig = field(default_factory=LinkingConfig)
    events: EventConfig = field(default_factory=EventConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    land_cover: LandCoverConfig = field(default_factory=LandCoverConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    nrt: NrtConfig = field(default_factory=NrtConfig)

    def algorithm_parameters(self) -> dict[str, Any]:
        """
        Devuelve los parámetros que afectan al resultado, para registrarlos en cada evento.

        Returns
        -------
        dict[str, Any]
            Parámetros serializables a JSON.
        """
        return {
            "window_mode": str(self.windows.mode),
            "footprint_buffer_m": self.geometry.footprint_buffer_m,
            "link_max_distance_m": self.linking.max_distance_m,
            "link_max_window_gap": self.linking.max_window_gap,
            "metric_crs": self.geometry.metric_crs,
            "biomass_coefficient_kg_per_mj": self.biomass.coefficient_kg_per_mj,
            "pass_gap_minutes": self.biomass.pass_gap_minutes,
            "pass_window_hours": self.biomass.pass_window_hours,
            "inactive_after_hours": self.events.inactive_after_hours,
            "modis_low_max": self.confidence.modis_low_max,
            "modis_nominal_max": self.confidence.modis_nominal_max,
            "single_sensor_high_level": self.confidence.single_sensor_high_level,
            "discard_isolated_low": self.confidence.discard_isolated_low,
            "frp_thresholds_mw": self.validation.frp_thresholds_mw,
        }

    def to_dict(self) -> dict[str, Any]:
        """Configuración serializable; la URL de la base de datos se oculta (lleva la clave)."""
        data = asdict(self)
        data["database"]["url"] = "***" if self.database.url else None

        return data


def _build_section(section_cls: type, values: dict[str, Any] | None, name: str) -> Any:
    values = values or {}
    known = {item.name for item in fields(section_cls)}
    unknown = set(values) - known
    if unknown:
        raise InvalidConfigError(f"{name}: claves desconocidas {sorted(unknown)}")

    normalized = {
        key: tuple(value) if isinstance(value, list) else value for key, value in values.items()
    }

    return section_cls(**normalized)


def config_from_dict(raw: dict[str, Any]) -> PipelineConfig:
    """
    Construye la configuración a partir de un diccionario (p. ej. YAML ya parseado).

    Parameters
    ----------
    raw : dict[str, Any]
        Secciones de configuración; las ausentes toman su valor por defecto.

    Returns
    -------
    PipelineConfig
        Configuración validada.

    Raises
    ------
    InvalidConfigError
        Si hay secciones o claves desconocidas o valores fuera de rango.
    """
    sections = {item.name: item.type for item in fields(PipelineConfig)}
    unknown = set(raw) - set(sections)
    if unknown:
        raise InvalidConfigError(f"Secciones desconocidas: {sorted(unknown)}")

    defaults = PipelineConfig()
    built = {}
    for name in sections:
        section_cls = type(getattr(defaults, name))
        assert is_dataclass(section_cls)
        built[name] = _build_section(section_cls, raw.get(name), name)

    return PipelineConfig(**built)


def load_config(path: str | Path | None = None) -> PipelineConfig:
    """
    Carga la configuración desde un YAML; sin ruta devuelve los valores por defecto.

    Parameters
    ----------
    path : str | Path | None
        Ruta del YAML.

    Returns
    -------
    PipelineConfig
        Configuración validada.
    """
    if path is None:
        return PipelineConfig()

    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    return config_from_dict(raw)
