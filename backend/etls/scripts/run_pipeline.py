"""
Ejecuta el pipeline completo sin parámetros por línea de comandos.

Descarga (o reutiliza) el archivo de NASA FIRMS de España, MODIS y VIIRS desde el año 2000, y lo
procesa. Edita las constantes de abajo (o sustituye ``frames`` por tus DataFrames) y lanza el
script. Genera los datos finales en ``clean_data/`` (GeoJSON para el mapa, JSON para el
chatbot). Si lo ejecutas en la consola de Python de PyCharm, al terminar tienes a mano
``tables.detections``, ``tables.observations``, ``tables.events``, etc. como DataFrames.

    python scripts/run_pipeline.py
"""

import json
import logging

from _paths import ETLS_DIR, SAMPLE_DIR
from fire_etl.config import load_config
from fire_etl.firms_download import download_all
from fire_etl.firms_nrt import download_nrt, nrt_files
from fire_etl.ingest import read_firms_csv
from fire_etl.pipeline import run_frames

# --- Datos de entrada ------------------------------------------------------------------------
DOWNLOAD = True  # True: descarga el archivo FIRMS de España (o reutiliza lo ya descargado)
RAW_DIR = ETLS_DIR / "data" / "raw"
NRT_DIR = RAW_DIR / "nrt"  # NRT del año en curso (necesita el token EARTHDATA_TOKEN)
# Si no hay ficheros descargados (p. ej. sin Internet) se usan estas muestras.
MODIS_FILE = SAMPLE_DIR / "MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt"
VIIRS_FILE = SAMPLE_DIR / "J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt"
CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
OUTPUT_DIR = ETLS_DIR / "output"  # tablas completas (estado del pipeline, GeoParquet)
CLEAN_DIR = ETLS_DIR / "clean_data"  # datos finales para el mapa y el chatbot
PREVIEW_EVENTS = 10  # cuántos eventos se muestran por consola

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

config = load_config(CONFIG_FILE)

if DOWNLOAD:
    logging.info("Descargando datos FIRMS (la primera vez tarda unos minutos)...")
    files = download_all(config.download, RAW_DIR).available
    # Sin token (variable EARTHDATA_TOKEN o .secrets/earthdata_token) solo avisa y sigue.
    download_nrt(config.nrt, NRT_DIR, base_dir=ETLS_DIR)
else:
    files = sorted(RAW_DIR.glob("*.csv"))

files = [*files, *nrt_files(NRT_DIR)]  # archivo anual + NRT (se deduplican por detección)

if not files:
    print("Sin ficheros en data/raw: se usan las muestras (ejecuta con DOWNLOAD = True).")
    files = [MODIS_FILE, VIIRS_FILE]

logging.info("Leyendo %s ficheros FIRMS...", len(files))
frames = [read_firms_csv(path) for path in files]
logging.info("Procesando %s filas en total.", sum(len(frame) for frame in frames))

tables = run_frames(frames, config, OUTPUT_DIR, CLEAN_DIR)

print("\nRecuentos:", tables.run_parameters["counts"])

final_events = json.loads((CLEAN_DIR / "fire_events.json").read_text(encoding="utf-8"))
print(f"\nEventos finales: {len(final_events)} (los {PREVIEW_EVENTS} más recientes)")
for event in final_events[:PREVIEW_EVENTS]:
    print(f"- {event['summary']}")

print(f"\nTablas completas (GeoParquet) en {OUTPUT_DIR}")
print(f"Datos finales (mapa + chatbot) en {CLEAN_DIR}")
