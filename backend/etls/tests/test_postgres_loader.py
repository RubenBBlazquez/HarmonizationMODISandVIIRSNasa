"""Tests de la carga en PostGIS con una conexión simulada (no necesitan base de datos)."""

from __future__ import annotations

import datetime as dt
import logging
import sys
import types
from dataclasses import replace

import pytest

from fire_etl.config import DatabaseConfig, PipelineConfig
from fire_etl.pipeline import process_detections
from fire_etl.postgres_loader import (
    TABLE_ORDER,
    apply_schema,
    build_load_plan,
    describe_url,
    execute_plan,
    insert_statement,
    load_to_postgres,
    truncate_statement,
)
from tests.conftest import ingest_rows, viirs_row

SECRET_URL = "postgresql://fires:topsecret@localhost:5432/fires"
SRID = 4326


class FakeTransaction:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        self.log.append("begin")

    def __exit__(self, *exc):
        self.log.append("rollback" if exc[0] else "commit")
        return False


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query):
        self.log.append(("execute", query))

    def executemany(self, query, rows):
        self.log.append(("executemany", query, len(rows)))


class FakeConnection:
    def __init__(self):
        self.log = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return FakeTransaction(self.log)

    def cursor(self):
        return FakeCursor(self.log)


@pytest.fixture
def tables(config):
    rows = [
        viirs_row("2026-09-14 23:50", frp=50.0),
        viirs_row("2026-09-15 00:10", north_m=200, frp=60.0),
    ]

    return process_detections(ingest_rows(config, viirs=rows), config)


@pytest.fixture
def enabled_config(config):
    return replace(config, database=DatabaseConfig(enabled=True, url=SECRET_URL))


def test_load_plan_follows_foreign_key_order_and_counts_rows(tables):
    plan = build_load_plan(tables, "EPSG:4326")
    by_table = {load.table: load for load in plan}

    assert [load.table for load in plan] == list(TABLE_ORDER)
    assert len(by_table["detections"].rows) == len(tables.detections) == 2
    assert len(by_table["fire_events"].rows) == len(tables.events) == 1
    assert len(by_table["event_detections"].rows) == 2
    assert all(len(row) == len(load.columns) for load in plan for row in load.rows)


def test_load_plan_converts_values_to_database_types(tables):
    detections = next(l for l in build_load_plan(tables, "EPSG:4326") if l.table == "detections")
    row = dict(zip(detections.columns, detections.rows[0]))

    assert row["geom_point"].startswith("POINT (-7.2")
    assert row["geom_footprint"].startswith("POLYGON")
    assert isinstance(row["acq_datetime"], dt.datetime) and row["acq_datetime"].tzinfo
    assert row["is_valid"] is True and row["skip_reason"] is None
    assert isinstance(row["frp"], float) and isinstance(row["is_representative"], bool)
    assert row["frp_class"] == 2  # int de Python, no numpy ni pd.NA


def test_insert_statement_casts_geometries_and_json(tables):
    plan = {load.table: load for load in build_load_plan(tables, "EPSG:4326")}

    detections_sql = insert_statement(plan["detections"], SRID)
    events_sql = insert_statement(plan["fire_events"], SRID)

    assert detections_sql.startswith("INSERT INTO fire.detections (detection_id,")
    assert detections_sql.count("ST_GeomFromText(%s, 4326)") == 2
    assert events_sql.count("%s::jsonb") == 3  # frp_series, merged_event_ids, params
    assert plan["detections"].columns.count("geom_point") == 1


def test_execute_plan_truncates_then_inserts_in_one_transaction(tables):
    connection = FakeConnection()
    plan = build_load_plan(tables, "EPSG:4326")

    counts = execute_plan(connection, plan, SRID)
    log = connection.log

    assert log[0] == "begin" and log[-1] == "commit" and log.count("begin") == 1
    assert log[1] == ("execute", truncate_statement())
    inserted = [entry[1].split()[2] for entry in log if entry[0] == "executemany"]
    assert inserted == [f"fire.{name}" for name in TABLE_ORDER if name != "event_merges"]
    assert counts["detections"] == 2 and counts["event_merges"] == 0


def test_execute_plan_inserts_in_chunks_and_logs_progress(tables, monkeypatch, caplog):
    monkeypatch.setattr("fire_etl.postgres_loader.INSERT_CHUNK_ROWS", 1)
    connection = FakeConnection()
    plan = build_load_plan(tables, "EPSG:4326")

    with caplog.at_level(logging.INFO, logger="fire_etl.postgres_loader"):
        counts = execute_plan(connection, plan, SRID)

    inserted = [entry for entry in connection.log if entry[0] == "executemany"]
    assert len(inserted) == sum(counts.values())  # un executemany por fila con trozos de 1
    assert all(entry[2] == 1 for entry in inserted)
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "Insertando 2 filas en fire.detections" in messages
    assert "detections: 1 de 2 filas" in messages


def test_truncate_lists_every_table_so_foreign_keys_are_allowed():
    statement = truncate_statement()

    assert statement.startswith("TRUNCATE ")
    assert all(f"fire.{name}" in statement for name in TABLE_ORDER)


def test_apply_schema_runs_the_sql_script(tmp_path):
    script = tmp_path / "schema.sql"
    script.write_text("CREATE SCHEMA IF NOT EXISTS fire;", encoding="utf-8")
    connection = FakeConnection()

    assert apply_schema(connection, script) is True
    assert ("execute", "CREATE SCHEMA IF NOT EXISTS fire;") in connection.log
    assert apply_schema(FakeConnection(), tmp_path / "missing.sql") is False


def test_load_is_skipped_when_database_is_disabled(tables, config):
    assert load_to_postgres(tables, config) is False


def test_load_without_url_logs_error(tables, config, caplog):
    no_url = replace(config, database=DatabaseConfig(enabled=True, url=None))

    with caplog.at_level(logging.ERROR):
        assert load_to_postgres(tables, no_url) is False

    assert "database.url" in caplog.text


def test_load_without_driver_logs_error_and_does_not_raise(
    tables, enabled_config, monkeypatch, caplog
):
    monkeypatch.setitem(sys.modules, "psycopg", None)  # simula que no está instalado

    with caplog.at_level(logging.ERROR):
        assert load_to_postgres(tables, enabled_config) is False

    assert "psycopg" in caplog.text


def test_load_failure_is_logged_without_credentials(tables, enabled_config, monkeypatch, caplog):
    def refuse(*args, **kwargs):
        raise RuntimeError("conexión rechazada")

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=refuse))

    with caplog.at_level(logging.ERROR):
        assert load_to_postgres(tables, enabled_config) is False

    assert "No se pudo cargar en PostGIS (localhost:5432/fires)" in caplog.text
    assert "conexión rechazada" in caplog.text
    assert "topsecret" not in caplog.text


def test_load_success_inserts_through_the_connection(tables, enabled_config, monkeypatch):
    connection = FakeConnection()
    monkeypatch.setitem(
        sys.modules, "psycopg", types.SimpleNamespace(connect=lambda *a, **k: connection)
    )

    assert load_to_postgres(tables, enabled_config) is True
    assert ("execute", truncate_statement()) in connection.log
    assert sum(1 for entry in connection.log if entry[0] == "executemany") == 4


def test_default_url_is_the_fixed_local_postgis_and_is_described_without_password():
    url = DatabaseConfig().url

    assert url == "postgresql://fires:fires@localhost:5432/fires"
    assert describe_url(url) == "localhost:5432/fires"


def test_database_password_is_not_serialized_in_the_config_dump():
    config = PipelineConfig(database=DatabaseConfig(enabled=True, url=SECRET_URL))

    assert config.to_dict()["database"]["url"] == "***"
    assert "topsecret" not in str(config.to_dict())
