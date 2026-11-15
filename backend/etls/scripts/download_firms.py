"""
Descarga los datos de NASA FIRMS (MODIS y VIIRS): archivo anual de España desde 2000 y NRT.

1. **Archivo anual** (sin token): un CSV por fuente y año completo, ya recortado a España
   (``download`` en ``config/default.yaml``). Los ya descargados no se vuelven a pedir.
2. **NRT del año en curso** (necesita token): ficheros diarios de Europa de los últimos ~60 días
   (``nrt``). El token (nunca va en el YAML ni en git) se lee de la variable de entorno
   ``EARTHDATA_TOKEN`` o, si no existe, del fichero local ``.secrets/earthdata_token`` (ignorado
   por git):

       python scripts/download_firms.py

   Sin token se omite el NRT y se avisa. Los ficheros de los últimos días se vuelven a bajar
   siempre porque crecen durante el día.

Atención: entre el fin del archivo anual (31-dic del último año completo) y el inicio del NRT
(~60 días atrás) hay un hueco que ninguna de las dos fuentes cubre; se avisa al final.

Deja los CSV en ``data/raw/`` y ``data/raw/nrt/`` (ignorados por git).
"""

import logging
from datetime import timedelta

from _paths import ETLS_DIR

from fire_etl.config import load_config
from fire_etl.firms_download import download_all
from fire_etl.firms_nrt import download_nrt, first_uncovered_day, nrt_files

CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
RAW_DIR = ETLS_DIR / "data" / "raw"
NRT_DIR = RAW_DIR / "nrt"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

config = load_config(CONFIG_FILE)
result = download_all(config.download, RAW_DIR)

print(f"\nDescargados: {len(result.downloaded)} | ya existían: {len(result.skipped)}")
print(f"No existen en el archivo: {len(result.missing)} | fallidos: {len(result.failed)}")
for url, reason in result.failed.items():
    print(f"  FALLO {url}: {reason}")

print(f"Ficheros en {RAW_DIR}: {len(result.available)}")

nrt = download_nrt(config.nrt, NRT_DIR, base_dir=ETLS_DIR)
print(
    f"\nNRT: descargados {len(nrt.downloaded)} | sin cambios {len(nrt.skipped)} | "
    f"fallidos {len(nrt.failed)} | ficheros en {NRT_DIR}: {len(nrt_files(NRT_DIR))}"
)
if nrt.token_missing:
    print(f"  Sin NRT: pon el token en la variable {config.nrt.token_env}")
    print(f"  o en el fichero local {config.nrt.token_file} (ignorado por git).")
if nrt.auth_failed:
    print("  El servidor rechazó el token (caducado o no válido): genera uno nuevo en Earthdata.")

if nrt.first_day:
    gap_start = first_uncovered_day()
    gap_end = nrt.first_day - timedelta(days=1)
    if gap_end >= gap_start:
        print(
            f"\nATENCIÓN, hueco sin cubrir: del {gap_start} al {gap_end} "
            f"({(gap_end - gap_start).days + 1} días). El archivo anual llega hasta "
            f"el {gap_start - timedelta(days=1)} y el NRT empieza el {nrt.first_day}."
        )
