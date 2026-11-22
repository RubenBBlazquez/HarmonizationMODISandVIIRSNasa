"""Connected components from geometry pairs obtained with the spatial index."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def component_labels(n_nodes: int, left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """
    Labels the connected components of an undirected graph.

    Parameters
    ----------
    n_nodes : int
        Number of nodes (positions ``0..n_nodes-1``).
    left, right : np.ndarray
        Endpoints of each edge.

    Returns
    -------
    np.ndarray
        Integer component label per node.
    """
    if n_nodes == 0:
        return np.empty(0, dtype=np.int64)

    weights = np.ones(len(left), dtype=np.int8)
    graph = coo_matrix((weights, (left, right)), shape=(n_nodes, n_nodes))
    _, labels = connected_components(graph, directed=False)

    return labels.astype(np.int64)


def self_join_pairs(
    geometries: gpd.GeoSeries, predicate: str, distance: float | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """
    Positional pairs ``(i, j)`` of geometries that satisfy ``predicate`` (via ``sjoin``).

    Parameters
    ----------
    geometries : gpd.GeoSeries
        Geometries in a metric CRS.
    predicate : str
        Spatial predicate of ``geopandas.sjoin`` (``intersects``, ``dwithin``...).
    distance : float | None
        Distance in CRS units for ``dwithin``.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Left and right positions of each pair (includes the ``(i, i)`` pairs).
    """
    if geometries.empty:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)

    frame = gpd.GeoDataFrame(geometry=geometries.reset_index(drop=True))
    join_kwargs = {"distance": distance} if distance is not None else {}
    pairs = gpd.sjoin(frame, frame, how="inner", predicate=predicate, **join_kwargs)

    return pairs.index.to_numpy(dtype=np.int64), pairs["index_right"].to_numpy(dtype=np.int64)
