"""
Ejecuta el pipeline completo sin parámetros por línea de comandos.

Edita las constantes de abajo (o sustituye los DataFrames ``modis`` y ``viirs`` por los tuyos) y
lanza el script. Si lo ejecutas en la consola de Python de PyCharm, al terminar tienes a mano
``tables.detections``, ``tables.observations``, ``tables.events``, etc. como DataFrames.

    python scripts/run_pipeline.py
"""

import logging
from pathlib import Path

from fire_etl.config import load_config
from fire_etl.ingest import read_firms_csv
from fire_etl.pipeline import run_frames

ETLS_DIR = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ETLS_DIR / "data" / "sample"

# --- Datos de entrada: cámbialos por tus ficheros, o define tú los DataFrames ---------------
MODIS_FILE = SAMPLE_DIR / "MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt"
VIIRS_FILE = SAMPLE_DIR / "J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt"
CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
OUTPUT_DIR = ETLS_DIR / "output"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

modis = read_firms_csv(MODIS_FILE)
viirs = read_firms_csv(VIIRS_FILE)
config = load_config(CONFIG_FILE)

tables = run_frames([modis, viirs], config, OUTPUT_DIR)

print("\nRecuentos:", tables.run_parameters["counts"])
print("\nEventos:")
print(
    tables.events[
        ["event_id", "first_detection", "last_detection", "status", "n_detections", "frp_max"]
        + ["biomass_max_kg_s", "confidence_fused"]
    ].to_string(index=False)
)
print(f"\nSalidas GeoParquet en {OUTPUT_DIR}")
