"""
Configuración del pipeline de fusión MODIS + VIIRS.

Los parámetros del algoritmo se definen como dataclasses inmutables cuyos valores por defecto
coinciden con ``backend/etls/config/default.yaml``. Un fichero YAML puede sobrescribir cualquier
subconjunto de claves con :func:`load_config`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import StrEnum
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
class ValidationConfig:
    """Rangos válidos de los campos de entrada."""

    lat_range: tuple[float, float] = (-90.0, 90.0)
    lon_range: tuple[float, float] = (-180.0, 180.0)
    scan_km_range: tuple[float, float] = (0.1, 10.0)
    track_km_range: tuple[float, float] = (0.1, 10.0)
    frp_min: float = 0.0
    allowed_daynight: tuple[str, ...] = ("D", "N")


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
    """

    coefficient_kg_per_mj: float = 0.368

    def __post_init__(self) -> None:
        if self.coefficient_kg_per_mj <= 0:
            raise InvalidConfigError("biomass.coefficient_kg_per_mj debe ser > 0")


@dataclass(frozen=True)
class GeometryConfig:
    """CRS de cálculo/almacenamiento y buffer del footprint."""

    metric_crs: str = "EPSG:25830"
    storage_crs: str = "EPSG:4326"
    footprint_buffer_m: float = 400.0

    def __post_init__(self) -> None:
        if self.footprint_buffer_m < 0:
            raise InvalidConfigError("geometry.footprint_buffer_m debe ser >= 0")


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
    use_previous_state: bool = True


@dataclass(frozen=True)
class LandCoverConfig:
    """
    Enriquecimiento opcional de las detecciones con cobertura del suelo (SIGPAC).

    Attributes
    ----------
    sigpac_dir : str | None
        Carpeta con los recintos SIGPAC (shapefile / GeoPackage). ``None`` desactiva el paso.
    sigpac_confidence : float
        Confianza asignada a los puntos resueltos con SIGPAC.
    min_confidence : float
        Umbral de confianza para ``is_agricultural`` / ``is_forest``.
    """

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
            "inactive_after_hours": self.events.inactive_after_hours,
            "modis_low_max": self.confidence.modis_low_max,
            "modis_nominal_max": self.confidence.modis_nominal_max,
            "single_sensor_high_level": self.confidence.single_sensor_high_level,
            "discard_isolated_low": self.confidence.discard_isolated_low,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
