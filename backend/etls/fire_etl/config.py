"""
Configuration of the MODIS + VIIRS fusion pipeline.

The algorithm parameters are defined as immutable dataclasses whose default values match
``backend/etls/config/default.yaml``. A YAML file can override any subset of keys through
:func:`load_config`.
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
    """Time partitioning mode for detections."""

    DAY_UTC = "day_utc"
    HOURS_12 = "hours_12"
    DATE_DAYNIGHT = "date_daynight"


class InvalidConfigError(ValueError):
    """The configuration contains invalid keys or values."""


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
    Area of interest in EPSG:4326.

    Attributes
    ----------
    bbox : tuple[float, float, float, float] | None
        ``(min_lon, min_lat, max_lon, max_lat)``.
    polygon_wkt : str | None
        Polygon in WKT. Mutually exclusive with ``bbox``.
    """

    bbox: tuple[float, float, float, float] | None = None
    polygon_wkt: str | None = None

    def __post_init__(self) -> None:
        if self.bbox is not None and self.polygon_wkt is not None:
            raise InvalidConfigError("area_of_interest: use bbox or polygon_wkt, not both")

        if self.bbox is not None:
            if len(self.bbox) != 4:
                raise InvalidConfigError("area_of_interest.bbox must have 4 values")

            object.__setattr__(self, "bbox", tuple(float(value) for value in self.bbox))

    @property
    def is_defined(self) -> bool:
        return self.bbox is not None or self.polygon_wkt is not None


@dataclass(frozen=True)
class ExclusionZone:
    """
    Zone and dates with heat sources that are not wildfires (e.g. a volcanic eruption).

    Detections inside the box and the dates are flagged as invalid (not discarded) with
    ``skip_reason = reason``.

    Attributes
    ----------
    name : str
        Descriptive name (for logging only).
    bbox : tuple[float, float, float, float]
        ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
    start, end : date | None
        UTC dates, both inclusive; ``None`` = no limit on that side.
    reason : str
        ``skip_reason`` value of the flagged detections.
    """

    name: str
    bbox: tuple[float, float, float, float]
    start: date | None = None
    end: date | None = None
    reason: str = "excluded_zone"

    def __post_init__(self) -> None:
        if len(self.bbox) != 4:
            raise InvalidConfigError(f"exclusion_zones[{self.name}].bbox must have 4 values")

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
    """FRP thresholds for noise and categories per instrument, in MW."""
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
    """Valid ranges of the input fields."""

    lat_range: tuple[float, float] = (-90.0, 90.0)
    lon_range: tuple[float, float] = (-180.0, 180.0)
    frp_min: float = 0.0
    allowed_daynight: tuple[str, ...] = ("D", "N")
    # Flags as invalid (``is_valid=False``, ``skip_reason="below_frp_threshold"``) the
    # detections below the minimum FRP of their instrument and day/night. The thresholds are
    # in the YAML; the default minimums match the published sensitivity for small
    # pixels.
    apply_frp_thresholds: bool = True
    # A forest hotspot (``is_forest``) stays valid even if its FRP is below the minimum
    # ("low" category): the risk is high and it must be able to raise an alert. In agriculture
    # low FRP is still discarded. Requires land cover (SIGPAC) to be enabled.
    keep_forest_below_frp: bool = True
    frp_thresholds_mw: dict[str, dict[str, float]] = field(
        default_factory=_default_frp_thresholds_mw
    )
    # Satellite names that are unified with those of the NRT files (the annual archive uses
    # "Aqua" / "Terra"; the NRT ones, "A" / "T"), so that detection_id does not duplicate
    # detections.
    satellite_aliases: dict[str, str] = field(default_factory=lambda: {"Aqua": "A", "Terra": "T"})
    # Flags as invalid the detections that are not vegetation fires: the FIRMS ``type`` field
    # (only in the annual files) distinguishes 0 = presumed vegetation fire from
    # 1 = active volcano, 2 = static land source (industry) and 3 = offshore detection.
    apply_source_filters: bool = True
    non_wildfire_types: dict[int, str] = field(default_factory=_default_non_wildfire_types)
    # Specific zones and dates that are not wildfires and that ``type`` does not separate (e.g. the
    # 2021 La Palma eruption, classified almost entirely as vegetation fire).
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
                "validation.frp_thresholds_mw: exactly VIIRS and MODIS are required"
            )

        thresholds: dict[str, dict[str, float]] = {}
        for instrument, values in self.frp_thresholds_mw.items():
            if set(values) != required_keys:
                raise InvalidConfigError(
                    f"validation.frp_thresholds_mw.{instrument}: keys {sorted(required_keys)}"
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
                    f"validation.frp_thresholds_mw.{instrument}: inconsistent limits"
                )
            thresholds[instrument] = normalized
        object.__setattr__(self, "frp_thresholds_mw", thresholds)


@dataclass(frozen=True)
class ConfidenceConfig:
    """
    Rules for normalizing and fusing confidence.

    Attributes
    ----------
    modis_low_max : int
        Maximum value (inclusive) of MODIS confidence considered ``low``.
    modis_nominal_max : int
        Maximum value (inclusive) considered ``nominal``; above it is ``high``.
    viirs_mapping : dict[str, str]
        Mapping (lowercase) between the original VIIRS value and the normalized level.
    single_sensor_high_level : str
        Fused level for a single-sensor observation with high confidence.
    discard_isolated_low : bool
        If ``True``, isolated low observations are not linked to events.
    """

    modis_low_max: int = 29
    modis_nominal_max: int = 79
    viirs_mapping: dict[str, str] = field(default_factory=_default_viirs_mapping)
    single_sensor_high_level: str = "medium"
    discard_isolated_low: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.modis_low_max < self.modis_nominal_max < 100:
            raise InvalidConfigError("confidence: 0 <= low_max < nominal_max < 100 is required")

        if self.single_sensor_high_level not in ("medium", "high"):
            raise InvalidConfigError("confidence.single_sensor_high_level: medium | high")

        invalid_levels = set(self.viirs_mapping.values()) - {"low", "nominal", "high"}
        if invalid_levels:
            raise InvalidConfigError(f"confidence.viirs_mapping: levels {invalid_levels}")


@dataclass(frozen=True)
class BiomassConfig:
    """
    Biomass estimation from fire radiative power (FRP).

    Attributes
    ----------
    coefficient_kg_per_mj : float
        Biomass consumed per unit of radiated energy (kg/MJ). Since FRP is in MW
        (= MJ/s), ``FRP * coefficient`` gives a **biomass burning rate in kg/s**.
    pass_gap_minutes : float
        Detections of an event closer together than this time belong to the same satellite pass.
    pass_window_hours : float
        Hours a pass is assumed to represent (at most; split in half with the neighboring
        pass) when integrating FRP over time to estimate the burned biomass.
    """

    coefficient_kg_per_mj: float = 0.368
    pass_gap_minutes: float = 30.0
    pass_window_hours: float = 6.0

    def __post_init__(self) -> None:
        if self.coefficient_kg_per_mj <= 0:
            raise InvalidConfigError("biomass.coefficient_kg_per_mj must be > 0")

        if self.pass_gap_minutes <= 0 or self.pass_window_hours <= 0:
            raise InvalidConfigError("biomass: pass_gap_minutes and pass_window_hours must be > 0")


@dataclass(frozen=True)
class GeometryConfig:
    """Calculation/storage CRS, nominal pixel size and footprint buffer."""

    metric_crs: str = "EPSG:25830"
    storage_crs: str = "EPSG:4326"
    footprint_buffer_m: float = 400.0
    # Nominal pixel side of each sensor, in km: MODIS 1 km, VIIRS 375 m. It fills in
    # ``area_width_km`` and ``area_height_km`` of each detection.
    pixel_size_km: dict[str, float] = field(default_factory=lambda: {"MODIS": 1.0, "VIIRS": 0.375})

    def __post_init__(self) -> None:
        if self.footprint_buffer_m < 0:
            raise InvalidConfigError("geometry.footprint_buffer_m must be >= 0")

        if set(self.pixel_size_km) != {"MODIS", "VIIRS"} or any(
            size <= 0 for size in self.pixel_size_km.values()
        ):
            raise InvalidConfigError(
                "geometry.pixel_size_km needs MODIS and VIIRS with values > 0 (km)"
            )


@dataclass(frozen=True)
class WindowConfig:
    """Time partitioning."""

    mode: WindowMode = WindowMode.DAY_UTC

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", WindowMode(self.mode))


@dataclass(frozen=True)
class LinkingConfig:
    """Linking of observations across consecutive windows."""

    max_distance_m: float = 1000.0
    max_window_gap: int = 1

    def __post_init__(self) -> None:
        if self.max_distance_m < 0 or self.max_window_gap < 1:
            raise InvalidConfigError("linking: max_distance_m >= 0 y max_window_gap >= 1")


@dataclass(frozen=True)
class EventConfig:
    """Event status."""

    inactive_after_hours: float = 48.0
    reference_time: datetime | None = None

    def __post_init__(self) -> None:
        if isinstance(self.reference_time, str):
            object.__setattr__(self, "reference_time", datetime.fromisoformat(self.reference_time))

        if self.reference_time is not None and self.reference_time.tzinfo is None:
            raise InvalidConfigError("events.reference_time must include a time zone (UTC)")


@dataclass(frozen=True)
class OutputConfig:
    """Output destination."""

    directory: str = "output"
    # Cleaned final data for the map and the chatbot (GeoJSON + JSON).
    clean_data_directory: str = "clean_data"
    use_previous_state: bool = True


@dataclass(frozen=True)
class DatabaseConfig:
    """
    Loading of the pipeline tables into PostGIS (in addition to the GeoParquet files).

    Attributes
    ----------
    enabled : bool
        If ``True``, inserts the tables after writing the files.
    url : str | None
        Fixed connection URL (PostgreSQL/PostGIS from ``docker-compose.yml``, local
        development only).
    apply_schema : bool
        Reapplies the SQL schema (idempotent) before each load.
    """

    enabled: bool = False
    url: str | None = "postgresql://fires:fires@localhost:5432/fires"
    apply_schema: bool = True


def _default_download_sources() -> dict[str, int]:
    return {"modis": 2000, "viirs-snpp": 2012, "viirs-jpss1": 2018}


@dataclass(frozen=True)
class DownloadConfig:
    """
    Download of the NASA FIRMS annual archive per country (public, no account or token).

    Attributes
    ----------
    base_url : str
        Archive root: ``<base_url>/<source>/<year>/<source>_<year>_<country>.csv``.
    country : str
        Country as named by FIRMS (``Spain``).
    sources : dict[str, int]
        Source -> first year with data (MODIS since 2000, VIIRS S-NPP since 2012, NOAA-20
        since 2018).
    start_year : int
        First year to download.
    end_year : int | None
        Last year; ``None`` = last complete year (the current year is not in the archive yet).
    directory : str
        Destination folder of the CSV files.
    timeout_s, retries, retry_wait_s : float, int, float
        Maximum time per request, retries and wait (multiplied by the attempt number).
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
            raise InvalidConfigError("download.end_year must be >= start_year")

        if self.retries < 1 or self.timeout_s <= 0 or not self.sources:
            raise InvalidConfigError(
                "download: retries >= 1, timeout_s > 0 and at least one source"
            )


@dataclass(frozen=True)
class NrtConfig:
    """
    Download of the NASA FIRMS near-real-time (NRT) files for the current year.

    The annual archive (``download``) only goes up to the last complete year; the NRT covers the
    last ~60 days. It needs an Earthdata token in the ``token_env`` environment variable
    (the token **never** goes in this file).

    Attributes
    ----------
    enabled : bool
        If ``False`` the NRT is not downloaded.
    base_url : str
        NRT archive root; the files are in ``<base_url>/<source>/<region>/``.
    region : str
        Region of the NRT files (``Europe``).
    sources : tuple[str, ...]
        Folders of each sensor/satellite.
    token_env : str
        Name of the environment variable with the token (``Authorization: Bearer``). Takes
        priority.
    token_file : str
        Local file with the token (one line), **ignored by git**, used if there is no environment
        variable. A relative path is resolved against the ``etls`` folder.
    directory : str
        Destination folder of the files.
    refetch_days : int
        The files of the last few days are always downloaded again: they grow during the day.
    timeout_s, retries, retry_wait_s : float, int, float
        Maximum time per request, retries and wait.
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
            raise InvalidConfigError("nrt.sources needs at least one folder")


@dataclass(frozen=True)
class LandCoverConfig:
    """
    Optional enrichment of detections with land cover (SIGPAC).

    Attributes
    ----------
    use_database : bool
        If ``True``, queries the parcels in ``fire.sigpac_recintos`` (uses ``database.url``)
        and takes priority over ``sigpac_dir``.
    sigpac_dir : str | None
        Folder with the SIGPAC parcels (shapefile / GeoPackage). Without ``use_database``, ``None``
        disables the step.
    sigpac_confidence : float
        Confidence assigned to the points resolved with SIGPAC.
    min_confidence : float
        Confidence threshold for ``is_agricultural`` / ``is_forest``.
    """

    use_database: bool = False
    sigpac_dir: str | None = None
    sigpac_confidence: float = 0.95
    min_confidence: float = 0.0


def _default_ignored_skip_reasons() -> tuple[str, ...]:
    return ("active_volcano", "volcanic_eruption")


def _default_protected_land_covers() -> tuple[str, ...]:
    return ("trees", "shrub_and_scrub", "grass")


@dataclass(frozen=True)
class StaticSourcesConfig:
    """
    Static thermal sources (industry, flares, cooling lava...) found in the detection history.

    ``scripts/load_static_sources.py`` grids the detections of ``fire.detections`` into square
    cells and stores in ``fire.static_sources`` the cells where the heat is persistent (it shows
    up all year round, year after year) or that FIRMS already labels as static. The pipeline then
    flags the new detections that fall in those cells (the NRT files have no FIRMS ``type``).

    Attributes
    ----------
    enabled : bool
        If ``True``, the pipeline reads ``fire.static_sources`` (``database.url``) and flags the
        detections inside them. Without the table it only logs a warning.
    grid_crs : str
        Projected CRS (meters) of the grid. Changing it (or ``cell_size_m``) requires running the
        analysis again.
    cell_size_m : float
        Side of each cell, in meters (~ one VIIRS pixel).
    min_days, min_years, min_months_of_year : int
        Persistence criterion: a cell is static if it has detections on at least ``min_days``
        distinct days, in ``min_years`` distinct years and in ``min_months_of_year`` distinct
        calendar months (a wildfire or a crop burn is seasonal; an industry is not).
    min_firms_static : int
        A cell with at least this many detections labeled by FIRMS as static
        (``firms_static_reason``) is static too. ``0`` disables this criterion.
    firms_static_reason : str
        ``skip_reason`` given to the FIRMS ``type`` of static land sources.
    ignored_skip_reasons : tuple[str, ...]
        Detections left out of the analysis (volcanoes already have their own reason).
    match_neighbors : bool
        Also flags the detections in the 8 cells around a static one (geolocation error of the
        pixels), unless their land cover is in ``protected_land_covers``.
    protected_land_covers : tuple[str, ...]
        SIGPAC land covers (``land_cover``) that are never flagged by neighborhood.
    skip_reason : str
        Reason written in the flagged detections.
    """

    enabled: bool = False
    grid_crs: str = "EPSG:25830"
    cell_size_m: float = 400.0
    min_days: int = 10
    min_years: int = 2
    min_months_of_year: int = 6
    min_firms_static: int = 3
    firms_static_reason: str = "static_land_source"
    ignored_skip_reasons: tuple[str, ...] = field(default_factory=_default_ignored_skip_reasons)
    match_neighbors: bool = True
    protected_land_covers: tuple[str, ...] = field(default_factory=_default_protected_land_covers)
    skip_reason: str = "persistent_thermal_source"

    def __post_init__(self) -> None:
        if self.cell_size_m <= 0:
            raise InvalidConfigError("static_sources.cell_size_m must be > 0")

        if min(self.min_days, self.min_years, self.min_months_of_year) < 1:
            raise InvalidConfigError(
                "static_sources: min_days, min_years and min_months_of_year must be >= 1"
            )

        if not 1 <= self.min_months_of_year <= 12:
            raise InvalidConfigError("static_sources.min_months_of_year must be in 1..12")

        if self.min_firms_static < 0:
            raise InvalidConfigError("static_sources.min_firms_static must be >= 0")


@dataclass(frozen=True)
class PipelineConfig:
    """Complete pipeline configuration."""

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
    static_sources: StaticSourcesConfig = field(default_factory=StaticSourcesConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    nrt: NrtConfig = field(default_factory=NrtConfig)

    def algorithm_parameters(self) -> dict[str, Any]:
        """
        Returns the parameters that affect the result, to record them in each event.

        Returns
        -------
        dict[str, Any]
            JSON-serializable parameters.
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
        """Serializable configuration; the database URL is hidden (it contains the password)."""
        data = asdict(self)
        data["database"]["url"] = "***" if self.database.url else None

        return data


def _build_section(section_cls: type, values: dict[str, Any] | None, name: str) -> Any:
    values = values or {}
    known = {item.name for item in fields(section_cls)}
    unknown = set(values) - known
    if unknown:
        raise InvalidConfigError(f"{name}: unknown keys {sorted(unknown)}")

    normalized = {
        key: tuple(value) if isinstance(value, list) else value for key, value in values.items()
    }

    return section_cls(**normalized)


def config_from_dict(raw: dict[str, Any]) -> PipelineConfig:
    """
    Builds the configuration from a dictionary (e.g. an already parsed YAML).

    Parameters
    ----------
    raw : dict[str, Any]
        Configuration sections; missing ones take their default value.

    Returns
    -------
    PipelineConfig
        Validated configuration.

    Raises
    ------
    InvalidConfigError
        If there are unknown sections or keys or out-of-range values.
    """
    sections = {item.name: item.type for item in fields(PipelineConfig)}
    unknown = set(raw) - set(sections)
    if unknown:
        raise InvalidConfigError(f"Unknown sections: {sorted(unknown)}")

    defaults = PipelineConfig()
    built = {}
    for name in sections:
        section_cls = type(getattr(defaults, name))
        assert is_dataclass(section_cls)
        built[name] = _build_section(section_cls, raw.get(name), name)

    return PipelineConfig(**built)


def load_config(path: str | Path | None = None) -> PipelineConfig:
    """
    Loads the configuration from a YAML file; without a path it returns the default values.

    Parameters
    ----------
    path : str | Path | None
        Path of the YAML file.

    Returns
    -------
    PipelineConfig
        Validated configuration.
    """
    if path is None:
        return PipelineConfig()

    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    return config_from_dict(raw)
