"""Tests of the NRT download with a local HTTP server that requires a token (no Internet)."""

from __future__ import annotations

import http.server
import logging
import threading
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from fire_etl.config import DownloadConfig, NrtConfig
from fire_etl.extract.firms.archive import plan_downloads
from fire_etl.extract.firms.nrt import (
    download_nrt,
    first_uncovered_day,
    needs_download,
    nrt_files,
    parse_listing,
    plan_nrt,
    resolve_token,
)

TOKEN = "test-token-0123456789"
TOKEN_ENV = "TEST_EARTHDATA_TOKEN"
TODAY = date(2026, 11, 14)
HEADER = "latitude,longitude,acq_date\n"
LISTING_ROW = '<tr data-path="x" data-name="{name}" title="Open"><td>{name}</td></tr>'


def name_for(day: date) -> str:
    return f"MODIS_C6_1_Europe_MCD14DL_NRT_{day.year}{day.timetuple().tm_yday:03d}.txt"


class Server:
    """Fake NRT server: public listing, files with token and login page."""

    def __init__(self, days: list[date]):
        self.files = {name_for(day): f"{HEADER}42.5,-7.2,{day}\n" for day in days}
        self.hits: list[str] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                outer.hits.append(self.path)
                if self.path.endswith("/Europe/"):
                    rows = "".join(LISTING_ROW.format(name=n) for n in sorted(outer.files))
                    self._send(200, f"<html><table>{rows}</table></html>")
                elif self.path == "/login":
                    self._send(200, "<html>Earthdata Login</html>")
                else:
                    name = self.path.rsplit("/", 1)[-1]
                    if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                        self.send_response(303)
                        self.send_header("Location", "/login")
                        self.end_headers()
                    elif name in outer.files:
                        self._send(200, outer.files[name])
                    else:
                        self._send(404, "does not exist")

            def _send(self, code, body):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}/archive/FIRMS"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def make_server():
    servers = []

    def factory(days):
        server = Server(days)
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.close()


def nrt_config(server: Server, tmp_path) -> NrtConfig:
    return NrtConfig(
        base_url=server.base_url,
        sources=("modis-c6.1",),
        token_env=TOKEN_ENV,
        # A file that does not exist: the tests must never read the real token from .secrets/.
        token_file=str(tmp_path / "no-token"),
        directory=str(tmp_path / "nrt"),
        refetch_days=2,
        retries=1,
        retry_wait_s=0.0,
    )


def days_back(count: int) -> list[date]:
    return [date.fromordinal(TODAY.toordinal() - offset) for offset in range(count, -1, -1)]


def test_parse_listing_extracts_unique_files_with_their_day():
    html = (
        LISTING_ROW.format(name="MODIS_C6_1_Europe_MCD14DL_NRT_2026258.txt")
        + LISTING_ROW.format(name="MODIS_C6_1_Europe_MCD14DL_NRT_2026258.txt")  # repeated
        + LISTING_ROW.format(name="MODIS_C6_1_Europe_MCD14DL_NRT_2026318.txt")
        + '<a href="/help/">help</a><tr data-name="README.txt">'
    )

    assert parse_listing(html) == [
        ("MODIS_C6_1_Europe_MCD14DL_NRT_2026258.txt", date(2026, 9, 15)),
        ("MODIS_C6_1_Europe_MCD14DL_NRT_2026318.txt", date(2026, 11, 14)),
    ]


def test_plan_only_includes_days_the_annual_archive_does_not_cover(tmp_path):
    config = NrtConfig(sources=("modis-c6.1",), directory=str(tmp_path))
    remote = {
        "modis-c6.1": [
            ("old.txt", date(2025, 12, 31)),  # covered by the annual archive
            ("new.txt", date(2026, 1, 1)),
            ("newer.txt", date(2026, 11, 14)),
        ]
    }

    items = plan_nrt(config, remote, tmp_path, TODAY)

    assert [item.name for item in items] == ["new.txt", "newer.txt"]
    assert first_uncovered_day(TODAY) == date(2026, 1, 1)


def test_annual_archive_never_plans_the_current_year_even_if_configured():
    today = datetime(2026, 11, 14, tzinfo=timezone.utc)

    default = plan_downloads(DownloadConfig(), "raw", today)
    forced = plan_downloads(DownloadConfig(end_year=2026), "raw", today)

    assert max(item.year for item in default) == 2025
    assert max(item.year for item in forced) == 2025  # 2026 comes from the NRT, not the archive


def test_recent_files_are_refetched_but_old_ones_are_not(tmp_path):
    config = NrtConfig(sources=("modis-c6.1",), directory=str(tmp_path), refetch_days=2)
    remote = {"modis-c6.1": [(name_for(day), day) for day in days_back(5)]}
    items = plan_nrt(config, remote, tmp_path, TODAY)
    for item in items:
        item.path.write_text(HEADER, encoding="utf-8")

    refetch = [item.day for item in items if needs_download(item, 2, TODAY)]

    assert refetch == [date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 14)]
    items[0].path.unlink()  # a missing file is downloaded even if it is old
    assert needs_download(items[0], 2, TODAY)


def test_download_with_a_valid_token_saves_files_and_hides_the_token(
    make_server, tmp_path, monkeypatch, caplog
):
    server = make_server(days_back(3))
    monkeypatch.setenv(TOKEN_ENV, TOKEN)

    with caplog.at_level(logging.DEBUG):
        result = download_nrt(nrt_config(server, tmp_path), today=TODAY)

    assert len(result.downloaded) == 4 and not result.failed and not result.auth_failed
    assert (result.first_day, result.last_day) == (date(2026, 11, 11), TODAY)
    assert len(nrt_files(tmp_path / "nrt")) == 4
    assert not list((tmp_path / "nrt").glob("*.part"))
    assert TOKEN not in caplog.text


def test_second_run_only_refreshes_the_most_recent_days(make_server, tmp_path, monkeypatch):
    server = make_server(days_back(6))
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    config = nrt_config(server, tmp_path)
    download_nrt(config, today=TODAY)
    newest = name_for(TODAY)
    server.files[newest] = HEADER + "42.5,-7.2,more-data\n"  # today's file has grown
    server.hits.clear()

    second = download_nrt(config, today=TODAY)

    fetched = {path.rsplit("/", 1)[-1] for path in server.hits if path.endswith(".txt")}
    assert fetched == {name_for(day) for day in days_back(2)}  # the last three days
    assert len(second.downloaded) == 3 and len(second.skipped) == 4
    assert "more-data" in (tmp_path / "nrt" / newest).read_text(encoding="utf-8")


def test_invalid_token_stops_without_following_the_redirect(make_server, tmp_path, monkeypatch):
    server = make_server(days_back(3))
    monkeypatch.setenv(TOKEN_ENV, "expired-token")

    result = download_nrt(nrt_config(server, tmp_path), today=TODAY)

    assert result.auth_failed and not result.downloaded
    assert "/login" not in server.hits  # the redirect was not followed (the token does not leave)
    assert len([hit for hit in server.hits if hit.endswith(".txt")]) == 1  # stopped at the first
    assert nrt_files(tmp_path / "nrt") == []


def test_missing_token_skips_everything_without_calling_the_server(
    make_server, tmp_path, monkeypatch, caplog
):
    server = make_server(days_back(3))
    monkeypatch.delenv(TOKEN_ENV, raising=False)

    with caplog.at_level(logging.WARNING):
        result = download_nrt(nrt_config(server, tmp_path), today=TODAY)

    assert result.token_missing and not result.downloaded
    assert server.hits == []
    assert TOKEN_ENV in caplog.text


def write_token_file(tmp_path, content: str) -> NrtConfig:
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "earthdata_token").write_text(content, encoding="utf-8")

    return NrtConfig(token_env=TOKEN_ENV)


def test_token_is_read_from_the_local_file_when_there_is_no_environment_variable(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    config = write_token_file(tmp_path, f"# Earthdata token\n\n  {TOKEN}  \nanother-line\n")

    assert resolve_token(config, tmp_path) == TOKEN  # first useful line, without spaces


def test_environment_variable_has_priority_over_the_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "from-env")
    config = write_token_file(tmp_path, "from-file\n")

    assert resolve_token(config, tmp_path) == "from-env"


@pytest.mark.parametrize("content", ["", "\n\n", "# just a comment\n"])
def test_empty_or_missing_token_file_gives_no_token(tmp_path, monkeypatch, content):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    config = write_token_file(tmp_path, content)

    assert resolve_token(config, tmp_path) == ""
    assert resolve_token(config, tmp_path / "other-folder") == ""  # the file does not exist there


def test_download_uses_the_local_token_file_and_never_logs_it(
    make_server, tmp_path, monkeypatch, caplog
):
    server = make_server(days_back(1))
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "earthdata_token").write_text(TOKEN + "\n", encoding="utf-8")
    config = replace(nrt_config(server, tmp_path), token_file=".secrets/earthdata_token")

    with caplog.at_level(logging.DEBUG):
        result = download_nrt(config, today=TODAY, base_dir=tmp_path)

    assert len(result.downloaded) == 2 and not result.token_missing
    assert TOKEN not in caplog.text


def test_download_reports_progress_so_it_does_not_look_stuck(
    make_server, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr("fire_etl.extract.firms.nrt.PROGRESS_EVERY", 2)
    server = make_server(days_back(4))  # 5 files
    monkeypatch.setenv(TOKEN_ENV, TOKEN)

    with caplog.at_level(logging.INFO, logger="fire_etl.extract.firms.nrt"):
        download_nrt(nrt_config(server, tmp_path), today=TODAY)

    messages = [record.getMessage() for record in caplog.records]
    assert any("NRT: 5 files" in m and "5 to download" in m for m in messages)
    assert any("downloaded 1 of 5" in m for m in messages)  # progress at the halfway point
    assert any(m.startswith("NRT finished: 5 downloaded") for m in messages)


def test_disabled_nrt_does_nothing(make_server, tmp_path, monkeypatch):
    server = make_server(days_back(1))
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    config = NrtConfig(enabled=False, base_url=server.base_url, directory=str(tmp_path))

    result = download_nrt(config, today=TODAY)

    assert not result.downloaded and server.hits == []


def test_a_non_csv_response_is_reported_and_does_not_stop_other_files(
    make_server, tmp_path, monkeypatch
):
    server = make_server(days_back(2))
    bad = name_for(days_back(2)[1])
    server.files[bad] = "<html>Earthdata Login</html>"
    monkeypatch.setenv(TOKEN_ENV, TOKEN)

    result = download_nrt(nrt_config(server, tmp_path), today=TODAY)

    assert len(result.failed) == 1 and bad in next(iter(result.failed))
    assert len(result.downloaded) == 2
    assert not (tmp_path / "nrt" / bad).exists()


def test_nrt_config_validation():
    with pytest.raises(ValueError):
        NrtConfig(sources=())

    with pytest.raises(ValueError):
        NrtConfig(retries=0)
