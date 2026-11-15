"""Tests de la carga de recintos SIGPAC (GeoPackage sintético y conexión simulada)."""

from __future__ import annotations

import zipfile
from contextlib import contextmanager
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from fire_etl import sigpac_loader
from fire_etl.sigpac_loader import (
    SigpacLoadError,
    extract_gpkg,
    find_zips,
    load_zip,
    prepare_rows,
    read_chunks,
)


def make_gpkg(path: Path, provincias: tuple[int, ...] = (20, 20, 20)) -> Path:
    frame = gpd.GeoDataFrame(
        {
            "provincia": list(provincias),
            "municipio": [1, 1, 2],
            "agregado": [0, 0, 0],
            "zona": [0, 0, 0],
            "poligono": [1, 1, 1],
            "parcela": [1, 2, 1],
            "recinto": [1, 1, 1],
            "dn_surface": [100.0, 200.0, 300.0],
            "pendiente_media": [10, 20, 30],
            "altitud": [5, 6, 7],
            "csp": [100.0, 0.0, 50.0],
            "coef_regadio": [0, 0, 0],
            "uso_sigpac": ["ta", "FO", None],
            "incidencias": ["11,12", None, None],
            "region": [5.0, None, 18.0],
        },
        geometry=[box(-2.0 + i, 43.0, -1.9 + i, 43.1) for i in range(3)],
        crs=4258,
    )
    frame.to_file(path, layer="recinto", driver="GPKG")

    return path


def make_zip(tmp_path: Path, name: str = "20_X_gpkg.zip", **kwargs) -> Path:
    gpkg = make_gpkg(tmp_path / "1520_X.gpkg", **kwargs)
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(gpkg, gpkg.name)
    gpkg.unlink()

    return zip_path


class FakeCopy:
    def __init__(self, log):
        self.log = log

    def write_row(self, row):
        self.log.append(("row", row))


class FakeCursor:
    def __init__(self, log, loaded):
        self.log = log
        self.loaded = loaded

    def execute(self, query, params=None):
        self.log.append(("sql", query.split()[0], params))
        self.last = query

    def fetchone(self):
        return (1,) if self.loaded else None

    @contextmanager
    def copy(self, query):
        self.log.append(("copy", query))
        yield FakeCopy(self.log)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, loaded=False):
        self.log = []
        self.loaded = loaded

    @contextmanager
    def transaction(self):
        self.log.append("begin")
        try:
            yield
        except Exception:
            self.log.append("rollback")
            raise
        self.log.append("commit")

    def cursor(self):
        return FakeCursor(self.log, self.loaded)


def test_extract_gpkg_requires_exactly_one_gpkg(tmp_path):
    empty = tmp_path / "e.zip"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "x")

    with pytest.raises(SigpacLoadError):
        extract_gpkg(empty, tmp_path / "out")

    ok = make_zip(tmp_path)
    assert extract_gpkg(ok, tmp_path / "out").suffix == ".gpkg"


def test_extract_gpkg_rejects_non_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("no soy un zip")

    with pytest.raises(SigpacLoadError):
        extract_gpkg(bad, tmp_path / "out")


def test_find_zips_expands_directories_and_rejects_missing(tmp_path):
    (tmp_path / "b.zip").touch()
    (tmp_path / "a.zip").touch()
    (tmp_path / "c.txt").touch()

    assert [p.name for p in find_zips([tmp_path])] == ["a.zip", "b.zip"]
    with pytest.raises(SigpacLoadError):
        find_zips([tmp_path / "nope.zip"])


def test_read_chunks_splits_and_reprojects(tmp_path):
    chunks = list(read_chunks(make_gpkg(tmp_path / "a.gpkg"), chunk_rows=2))

    assert [len(c) for c in chunks] == [2, 1]
    assert chunks[0].crs.to_epsg() == 4326


def test_prepare_rows_normalizes_uso_and_nulls(tmp_path):
    chunk = next(read_chunks(make_gpkg(tmp_path / "a.gpkg")))
    rows = prepare_rows(chunk)
    by_col = [dict(zip(sigpac_loader.TABLE_COLUMNS, row)) for row in rows]

    assert by_col[0]["uso_sigpac"] == "TA"
    assert by_col[0]["land_cover"] == "crops"
    assert by_col[0]["region"] == 5
    assert by_col[1]["land_cover"] == "trees"
    assert by_col[1]["region"] is None and by_col[1]["incidencias"] is None
    assert by_col[2]["uso_sigpac"] is None and by_col[2]["land_cover"] is None
    assert by_col[0]["geom"].startswith("0103000020E6100000")  # polígono con SRID 4326


def test_load_zip_replaces_province_in_one_transaction(tmp_path):
    connection = FakeConnection()
    result = load_zip(connection, make_zip(tmp_path))

    assert (result.provincia, result.n_recintos, result.skipped) == (20, 3, False)
    kinds = [e[1] if e[0] == "sql" else e[0] for e in connection.log if e != "begin"]
    assert kinds.index("SELECT") < kinds.index("DELETE") < kinds.index("copy")
    assert "DELETE" in kinds and "INSERT" in kinds and "copy" in kinds
    assert connection.log.count("begin") == 1 and connection.log[-1] == "commit"
    assert sum(1 for e in connection.log if e[0] == "row") == 3


def test_load_zip_skips_already_loaded_without_extracting(tmp_path):
    connection = FakeConnection(loaded=True)
    result = load_zip(connection, tmp_path / "no_existe.zip")

    assert result.skipped and result.n_recintos == 0
    assert "begin" not in connection.log


def test_load_zip_force_reloads(tmp_path):
    connection = FakeConnection(loaded=True)

    assert not load_zip(connection, make_zip(tmp_path), force=True).skipped


def test_load_zip_rejects_mixed_provinces_and_rolls_back(tmp_path):
    connection = FakeConnection()

    with pytest.raises(SigpacLoadError):
        load_zip(connection, make_zip(tmp_path, provincias=(20, 20, 21)))

    assert connection.log[-1] == "rollback"


LISTING = (
    '<pre><A HREF="/geopackages/2026/">[To Parent Directory]</A><br><br> 1/9/2026  7:42 AM'
    '    727436525 <A HREF="/geopackages/2026/recintos/02_ALBACETE_rec_gpkg.zip">x</A><br>'
    ' 1/9/2026  7:42 AM    359506354 <A HREF="/geopackages/2026/recintos/'
    '07_ILLES%20BALEARS_rec_gpkg.zip">y</A><br></pre>'
)


def test_list_remote_zips_decodes_names_and_sizes(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return LISTING.encode()

    monkeypatch.setattr(sigpac_loader.urllib.request, "urlopen", lambda *a, **k: Response())
    remote = sigpac_loader.list_remote_zips("https://example.org/geopackages/2026/recintos/")

    assert [r.name for r in remote] == [
        "02_ALBACETE_rec_gpkg.zip",
        "07_ILLES BALEARS_rec_gpkg.zip",
    ]
    assert remote[1].size == 359506354
    assert remote[1].url == (
        "https://example.org/geopackages/2026/recintos/07_ILLES%20BALEARS_rec_gpkg.zip"
    )


def test_load_remote_skips_loaded_before_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr(sigpac_loader, "download_zip", lambda *a: pytest.fail("no debe descargar"))
    remote = sigpac_loader.RemoteZip("20_X_gpkg.zip", "https://x/20.zip", 1)

    assert sigpac_loader.load_remote(FakeConnection(loaded=True), remote, tmp_path).skipped


def test_load_remote_downloads_loads_and_deletes_zip(tmp_path, monkeypatch):
    zip_path = make_zip(tmp_path / "dl" if (tmp_path / "dl").mkdir() is None else tmp_path)
    monkeypatch.setattr(sigpac_loader, "download_zip", lambda remote, dest: zip_path)
    remote = sigpac_loader.RemoteZip(zip_path.name, "https://x/20.zip", None)

    result = sigpac_loader.load_remote(FakeConnection(), remote, tmp_path)

    assert result.n_recintos == 3 and not zip_path.exists()
