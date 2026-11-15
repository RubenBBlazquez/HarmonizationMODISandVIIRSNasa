"""
Ejecuta el pipeline completo sin parámetros por línea de comandos.

Descarga (o reutiliza) el archivo de NASA FIRMS de España, MODIS y VIIRS desde el año 2000, y lo
procesa. Edita las constantes de abajo (o sustituye ``frames`` por tus DataFrames) y lanza el
script. Genera los datos finales en ``clean_data/`` (GeoJSON para el mapa, JSON para el
chatbot). Si lo ejecutas en la consola de Python de PyCharm, al terminar tienes a mano
``tables.detections``, ``tables.observations``, ``tables.events``, etc. como DataFrames.

    python scripts/run_pipeline.py

Qué acaba en ``clean_data/``
----------------------------
Solo los eventos de incendio y las detecciones que pertenecen a algún evento (las observaciones
aisladas de confianza baja y el ruido por FRP no llegan). Fechas en UTC
(``YYYY-MM-DDTHH:MM:SSZ``) y coordenadas en EPSG:4326 (``lon, lat`` en GeoJSON). Ficheros:

- ``fire_events.geojson``: un punto (centroide) por evento, para el mapa.
- ``fire_event_areas.geojson``: envolvente (polígono) de cada evento. Propiedades: ``event_id``,
  ``status``, ``frp_category``, ``footprint_area_km2``.
- ``fire_detections.geojson``: las detecciones de los eventos (puntos).
- ``fire_events.json``: igual que ``fire_events.geojson`` pero plano y con ``frp_series``, para
  el chatbot.
- ``metadata.json``: fecha de generación, recuentos, parámetros del algoritmo, fuentes y avisos.

Columnas de los eventos (``fire_events.geojson`` / ``fire_events.json``), ordenados del más
reciente al más antiguo:

- ``event_id``: identificador del evento.
- ``status``: ``active`` (sigue activo) o ``closed`` (cerrado).
- ``first_detection`` / ``last_detection``: primera y última detección del evento (UTC).
- ``duration_hours``: horas entre la primera y la última detección.
- ``lat`` / ``lon``: centroide del evento (5 decimales).
- ``n_detections``: número de detecciones (píxeles) del evento.
- ``sensors``: sensores que lo han visto: ``MODIS``, ``VIIRS`` o ``MODIS,VIIRS``.
- ``frp_max_mw``: FRP (potencia radiativa del fuego) máximo, en MW.
- ``frp_category``: intensidad (low, medium, high) de la detección más intensa del evento.
- ``biomass_max_kg_s``: tasa máxima de combustión de biomasa (FRP x coeficiente en kg/MJ), en
  kg/s. Es una tasa, no una masa total.
- ``biomass_burned_t``: biomasa quemada estimada, en toneladas.
- ``n_passes``: pasadas de satélite que lo observaron.
- ``max_pass_gap_hours``: mayor hueco, en horas, entre pasadas consecutivas.
- ``confidence``: confianza fusionada de las observaciones (low, medium, high, very_high).
- ``footprint_area_km2``: área de la envolvente (huella de píxeles más buffer), en km². NO es
  superficie quemada.
- ``land_cover``: cobertura del suelo dominante entre sus detecciones (SIGPAC normalizado:
  crops, trees, shrub_and_scrub, grass, built, bare, water, other); vacío si no hay SIGPAC.
- ``is_agricultural`` / ``is_forest``: ``true`` si ``land_cover`` es ``crops`` / ``trees``;
  vacío si no hay cobertura.
- ``summary``: frase en castellano que resume el evento (popups del mapa y chatbot).
- ``frp_series`` (solo en ``fire_events.json``): serie temporal por observación, con
  ``window_start``, ``n_detections``, ``frp_total``, ``frp_max`` y ``biomass_total_kg_s``.

Columnas de las detecciones (``fire_detections.geojson``):

- ``detection_id``: identificador de la detección; ``event_id``: evento al que pertenece.
- ``acq_datetime``: instante de adquisición (UTC).
- ``source`` / ``satellite``: producto (MODIS o VIIRS) y satélite que la detectó.
- ``lat`` / ``lon``: posición del píxel (5 decimales).
- ``frp_mw``: FRP de la detección, en MW; ``frp_category``: low, medium o high.
- ``biomass_kg_s``: tasa de combustión de biomasa de la detección, en kg/s.
- ``confidence``: confianza normalizada de la detección.
- ``daynight``: ``D`` (día) o ``N`` (noche).
- ``land_cover``: cobertura del suelo SIGPAC en ese punto (vacío si no hay SIGPAC).
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
