"""
Finds the static thermal sources (industry, flares, lava...) and loads them into
``fire.static_sources``.

    python scripts/load_static_sources.py

Grids every detection of ``fire.detections`` (section ``static_sources`` of the YAML) and keeps
the cells with heat all year round, year after year, or labeled by FIRMS as static land source.
The pipeline then flags the new detections that fall in them (the NRT files have no FIRMS
``type``). Run it again after loading more history; it only replaces the cells of the configured
grid.

Optional:
    python scripts/load_static_sources.py --dry-run                    # analysis only, no load
    python scripts/load_static_sources.py --url postgresql://...       # load into another database
    python scripts/load_static_sources.py --source-url postgresql://...  # history from another one

Computing on the local database (full history, with the FIRMS ``type`` labels) and loading into
Railway (``--url``) is the recommended setup: Railway only keeps part of the history.
"""

import argparse
import logging
import sys

from _paths import ETLS_DIR

from fire_etl.config import load_config
from fire_etl.load.postgres import describe_url
from fire_etl.load.static_sources import StaticSourcesLoadError, ensure_table, load_static_sources
from fire_etl.transform.static_sources import (
    StaticSourcesError,
    compute_cell_stats,
    select_static_cells,
)

CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
CONNECT_TIMEOUT_S = 5
TOP_CELLS = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--url", help="database to load into (by default, database.url of the YAML)"
    )
    parser.add_argument(
        "--source-url", help="database with fire.detections (by default, database.url of the YAML)"
    )
    parser.add_argument("--dry-run", action="store_true", help="analyze and log, without loading")

    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    config = load_config(CONFIG_FILE)
    default_url = config.database.url
    source_url = args.source_url or default_url
    target_url = args.url or default_url
    if not source_url or not target_url:
        logging.error("No database URL: set database.url in the YAML or pass --url/--source-url")
        return 2

    import psycopg

    rules = config.static_sources
    logging.info("Analyzing fire.detections in %s", describe_url(source_url))
    try:
        with psycopg.connect(source_url, connect_timeout=CONNECT_TIMEOUT_S) as connection:
            stats = compute_cell_stats(connection, rules)
        cells = select_static_cells(stats, rules)
    except StaticSourcesError as error:
        logging.error("%s", error)
        return 2

    logging.info(
        "%s candidate cells -> %s static (%s m, %s): %s",
        len(stats),
        len(cells),
        rules.cell_size_m,
        rules.grid_crs,
        cells["criterion"].value_counts().to_dict(),
    )
    for row in cells.head(TOP_CELLS).itertuples(index=False):
        logging.info(
            "  cell (%s, %s): %s days, %s years, %s months, %s FIRMS static, %s..%s [%s]",
            row.ix,
            row.iy,
            row.n_days,
            row.n_years,
            row.n_months_of_year,
            row.n_firms_static,
            row.first_date,
            row.last_date,
            row.criterion,
        )

    if args.dry_run:
        logging.info("--dry-run: nothing loaded")
        return 0

    logging.info("Loading into %s", describe_url(target_url))
    with psycopg.connect(target_url, connect_timeout=CONNECT_TIMEOUT_S) as connection:
        ensure_table(connection)
        try:
            load_static_sources(connection, cells, rules)
        except StaticSourcesLoadError as error:
            logging.error("%s", error)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
