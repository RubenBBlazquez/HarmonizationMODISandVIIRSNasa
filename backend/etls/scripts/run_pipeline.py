"""
Runs the complete pipeline without command-line parameters.

It downloads (or reuses) the NASA FIRMS archive of Spain, MODIS and VIIRS since the year 2000,
and processes it. Edit the constants below (or replace ``frames`` with your DataFrames) and run
the script. It generates the final data in ``clean_data/`` (GeoJSON for the map, JSON for the
chatbot). If you run it in the PyCharm Python console, when it finishes you have
``tables.detections``, ``tables.observations``, ``tables.events``, etc. at hand as DataFrames.

    python scripts/run_pipeline.py

What ends up in ``clean_data/``
-------------------------------
Only the fire events and the detections that belong to some event (isolated low-confidence
observations and FRP noise do not get there). Dates in UTC (``YYYY-MM-DDTHH:MM:SSZ``) and
coordinates in EPSG:4326 (``lon, lat`` in GeoJSON). Files:

- ``fire_events.geojson``: one point (centroid) per event, for the map.
- ``fire_event_areas.geojson``: envelope (polygon) of each event. Properties: ``event_id``,
  ``status``, ``frp_category``, ``footprint_area_km2``.
- ``fire_detections.geojson``: the detections of the events (points).
- ``fire_events.json``: same as ``fire_events.geojson`` but flat and with ``frp_series``, for
  the chatbot.
- ``metadata.json``: generation date, counts, algorithm parameters, sources and warnings.

Event columns (``fire_events.geojson`` / ``fire_events.json``), sorted from newest to oldest:

- ``event_id``: event identifier.
- ``status``: ``active`` (still active) or ``closed``.
- ``first_detection`` / ``last_detection``: first and last detection of the event (UTC).
- ``duration_hours``: hours between the first and the last detection.
- ``lat`` / ``lon``: centroid of the event (5 decimals).
- ``n_detections``: number of detections (pixels) of the event.
- ``sensors``: sensors that saw it: ``MODIS``, ``VIIRS`` or ``MODIS,VIIRS``.
- ``frp_max_mw``: maximum FRP (fire radiative power), in MW.
- ``frp_category``: intensity (low, medium, high) of the most intense detection of the event.
- ``biomass_max_kg_s``: maximum biomass burning rate (FRP x coefficient in kg/MJ), in
  kg/s. It is a rate, not a total mass.
- ``biomass_burned_t``: estimated burned biomass, in tonnes.
- ``n_passes``: satellite passes that observed it.
- ``max_pass_gap_hours``: largest gap, in hours, between consecutive passes.
- ``confidence``: fused confidence of the observations (low, medium, high, very_high).
- ``footprint_area_km2``: area of the envelope (pixel footprint plus buffer), in km². It is NOT
  burned area.
- ``land_cover``: dominant land cover among its detections (normalized SIGPAC:
  crops, trees, shrub_and_scrub, grass, built, bare, water, other); empty if there is no SIGPAC.
- ``is_agricultural`` / ``is_forest``: ``true`` if ``land_cover`` is ``crops`` / ``trees``;
  empty if there is no land cover.
- ``summary``: sentence in Spanish that summarizes the event (map popups and chatbot).
- ``frp_series`` (only in ``fire_events.json``): time series per observation, with
  ``window_start``, ``n_detections``, ``frp_total``, ``frp_max`` and ``biomass_total_kg_s``.

Detection columns (``fire_detections.geojson``):

- ``detection_id``: identifier of the detection; ``event_id``: event it belongs to.
- ``acq_datetime``: acquisition instant (UTC).
- ``source`` / ``satellite``: product (MODIS or VIIRS) and satellite that detected it.
- ``lat`` / ``lon``: position of the pixel (5 decimals).
- ``frp_mw``: FRP of the detection, in MW; ``frp_category``: low, medium or high.
- ``biomass_kg_s``: biomass burning rate of the detection, in kg/s.
- ``confidence``: normalized confidence of the detection.
- ``daynight``: ``D`` (day) or ``N`` (night).
- ``land_cover``: SIGPAC land cover at that point (empty if there is no SIGPAC).
"""

import json
import logging

from _paths import ETLS_DIR, SAMPLE_DIR

from fire_etl.config import load_config
from fire_etl.extract.firms.archive import download_all
from fire_etl.extract.firms.nrt import download_nrt, nrt_files
from fire_etl.extract.firms.reader import read_firms_csv
from fire_etl.pipeline import run_frames

# --- Input data ------------------------------------------------------------------------------
DOWNLOAD = True  # True: downloads the Spain FIRMS archive (or reuses what is already downloaded)
RAW_DIR = ETLS_DIR / "data" / "raw"
NRT_DIR = RAW_DIR / "nrt"  # NRT of the current year (needs the EARTHDATA_TOKEN token)
# If there are no downloaded files (e.g. no Internet) these samples are used.
MODIS_FILE = SAMPLE_DIR / "MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt"
VIIRS_FILE = SAMPLE_DIR / "J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt"
CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
OUTPUT_DIR = ETLS_DIR / "output"  # complete tables (pipeline state, GeoParquet)
CLEAN_DIR = ETLS_DIR / "clean_data"  # final data for the map and the chatbot
PREVIEW_EVENTS = 10  # how many events are shown in the console

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

config = load_config(CONFIG_FILE)

if DOWNLOAD:
    logging.info("Downloading FIRMS data (the first time takes a few minutes)...")
    files = download_all(config.download, RAW_DIR).available
    # Without a token (EARTHDATA_TOKEN variable or .secrets/earthdata_token) it only warns and
    # continues.
    download_nrt(config.nrt, NRT_DIR, base_dir=ETLS_DIR)
else:
    files = sorted(RAW_DIR.glob("*.csv"))

files = [*files, *nrt_files(NRT_DIR)]  # annual archive + NRT (deduplicated by detection)

if not files:
    print("No files in data/raw: the samples are used (run with DOWNLOAD = True).")
    files = [MODIS_FILE, VIIRS_FILE]

logging.info("Reading %s FIRMS files...", len(files))
frames = [read_firms_csv(path) for path in files]
logging.info("Processing %s rows in total.", sum(len(frame) for frame in frames))

tables = run_frames(frames, config, OUTPUT_DIR, CLEAN_DIR)

print("\nCounts:", tables.run_parameters["counts"])

final_events = json.loads((CLEAN_DIR / "fire_events.json").read_text(encoding="utf-8"))
print(f"\nFinal events: {len(final_events)} (the {PREVIEW_EVENTS} most recent)")
for event in final_events[:PREVIEW_EVENTS]:
    print(f"- {event['summary']}")

print(f"\nComplete tables (GeoParquet) in {OUTPUT_DIR}")
print(f"Final data (map + chatbot) in {CLEAN_DIR}")
