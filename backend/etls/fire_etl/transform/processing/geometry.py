"""
Detection geometries: points, area-of-interest filter and footprints.

All distance and area calculations are done in a metric CRS (EPSG:25830 by default, or UTM);
never in degrees. Geometries are stored afterwards in EPSG:4326 (see
:mod:`fire_etl.load.geoparquet`).

Known limitation of the footprint: it is approximated as a rectangle aligned with the axes of
the metric CRS, of ``area_width_km`` in X by ``area_height_km`` in Y (km, generic: they do not
depend on the sensor) and centered on the point. It does not consider the real inclination of
the orbit, the *bow-tie* overlap or the pixel point spread function; the configurable buffer
absorbs part of that error and of the geolocation error.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS

from fire_etl.config import AreaOfInterestConfig, GeometryConfig

METERS_PER_KM = 1000.0
UTM_ZONE_WIDTH_DEG = 6.0


class MetricCrsError(ValueError):
    """The CRS configured for the calculations is not metric."""


@dataclass(frozen=True)
class DetectionGeometries:
    """
    Detections with geometry in the metric CRS.

    Attributes
    ----------
    detections : gpd.GeoDataFrame
        Detections inside the area of interest with ``geom_point``, ``geom_footprint`` (active) and
        ``footprint_area_m2``.
    metric_crs : CRS
        Metric CRS used in the calculations.
    outside_aoi : int
        Detections discarded for being outside the area of interest.
    """

    detections: gpd.GeoDataFrame
    metric_crs: CRS
    outside_aoi: int


def area_of_interest_geometry(aoi: AreaOfInterestConfig) -> shapely.Geometry | None:
    """
    Builds the geometry of the area of interest in EPSG:4326.

    Parameters
    ----------
    aoi : AreaOfInterestConfig
        Configured area.

    Returns
    -------
    shapely.Geometry | None
        Polygon of the area, or ``None`` if there is no filter.
    """
    if aoi.bbox is not None:
        return shapely.box(*aoi.bbox)

    if aoi.polygon_wkt is not None:
        return shapely.from_wkt(aoi.polygon_wkt)

    return None


def utm_crs_for_lon_lat(lon: float, lat: float) -> CRS:
    """
    Returns the UTM CRS (WGS84) of the zone containing ``(lon, lat)``.

    Parameters
    ----------
    lon : float
        Longitude in degrees.
    lat : float
        Latitude in degrees.

    Returns
    -------
    CRS
        EPSG:326xx (northern hemisphere) or EPSG:327xx (southern hemisphere).
    """
    zone = int(np.floor((lon + 180.0) / UTM_ZONE_WIDTH_DEG)) % 60 + 1
    base = 32600 if lat >= 0 else 32700

    return CRS.from_epsg(base + zone)


def resolve_metric_crs(spec: str, reference: shapely.Geometry | None) -> CRS:
    """
    Resolves the metric CRS used for calculations.

    Parameters
    ----------
    spec : str
        CRS code (e.g. ``EPSG:25830``) or ``auto`` for the UTM zone of the center of
        ``reference``.
    reference : shapely.Geometry | None
        Geometry in EPSG:4326 (area of interest or envelope of the data) used in ``auto``.

    Returns
    -------
    CRS
        Projected CRS in meters.

    Raises
    ------
    MetricCrsError
        If the CRS is not projected in meters or ``auto`` has no reference.
    """
    if spec.lower() == "auto":
        if reference is None or reference.is_empty:
            raise MetricCrsError("metric_crs=auto needs an area of interest or data")

        center = reference.centroid
        crs = utm_crs_for_lon_lat(center.x, center.y)
    else:
        crs = CRS.from_user_input(spec)

    units = {axis.unit_name for axis in crs.axis_info}
    if not crs.is_projected or units != {"metre"}:
        raise MetricCrsError(f"The CRS {crs.to_string()} is not projected in meters")

    return crs


def build_footprints(
    points: gpd.GeoSeries, width_km: pd.Series, height_km: pd.Series, buffer_m: float
) -> gpd.GeoSeries:
    """
    Builds the footprint of each detection: ``area_width_km`` × ``area_height_km`` rectangle
    plus the buffer.

    Parameters
    ----------
    points : gpd.GeoSeries
        Points in a metric CRS.
    width_km : pd.Series
        Width of the area in km, applied in X.
    height_km : pd.Series
        Height of the area in km, applied in Y.
    buffer_m : float
        Buffer in meters to absorb the position error.

    Returns
    -------
    gpd.GeoSeries
        Polygons in the same CRS as ``points``.
    """
    x = points.x.to_numpy()
    y = points.y.to_numpy()
    half_width = width_km.to_numpy(dtype=float) * METERS_PER_KM / 2.0
    half_height = height_km.to_numpy(dtype=float) * METERS_PER_KM / 2.0

    boxes = shapely.box(x - half_width, y - half_height, x + half_width, y + half_height)
    if buffer_m > 0:
        boxes = shapely.buffer(boxes, buffer_m, join_style="mitre")

    return gpd.GeoSeries(boxes, index=points.index, crs=points.crs)


def build_detection_geometries(
    detections: pd.DataFrame, geometry: GeometryConfig, aoi: AreaOfInterestConfig
) -> DetectionGeometries:
    """
    Creates points and footprints, filters by area of interest and reprojects to the metric CRS.

    Parameters
    ----------
    detections : pd.DataFrame
        Detections in the common schema (``lat``, ``lon``, ``area_width_km``...).
    geometry : GeometryConfig
        CRS and buffer.
    aoi : AreaOfInterestConfig
        Area of interest.

    Returns
    -------
    DetectionGeometries
        Geolocated detections in the metric CRS.
    """
    points = gpd.GeoSeries(
        gpd.points_from_xy(detections["lon"], detections["lat"]),
        index=detections.index,
        crs=geometry.storage_crs,
    )

    aoi_geometry = area_of_interest_geometry(aoi)
    inside = np.ones(len(points), dtype=bool)
    if aoi_geometry is not None:
        inside = points.intersects(aoi_geometry).to_numpy()

    reference = aoi_geometry
    if reference is None and inside.any():
        reference = shapely.box(*points[inside].total_bounds)

    metric_crs = resolve_metric_crs(geometry.metric_crs, reference)

    kept = detections[inside].copy()
    metric_points = points[inside].to_crs(metric_crs)
    footprints = build_footprints(
        metric_points, kept["area_width_km"], kept["area_height_km"], geometry.footprint_buffer_m
    )

    kept["geom_footprint"] = footprints
    kept["geom_point"] = metric_points
    frame = gpd.GeoDataFrame(kept, geometry="geom_footprint", crs=metric_crs)
    frame["footprint_area_m2"] = frame["geom_footprint"].area

    return DetectionGeometries(
        detections=frame, metric_crs=metric_crs, outside_aoi=int((~inside).sum())
    )
