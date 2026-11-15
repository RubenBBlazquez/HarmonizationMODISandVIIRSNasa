"""Tests de la caché con caducidad (sin base de datos)."""

from __future__ import annotations

from backend.api.cache import TTLCache


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_cache_computes_once_while_the_entry_is_fresh():
    calls = []
    cache = TTLCache(10, clock=Clock())

    for _ in range(3):
        assert cache.get_or_compute("a", lambda: calls.append(1) or "value") == "value"

    assert len(calls) == 1


def test_cache_recomputes_after_the_ttl():
    clock, calls = Clock(), []
    cache = TTLCache(10, clock=clock)
    cache.get_or_compute("a", lambda: calls.append(1))
    clock.now = 11
    cache.get_or_compute("a", lambda: calls.append(1))

    assert len(calls) == 2


def test_cache_keeps_keys_apart_and_drops_the_oldest_when_full():
    cache = TTLCache(10, max_entries=2, clock=Clock())
    for key in ("a", "b", "c"):
        cache.get_or_compute(key, lambda key=key: key)
    calls = []
    cache.get_or_compute("a", lambda: calls.append("a") or "a")  # "a" ya se descartó
    cache.get_or_compute("c", lambda: calls.append("c") or "c")  # "c" sigue guardada

    assert calls == ["a"]


def test_cache_clear_forces_a_new_computation():
    calls = []
    cache = TTLCache(10, clock=Clock())
    cache.get_or_compute("a", lambda: calls.append(1))
    cache.clear()
    cache.get_or_compute("a", lambda: calls.append(1))

    assert len(calls) == 2
