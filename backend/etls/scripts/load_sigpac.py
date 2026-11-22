"""
Downloads the SIGPAC parcels from the FEGA and loads them into PostGIS, province by province.

    python scripts/load_sigpac.py

Without arguments it lists the 50 provincial zips of ``SIGPAC_BASE_URL`` and, one by one, does:
download -> unzip -> read with geopandas -> load into ``fire.sigpac_recintos`` -> delete the
zip and the GeoPackage. Provinces already loaded are skipped before downloading, so it can be
interrupted and launched again. If a zip fails it is reported and the next one is processed.

Optional:
    python scripts/load_sigpac.py data/sigpac/    # uses local zips instead of downloading
    python scripts/load_sigpac.py --force         # reloads even if already loaded
    python scripts/load_sigpac.py --only 20 28    # only those provinces (INE code)
    python scripts/load_sigpac.py --keep-zips     # keeps the downloaded zips
"""

import argparse
import logging
import sys
from pathlib import Path

from _paths import ETLS_DIR

from fire_etl.config import load_config
from fire_etl.load.postgres import apply_schema, describe_url
from fire_etl.load.sigpac import (
    SIGPAC_BASE_URL,
    SigpacLoadError,
    find_zips,
    list_remote_zips,
    load_remote,
    load_zip,
)

CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
DOWNLOAD_DIR = ETLS_DIR / "data" / "sigpac"  # ignored by git
CONNECT_TIMEOUT_S = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, help="local zips or folders (optional)")
    parser.add_argument("--url", help="PostgreSQL URL (by default, database.url of the YAML)")
    parser.add_argument("--base-url", default=SIGPAC_BASE_URL, help="listing of the FEGA zips")
    parser.add_argument("--download-dir", type=Path, default=DOWNLOAD_DIR)
    parser.add_argument("--only", nargs="+", metavar="PROV", help="province codes (20 28)")
    parser.add_argument("--force", action="store_true", help="reload the ones already loaded")
    parser.add_argument(
        "--keep-zips", action="store_true", help="do not delete the downloaded zips"
    )
    parser.add_argument("--work-dir", type=Path, help="folder to unzip into (disk)")

    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    url = args.url or load_config(CONFIG_FILE).database.url
    try:
        if args.paths:
            items = find_zips(args.paths)
        else:
            items = list_remote_zips(args.base_url)
    except SigpacLoadError as error:
        logging.error("%s", error)
        return 2

    if args.only:
        wanted = {f"{int(code):02d}_" for code in args.only}
        items = [i for i in items if i.name[:3] in wanted]

    if not items:
        logging.error("No zips to process.")
        return 2

    import psycopg

    failed: list[str] = []
    loaded = skipped = 0
    logging.info("PostGIS: connecting to %s...", describe_url(url))
    with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S, autocommit=True) as connection:
        apply_schema(connection)
        for number, item in enumerate(items, start=1):
            logging.info("[%d/%d] %s", number, len(items), item.name)
            try:
                if args.paths:
                    result = load_zip(connection, item, args.force, args.work_dir)
                else:
                    result = load_remote(
                        connection, item, args.download_dir, args.force, args.keep_zips
                    )
            except Exception as error:  # one bad zip must not stop the rest
                logging.error("%s: FAILED (%s)", item.name, error)
                failed.append(item.name)
                continue

            skipped += result.skipped
            loaded += not result.skipped

    print(f"\nLoaded: {loaded} | skipped: {skipped} | failed: {len(failed)}")
    for name in failed:
        print(f"  FAILED {name}")
    print("Land cover: SIGPAC (FEGA / Ministerio de Agricultura, Pesca y Alimentación), CC BY 4.0")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
