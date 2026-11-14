"""
Validación visual del pipeline sobre un incendio real.

Ejecuta el pipeline sobre CSV FIRMS locales (MODIS y VIIRS del MISMO periodo), muestra un
resumen (detecciones, observaciones, eventos, confianza) y guarda un mapa PNG para revisión.

Mientras no exista la ingesta por API, los datos se descargan a mano desde el archivo de
FIRMS (https://firms.modaps.eosdis.nasa.gov/download/). Incendio de referencia sugerido:
Sierra de la Culebra (Zamora), 15-25 de junio de 2022 (``PRESET = "culebra-2022"``).

Por defecto usa los ficheros de muestra de ``backend/etls/data/sample`` (MODIS de septiembre
y VIIRS de noviembre de 2026: no se solapan en el tiempo, así que sirven para probar el flujo
pero no mostrarán fusión MODIS + VIIRS).

Edita las constantes de la sección "Parámetros" y ejecuta ``python scripts/validate_real_fire.py``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from _paths import ETLS_DIR, SAMPLE_DIR
from fire_etl.config import AreaOfInterestConfig, PipelineConfig, load_config
from fire_etl.ingest import read_firms_csv
from fire_etl.outputs import PipelineTables, to_storage_crs
from fire_etl.pipeline import run_frames

DEFAULT_MODIS = SAMPLE_DIR / "MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt"
DEFAULT_VIIRS = SAMPLE_DIR / "J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt"

# Áreas de interés de incendios conocidos (min_lon, min_lat, max_lon, max_lat).
PRESETS = {
    "culebra-2022": (-6.60, 41.80, -5.90, 42.15),
    "sierra-bermeja-2021": (-5.35, 36.45, -5.05, 36.65),
    "tenerife-2023": (-16.65, 28.25, -16.30, 28.50),
}

# Paleta categórica validada (slots 1 y 2) y tinta neutra para envolventes.
COLOR_VIIRS = "#2a78d6"
COLOR_MODIS = "#eb6834"
COLOR_HULL = "#3d3d3a"
COLOR_TEXT_MUTED = "#6b6a64"
TOP_EVENTS_LABELLED = 5


# --- Parámetros: edítalos aquí ------------------------------------------------------------
MODIS_FILE = DEFAULT_MODIS  # CSV de MODIS del incendio (mismo periodo que VIIRS)
VIIRS_FILE = DEFAULT_VIIRS  # CSV de VIIRS del incendio
PRESET: str | None = None  # "culebra-2022", "sierra-bermeja-2021", "tenerife-2023" o None
FOCUS_LARGEST = False  # True: el mapa se encuadra en el evento mayor
CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
OUTPUT_DIR = ETLS_DIR / "output" / "validation"


def build_config() -> PipelineConfig:
    config = load_config(CONFIG_FILE)
    config = replace(config, output=replace(config.output, use_previous_state=False))
    if PRESET is not None:
        config = replace(config, area_of_interest=AreaOfInterestConfig(bbox=PRESETS[PRESET]))

    return config


def print_summary(tables: PipelineTables) -> None:
    counts = tables.run_parameters["counts"]
    detections = tables.detections
    observations = tables.observations
    events = tables.events

    print("\n=== Resumen de validación ===")
    print(f"CRS métrico: {tables.run_parameters['metric_crs_resolved']}")
    print(f"Detecciones: {counts['detections']}  {detections['source'].value_counts().to_dict()}")
    print(f"Descartadas: {counts['discarded']}")
    print(f"Observaciones: {counts['observations']}")
    print(f"  por sensores: {observations['sensors'].value_counts().to_dict()}")
    print(f"  confianza fusionada: {observations['confidence_fused'].value_counts().to_dict()}")
    print(f"  low aisladas: {int(observations['is_isolated_low'].sum())}")
    print(f"Eventos: {counts['fire_events']}  (fusiones: {counts['event_merges']})")

    if not events.empty:
        top = events.sort_values("n_detections", ascending=False).head(TOP_EVENTS_LABELLED)
        columns = [
            "event_id",
            "first_detection",
            "last_detection",
            "status",
            "n_detections",
            "sensors",
            "frp_max",
            "confidence_fused",
        ]
        print("\nEventos principales:")
        print(top[columns].to_string(index=False))

    print(
        "\nNota: hull_area_m2 / footprint_area_m2 son la huella de los píxeles + buffer, "
        "NO superficie quemada."
    )


def plot_map(tables: PipelineTables, storage_crs: str, path: Path, focus_largest: bool) -> None:
    detections = to_storage_crs(tables.detections, storage_crs)
    events = to_storage_crs(tables.events, storage_crs)

    figure, axis = plt.subplots(figsize=(9, 8))
    for source, color in (("MODIS", COLOR_MODIS), ("VIIRS", COLOR_VIIRS)):
        subset = detections[detections["source"] == source]
        if not subset.empty:
            subset.plot(ax=axis, facecolor=color, edgecolor="white", linewidth=0.5, alpha=0.45)

    if not events.empty:
        events.plot(ax=axis, facecolor="none", edgecolor=COLOR_HULL, linewidth=2)
        top = events.sort_values("n_detections", ascending=False).head(TOP_EVENTS_LABELLED)
        for row in top.itertuples():
            axis.annotate(
                f"{row.event_id[:12]} · {row.n_detections} det.",
                xy=(row.geom_centroid.x, row.geom_centroid.y),
                xytext=(6, 6),
                textcoords="offset points",
                fontsize=8,
                color=COLOR_HULL,
            )

        if focus_largest:
            minx, miny, maxx, maxy = top.iloc[[0]].total_bounds
            margin = max(maxx - minx, maxy - miny) * 0.25 or 0.05
            axis.set_xlim(minx - margin, maxx + margin)
            axis.set_ylim(miny - margin, maxy + margin)

    legend = [
        Patch(facecolor=COLOR_MODIS, alpha=0.45, label="Footprint MODIS"),
        Patch(facecolor=COLOR_VIIRS, alpha=0.45, label="Footprint VIIRS"),
        Line2D([0], [0], color=COLOR_HULL, linewidth=2, label="Envolvente de evento"),
    ]
    axis.legend(handles=legend, loc="upper left", frameon=False, fontsize=9)
    axis.set_title("Detecciones FIRMS fusionadas en eventos", loc="left", fontsize=12)
    axis.set_xlabel("Longitud", color=COLOR_TEXT_MUTED)
    axis.set_ylabel("Latitud", color=COLOR_TEXT_MUTED)
    axis.grid(color="#e4e3dd", linewidth=0.5)
    axis.set_axisbelow(True)
    for spine in axis.spines.values():
        spine.set_visible(False)

    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = build_config()

    modis = read_firms_csv(MODIS_FILE)
    viirs = read_firms_csv(VIIRS_FILE)
    tables = run_frames([modis, viirs], config, OUTPUT_DIR)
    print_summary(tables)

    map_path = OUTPUT_DIR / "validation_map.png"
    plot_map(tables, config.geometry.storage_crs, map_path, FOCUS_LARGEST)
    print(f"\nMapa guardado en {map_path}")


if __name__ == "__main__":
    main()
