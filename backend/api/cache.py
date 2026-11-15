"""Caché en memoria con caducidad para respuestas costosas de la API."""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from threading import Lock
from typing import Any


class TTLCache:
    """
    Caché con caducidad y tamaño máximo (se descarta la entrada más antigua).

    Parameters
    ----------
    ttl_seconds : float
        Segundos que vive cada entrada.
    max_entries : int
        Máximo de entradas guardadas.
    clock : Callable[[], float]
        Reloj monotónico (inyectable para los tests).
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._items: dict[Hashable, tuple[float, Any]] = {}
        self._lock = Lock()

    def get_or_compute(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        """Devuelve el valor guardado y vigente de ``key`` o lo calcula y lo guarda."""
        now = self._clock()
        with self._lock:
            entry = self._items.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]

        value = compute()  # fuera del lock: una consulta lenta no bloquea al resto
        with self._lock:
            self._items[key] = (self._clock() + self._ttl, value)
            while len(self._items) > self._max_entries:
                self._items.pop(next(iter(self._items)))

        return value

    def clear(self) -> None:
        """Vacía la caché (por ejemplo, tras cargar datos nuevos)."""
        with self._lock:
            self._items.clear()
