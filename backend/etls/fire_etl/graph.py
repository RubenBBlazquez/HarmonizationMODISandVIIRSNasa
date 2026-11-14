"""Componentes conexas a partir de pares de geometrías obtenidos con el índice espacial."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def component_labels(n_nodes: int, left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """
    Etiqueta las componentes conexas de un grafo no dirigido.

    Parameters
    ----------
    n_nodes : int
        Número de nodos (posiciones ``0..n_nodes-1``).
    left, right : np.ndarray
        Extremos de cada arista.

    Returns
    -------
    np.ndarray
        Etiqueta entera de componente por nodo.
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
    Pares posicionales ``(i, j)`` de geometrías que cumplen ``predicate`` (vía ``sjoin``).

    Parameters
    ----------
    geometries : gpd.GeoSeries
        Geometrías en un CRS métrico.
    predicate : str
        Predicado espacial de ``geopandas.sjoin`` (``intersects``, ``dwithin``...).
    distance : float | None
        Distancia en unidades del CRS para ``dwithin``.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Posiciones izquierda y derecha de cada par (incluye los pares ``(i, i)``).
    """
    if geometries.empty:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)

    frame = gpd.GeoDataFrame(geometry=geometries.reset_index(drop=True))
    join_kwargs = {"distance": distance} if distance is not None else {}
    pairs = gpd.sjoin(frame, frame, how="inner", predicate=predicate, **join_kwargs)

    return pairs.index.to_numpy(dtype=np.int64), pairs["index_right"].to_numpy(dtype=np.int64)
