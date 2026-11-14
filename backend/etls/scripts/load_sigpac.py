"""
Descarga los recintos SIGPAC del FEGA y los carga en PostGIS, provincia a provincia.

    python scripts/load_sigpac.py

Sin argumentos lista los 50 zips provinciales de ``SIGPAC_BASE_URL`` y, de uno en uno, hace:
descargar -> descomprimir -> leer con geopandas -> cargar en ``fire.sigpac_recintos`` -> borrar
zip y GeoPackage. Las provincias ya cargadas se saltan antes de descargar, así que se puede
interrumpir y volver a lanzar. Si un zip falla se informa y se sigue con el siguiente.

Opcionales:
    python scripts/load_sigpac.py data/sigpac/    # usa zips locales en vez de descargar
    python scripts/load_sigpac.py --force         # recarga aunque ya estén cargadas
    python scripts/load_sigpac.py --only 20 28    # solo esas provincias (código INE)
    python scripts/load_sigpac.py --keep-zips     # conserva los zips descargados
"""

import argparse
import logging
import sys
from pathlib import Path

from _paths import ETLS_DIR
from fire_etl.config import load_config
from fire_etl.postgres_loader import apply_schema, describe_url
from fire_etl.sigpac_loader import (
    SIGPAC_BASE_URL,
    SigpacLoadError,
    find_zips,
    list_remote_zips,
    load_remote,
    load_zip,
)

CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
DOWNLOAD_DIR = ETLS_DIR / "data" / "sigpac"  # ignorado por git
CONNECT_TIMEOUT_S = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, help="zips o carpetas locales (opcional)")
    parser.add_argument("--url", help="URL de PostgreSQL (por defecto, database.url del YAML)")
    parser.add_argument("--base-url", default=SIGPAC_BASE_URL, help="listado de zips del FEGA")
    parser.add_argument("--download-dir", type=Path, default=DOWNLOAD_DIR)
    parser.add_argument("--only", nargs="+", metavar="PROV", help="códigos de provincia (20 28)")
    parser.add_argument("--force", action="store_true", help="recarga los ya cargados")
    parser.add_argument("--keep-zips", action="store_true", help="no borra los zips descargados")
    parser.add_argument("--work-dir", type=Path, help="carpeta donde descomprimir (disco)")

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
        logging.error("No hay zips que procesar.")
        return 2

    import psycopg

    failed: list[str] = []
    loaded = skipped = 0
    logging.info("PostGIS: conectando a %s...", describe_url(url))
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
            except Exception as error:  # un zip malo no debe parar el resto
                logging.error("%s: FALLO (%s)", item.name, error)
                failed.append(item.name)
                continue

            skipped += result.skipped
            loaded += not result.skipped

    print(f"\nCargados: {loaded} | saltados: {skipped} | fallidos: {len(failed)}")
    for name in failed:
        print(f"  FALLO {name}")
    print("Land cover: SIGPAC (FEGA / Ministerio de Agricultura, Pesca y Alimentación), CC BY 4.0")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
