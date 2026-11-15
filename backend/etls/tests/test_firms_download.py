"""Tests de la descarga del archivo anual de FIRMS (con un servidor HTTP local, sin Internet)."""

from __future__ import annotations

import functools
import http.server
import threading
from datetime import datetime, timezone

import pytest

from fire_etl.config import DownloadConfig
from fire_etl.firms_download import build_url, download_all, last_complete_year, plan_downloads

CSV = "latitude,longitude,acq_date\n42.5,-7.2,2020-01-01\n"
LOGIN_PAGE = "<html><body>Earthdata Login</body></html>"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "server"
    for source, year in (("modis", 2011), ("modis", 2012), ("modis", 2013), ("viirs-snpp", 2012)):
        path = root / source / str(year) / f"{source}_{year}_Spain.csv"
        path.parent.mkdir(parents=True)
        path.write_text(CSV, encoding="utf-8")

    handler = functools.partial(QuietHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def local_config(base_url: str, tmp_path) -> DownloadConfig:
    return DownloadConfig(
        base_url=base_url,
        sources={"modis": 2000, "viirs-snpp": 2012},
        start_year=2011,
        end_year=2013,
        directory=str(tmp_path / "raw"),
        retries=1,
        retry_wait_s=0.0,
    )


def test_build_url_follows_the_firms_country_archive_layout():
    url = build_url(DownloadConfig(), "viirs-jpss1", 2022)

    assert url == (
        "https://firms.modaps.eosdis.nasa.gov/data/country/viirs-jpss1/2022/"
        "viirs-jpss1_2022_Spain.csv"
    )


def test_plan_starts_each_source_at_its_first_year_up_to_the_last_complete_year():
    today = datetime(2026, 11, 14, tzinfo=timezone.utc)

    items = plan_downloads(DownloadConfig(), "raw", today)
    years = {
        source: [item.year for item in items if item.source == source]
        for source in DownloadConfig().sources
    }

    assert last_complete_year(today) == 2025
    assert (years["modis"][0], years["modis"][-1], len(years["modis"])) == (2000, 2025, 26)
    assert (years["viirs-snpp"][0], years["viirs-snpp"][-1]) == (2012, 2025)
    assert (years["viirs-jpss1"][0], years["viirs-jpss1"][-1]) == (2018, 2025)
    assert len(items) == 26 + 14 + 8


def test_plan_respects_start_and_end_year():
    config = DownloadConfig(start_year=2015, end_year=2019)

    items = plan_downloads(config, "raw")

    assert {(i.source, i.year) for i in items if i.source == "modis"} == {
        ("modis", year) for year in range(2015, 2020)
    }
    assert min(i.year for i in items if i.source == "viirs-snpp") == 2015
    assert min(i.year for i in items if i.source == "viirs-jpss1") == 2018


def test_download_all_downloads_skips_existing_and_reports_missing(server, tmp_path):
    _, base_url = server
    config = local_config(base_url, tmp_path)

    first = download_all(config)
    second = download_all(config)

    assert len(first.downloaded) == 4 and first.skipped == []
    assert [url.rsplit("/", 1)[-1] for url in first.missing] == ["viirs-snpp_2013_Spain.csv"]
    assert first.failed == {}
    assert len(second.skipped) == 4 and second.downloaded == []
    assert (tmp_path / "raw" / "modis_2012_Spain.csv").read_text(encoding="utf-8") == CSV
    assert not list((tmp_path / "raw").glob("*.part"))


def test_download_rejects_non_csv_content_and_keeps_going(server, tmp_path):
    root, base_url = server
    (root / "modis" / "2012" / "modis_2012_Spain.csv").write_text(LOGIN_PAGE, encoding="utf-8")

    result = download_all(local_config(base_url, tmp_path))

    assert len(result.failed) == 1 and "modis_2012_Spain.csv" in next(iter(result.failed))
    assert not (tmp_path / "raw" / "modis_2012_Spain.csv").exists()
    assert not list((tmp_path / "raw").glob("*.part"))
    assert len(result.downloaded) == 3  # el resto se descarga igualmente


def test_download_config_validation():
    with pytest.raises(ValueError):
        DownloadConfig(start_year=2020, end_year=2010)
