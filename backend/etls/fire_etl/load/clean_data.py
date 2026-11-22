"""
Writing of the final data (``clean_data``) as GeoJSON and JSON for the map and the chatbot.

The data is built in :mod:`fire_etl.transform.clean_data`; here it is only serialized.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from shapely.geometry import Point, mapping

from fire_etl.transform.clean_data import DETECTION_COLUMNS, EVENT_COLUMNS, CleanData, to_records

EVENTS_GEOJSON = "fire_events.geojson"
AREAS_GEOJSON = "fire_event_areas.geojson"
DETECTIONS_GEOJSON = "fire_detections.geojson"
EVENTS_JSON = "fire_events.json"
METADATA_JSON = "metadata.json"


def _feature_collection(
    properties: pd.DataFrame, geometries: Sequence[Any], columns: Sequence[str]
) -> dict[str, Any]:
    features = [
        {"type": "Feature", "geometry": mapping(geometry), "properties": props}
        for props, geometry in zip(to_records(properties.loc[:, list(columns)]), geometries)
    ]

    return {"type": "FeatureCollection", "features": features}


def _points(frame: pd.DataFrame) -> list[Point]:
    return [Point(lon, lat) for lon, lat in zip(frame["lon"], frame["lat"])]


def _dump(data: Any, path: Path, indent: int | None = None) -> None:
    """Writes compact JSON (the data files are large); ``indent`` only for human reading."""
    separators = (",", ": ") if indent else (",", ":")
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=indent, separators=separators)


def write_clean_data(clean: CleanData, directory: str | Path) -> Path:
    """
    Writes the ``clean_data`` files into ``directory`` (created if it does not exist).

    Parameters
    ----------
    clean : CleanData
        Result of :func:`fire_etl.transform.clean_data.build_clean_data`.
    directory : str | Path
        Output folder.

    Returns
    -------
    Path
        The output folder.
    """
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    events, detections = clean.events, clean.detections

    _dump(_feature_collection(events, _points(events), EVENT_COLUMNS), output / EVENTS_GEOJSON)
    area_columns = ["event_id", "status", "frp_category", "footprint_area_km2"]
    _dump(_feature_collection(events, clean.areas, area_columns), output / AREAS_GEOJSON)
    _dump(
        _feature_collection(detections, _points(detections), DETECTION_COLUMNS),
        output / DETECTIONS_GEOJSON,
    )
    _dump(to_records(events.loc[:, [*EVENT_COLUMNS, "frp_series"]]), output / EVENTS_JSON)
    _dump(clean.metadata, output / METADATA_JSON, indent=2)

    return output
