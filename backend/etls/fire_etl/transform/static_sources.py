"""
Static thermal sources (industry, gas flares, cooling lava...) found in the detection history.

FIRMS only labels static land sources (``type = 2``) in the annual files: the near-real-time ones
have no ``type``, so a steelworks shows up as a new fire on every pass. This module:

1. Analysis (``scripts/load_static_sources.py``): grids every detection of ``fire.detections``
   into square cells of ``cell_size_m`` in ``grid_crs`` and computes per cell how many distinct
   days, years and calendar months had heat (``CELL_STATS_SQL``). :func:`select_static_cells`
   keeps the persistent ones (heat all year round, year after year: a wildfire or a crop burn is
   seasonal) and those that FIRMS already labels as static. They are stored in
   ``fire.static_sources`` (:mod:`fire_etl.load.static_sources`).
2. Pipeline: :func:`fetch_static_cells` reads the table and :func:`flag_static_sources` flags
   (``is_valid=False``, ``skip_reason``) the detections that fall in a static cell or, with
   ``match_neighbors``, in one of its 8 neighbors unless SIGPAC says the point is vegetation.

The grid is computed with the same CRS in PostGIS and in Python (PROJ in both), so the cell
indices match.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from pyproj import CRS, Transformer

from fire_etl.config import StaticSourcesConfig
from fire_etl.transform.firms.validation import BELOW_FRP_REASON

LOGGER = logging.getLogger(__name__)

TABLE = "fire.static_sources"
SOURCE_SRID = 4326
CONNECT_TIMEOUT_S = 5
MULTI_SEPARATOR = "|"  # several land covers of one detection (see sigpac_land_cover.py)

CRITERION_PERSISTENCE = "persistence"
CRITERION_FIRMS = "firms_type"
CRITERION_BOTH = "persistence+firms_type"

STAT_COLUMNS = (
    "ix",
    "iy",
    "n_detections",
    "n_days",
    "n_years",
    "n_months_of_year",
    "n_firms_static",
    "first_date",
    "last_date",
)

# Statistics per cell of every detection (valid or flagged) except the ignored reasons. The
# HAVING only trims the cells that can never pass the thresholds (select_static_cells decides).
CELL_STATS_SQL = """
WITH gridded AS (
    SELECT floor(ST_X(p) / %(size)s)::int AS ix,
           floor(ST_Y(p) / %(size)s)::int AS iy,
           acq_date,
           skip_reason
    FROM (
        SELECT ST_Transform(geom_point, %(srid)s) AS p, acq_date, skip_reason
        FROM fire.detections
        WHERE skip_reason IS NULL OR NOT (skip_reason = ANY(%(ignored)s))
    ) AS d
)
SELECT ix,
       iy,
       count(*) AS n_detections,
       count(DISTINCT acq_date) AS n_days,
       count(DISTINCT extract(year FROM acq_date)) AS n_years,
       count(DISTINCT extract(month FROM acq_date)) AS n_months_of_year,
       count(*) FILTER (WHERE skip_reason = %(firms_reason)s) AS n_firms_static,
       min(acq_date) AS first_date,
       max(acq_date) AS last_date
FROM gridded
GROUP BY ix, iy
HAVING count(DISTINCT acq_date) >= %(min_days)s
    OR count(*) FILTER (WHERE skip_reason = %(firms_reason)s) >= %(min_firms)s
"""

FETCH_CELLS_SQL = f"SELECT ix, iy FROM {TABLE} WHERE grid_srid = %s AND cell_size_m = %s"


class StaticSourcesError(ValueError):
    """Invalid grid or input for the static sources."""


@dataclass
class FlagSummary:
    """Counts of the flagging, for the log."""

    static_cells: int = 0
    in_cell: int = 0
    in_neighbor: int = 0
    protected: int = 0

    @property
    def flagged(self) -> int:
        return self.in_cell + self.in_neighbor


# --- Grid -----------------------------------------------------------------------------------


def grid_srid(config: StaticSourcesConfig) -> int:
    """
    EPSG code of the grid CRS.

    Raises
    ------
    StaticSourcesError
        If the CRS has no EPSG code or is not projected in meters.
    """
    crs = CRS.from_user_input(config.grid_crs)
    units = {axis.unit_name for axis in crs.axis_info}
    srid = crs.to_epsg()
    if not crs.is_projected or units != {"metre"} or srid is None:
        raise StaticSourcesError(
            f"static_sources.grid_crs={config.grid_crs} must be an EPSG CRS projected in meters"
        )

    return srid


def cell_indices(
    lons: pd.Series, lats: pd.Series, config: StaticSourcesConfig
) -> tuple[np.ndarray, np.ndarray]:
    """
    Grid cell ``(ix, iy)`` of each point (same formula as ``CELL_STATS_SQL``).

    Parameters
    ----------
    lons, lats : pd.Series
        Coordinates in EPSG:4326.
    config : StaticSourcesConfig
        Grid CRS and cell size.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Integer indices (``float`` NaN where the coordinates are missing).
    """
    transformer = Transformer.from_crs(SOURCE_SRID, grid_srid(config), always_xy=True)
    xs, ys = transformer.transform(
        pd.to_numeric(lons, errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(lats, errors="coerce").to_numpy(dtype=float),
    )

    return np.floor(xs / config.cell_size_m), np.floor(ys / config.cell_size_m)


# --- Analysis -------------------------------------------------------------------------------


def stats_query_params(config: StaticSourcesConfig) -> dict[str, Any]:
    """Parameters of ``CELL_STATS_SQL``."""
    min_firms = config.min_firms_static if config.min_firms_static > 0 else np.iinfo(np.int32).max

    return {
        "size": float(config.cell_size_m),
        "srid": grid_srid(config),
        "ignored": list(config.ignored_skip_reasons),
        "firms_reason": config.firms_static_reason,
        "min_days": int(config.min_days),
        "min_firms": int(min_firms),
    }


def select_static_cells(stats: pd.DataFrame, config: StaticSourcesConfig) -> pd.DataFrame:
    """
    Keeps the static cells and labels the criterion that made them static.

    Parameters
    ----------
    stats : pd.DataFrame
        One row per cell with ``STAT_COLUMNS``.
    config : StaticSourcesConfig
        Thresholds.

    Returns
    -------
    pd.DataFrame
        Static cells with ``STAT_COLUMNS`` and ``criterion``, sorted by ``n_days`` (desc).
    """
    missing = set(STAT_COLUMNS) - set(stats.columns)
    if missing:
        raise StaticSourcesError(f"Missing cell statistics: {sorted(missing)}")

    persistent = (
        (stats["n_days"] >= config.min_days)
        & (stats["n_years"] >= config.min_years)
        & (stats["n_months_of_year"] >= config.min_months_of_year)
    )
    firms = pd.Series(False, index=stats.index)
    if config.min_firms_static > 0:
        firms = stats["n_firms_static"] >= config.min_firms_static

    criterion = np.select(
        [persistent & firms, persistent, firms],
        [CRITERION_BOTH, CRITERION_PERSISTENCE, CRITERION_FIRMS],
        default="",
    )
    selected = stats.loc[persistent | firms, list(STAT_COLUMNS)].copy()
    selected["criterion"] = criterion[(persistent | firms).to_numpy()]

    return selected.sort_values(["n_days", "ix", "iy"], ascending=[False, True, True]).reset_index(
        drop=True
    )


def compute_cell_stats(connection: Any, config: StaticSourcesConfig) -> pd.DataFrame:
    """
    Runs ``CELL_STATS_SQL`` on ``fire.detections``.

    Parameters
    ----------
    connection : psycopg.Connection
        Connection to the database with the detection history.
    config : StaticSourcesConfig
        Grid and thresholds.

    Returns
    -------
    pd.DataFrame
        One row per candidate cell with ``STAT_COLUMNS``.
    """
    with connection.cursor() as cursor:
        cursor.execute(CELL_STATS_SQL, stats_query_params(config))
        rows = cursor.fetchall()

    stats = pd.DataFrame(rows, columns=list(STAT_COLUMNS))
    for column in STAT_COLUMNS[:7]:
        stats[column] = stats[column].astype("int64")

    return stats


# --- Pipeline -------------------------------------------------------------------------------


def fetch_static_cells(url: str, config: StaticSourcesConfig) -> set[tuple[int, int]] | None:
    """
    Reads the static cells of the configured grid from ``fire.static_sources``.

    Returns
    -------
    set[tuple[int, int]] | None
        ``(ix, iy)`` of each cell; ``None`` if the table cannot be read (the pipeline continues
        without the filter).
    """
    try:
        import psycopg
    except ImportError:
        LOGGER.error("Missing driver: pip install 'psycopg[binary]'. Static sources not applied.")
        return None

    try:
        with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S) as connection:
            with connection.cursor() as cursor:
                cursor.execute(FETCH_CELLS_SQL, (grid_srid(config), float(config.cell_size_m)))
                rows = cursor.fetchall()
    except Exception as error:
        # database down or table missing: the pipeline continues without the filter
        LOGGER.warning(
            "Static sources not applied (%s). Run scripts/load_static_sources.py.",
            str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__,
        )
        return None

    if not rows:
        LOGGER.warning(
            "%s has no cells for grid EPSG:%s / %s m: run scripts/load_static_sources.py.",
            TABLE,
            grid_srid(config),
            config.cell_size_m,
        )

    return {(int(ix), int(iy)) for ix, iy in rows}


def _neighbors(cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
    ring = {
        (ix + dx, iy + dy)
        for ix, iy in cells
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if dx or dy
    }

    return ring - cells


def _is_protected(land_cover: pd.Series, protected: tuple[str, ...]) -> pd.Series:
    allowed = set(protected)

    return land_cover.map(
        lambda value: isinstance(value, str) and bool(allowed & set(value.split(MULTI_SEPARATOR)))
    ).astype(bool)


def flag_static_sources(
    detections: pd.DataFrame, cells: set[tuple[int, int]], config: StaticSourcesConfig
) -> tuple[pd.DataFrame, FlagSummary]:
    """
    Flags the detections that fall in a static cell (or next to one).

    Only valid detections and those flagged for low FRP are touched (a source reason wins over
    low FRP, as with the FIRMS ``type``); volcanoes or exclusion zones keep their reason.

    Parameters
    ----------
    detections : pd.DataFrame
        Detections with ``lon``, ``lat``, ``is_valid``, ``skip_reason`` and, optionally,
        ``land_cover``.
    cells : set[tuple[int, int]]
        Static cells of the configured grid.
    config : StaticSourcesConfig
        Grid, neighborhood and reason.

    Returns
    -------
    tuple[pd.DataFrame, FlagSummary]
        Updated detections (copy) and counts.
    """
    summary = FlagSummary(static_cells=len(cells))
    if not cells or detections.empty:
        return detections, summary

    ix, iy = cell_indices(detections["lon"], detections["lat"], config)
    keys = pd.MultiIndex.from_arrays([ix, iy])
    flaggable = detections["skip_reason"].isna() | (detections["skip_reason"] == BELOW_FRP_REASON)
    flaggable = flaggable.to_numpy()

    in_cell = keys.isin(list(cells)) & flaggable
    in_neighbor = np.zeros(len(detections), dtype=bool)
    if config.match_neighbors:
        near = keys.isin(list(_neighbors(cells))) & flaggable
        protected = np.zeros(len(detections), dtype=bool)
        if "land_cover" in detections:
            protected = _is_protected(
                detections["land_cover"], config.protected_land_covers
            ).to_numpy()

        in_neighbor = near & ~protected
        summary.protected = int((near & protected).sum())

    summary.in_cell = int(in_cell.sum())
    summary.in_neighbor = int(in_neighbor.sum())
    flagged = in_cell | in_neighbor
    if not flagged.any():
        return detections, summary

    updated = detections.copy()
    updated.loc[flagged, "is_valid"] = False
    updated.loc[flagged, "skip_reason"] = config.skip_reason

    return updated, summary


def log_flag_summary(summary: FlagSummary, config: StaticSourcesConfig) -> None:
    """Logs the result of :func:`flag_static_sources`."""
    LOGGER.info(
        "Static sources (%s cells): %s detections flagged as %s (%s in a cell, %s next to one; "
        "%s next to one kept because of their vegetation land cover)",
        summary.static_cells,
        summary.flagged,
        config.skip_reason,
        summary.in_cell,
        summary.in_neighbor,
        summary.protected,
    )
