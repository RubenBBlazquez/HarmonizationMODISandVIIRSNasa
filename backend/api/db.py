"""Conexión a PostGIS para la API (una conexión por petición; suficiente para el MVP)."""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row

# Dentro de docker-compose la variable DATABASE_URL apunta al contenedor `postgis`.
DEFAULT_DATABASE_URL = "postgresql://fires:fires@localhost:5432/fires"
CONNECT_TIMEOUT_S = 5


def database_url() -> str:
    """URL de conexión: ``DATABASE_URL`` o la base local de desarrollo."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_connection() -> Iterator[psycopg.Connection]:
    """Dependencia de FastAPI: abre una conexión de solo lectura lógica y la cierra al acabar."""
    with psycopg.connect(
        database_url(), row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT_S
    ) as connection:
        yield connection
