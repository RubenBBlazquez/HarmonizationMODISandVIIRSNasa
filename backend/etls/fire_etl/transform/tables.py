"""Final tables of the pipeline: the result of the transformation that the load consumes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import pandas as pd


@dataclass(frozen=True)
class PipelineTables:
    """
    Final tables of the pipeline (geometries in the metric CRS).

    Attributes
    ----------
    detections, observations, events : gpd.GeoDataFrame
        Layers with geometry.
    event_detections, merges, discarded : pd.DataFrame
        Tables without geometry.
    run_parameters : dict[str, Any]
        Parameters and metrics of the run.
    """

    detections: gpd.GeoDataFrame
    observations: gpd.GeoDataFrame
    events: gpd.GeoDataFrame
    event_detections: pd.DataFrame
    merges: pd.DataFrame
    discarded: pd.DataFrame
    run_parameters: dict[str, Any]
