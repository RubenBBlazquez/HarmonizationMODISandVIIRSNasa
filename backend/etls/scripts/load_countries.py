"""
Downloads the country borders (Natural Earth 1:50m) and loads them into ``fire.countries``.

    python scripts/load_countries.py

The zip is kept in ``data/natural_earth/`` (ignored by git) and is not downloaded again. The
schema is applied first (idempotent), so it also works on a database created before the table
existed. Reloading replaces every country.

Optional:
    python scripts/load_countries.py path/to/ne_50m_admin_0_countries.zip   # local file
    python scripts/load_countries.py --url postgresql://...                  # other database
"""

import argparse
import logging
import sys
from pathlib import Path

from _paths import ETLS_DIR

from fire_etl.config import load_config
from fire_etl.load.countries import (
    NATURAL_EARTH_URL,
    CountriesLoadError,
    download_borders,
    load_countries,
    prepare_rows,
    read_borders,
)
from fire_etl.load.postgres import apply_schema, describe_url

CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
DOWNLOAD_FILE = ETLS_DIR / "data" / "natural_earth" / "ne_50m_admin_0_countries.zip"
CONNECT_TIMEOUT_S = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", nargs="?", type=Path, help="local Natural Earth zip (optional)")
    parser.add_argument("--url", help="PostgreSQL URL (by default, database.url of the YAML)")
    parser.add_argument("--source-url", default=NATURAL_EARTH_URL, help="Natural Earth zip URL")

    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    url = args.url or load_config(CONFIG_FILE).database.url
    try:
        path = args.path or download_borders(DOWNLOAD_FILE, args.source_url)
        rows = prepare_rows(read_borders(path))
    except (OSError, CountriesLoadError) as error:
        logging.error("%s", error)
        return 2

    import psycopg

    logging.info("Connecting to %s", describe_url(url))
    with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S) as connection:
        apply_schema(connection)
        try:
            load_countries(connection, rows)
        except CountriesLoadError as error:
            logging.error("%s", error)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
