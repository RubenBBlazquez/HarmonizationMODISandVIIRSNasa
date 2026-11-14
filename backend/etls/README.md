# ETLs: fusión de detecciones MODIS + VIIRS (NASA FIRMS)

Pipeline que ingiere CSV de FIRMS de MODIS y VIIRS, los fusiona espacial y temporalmente con
GeoPandas y genera eventos de incendio con una confianza fusionada. Las salidas (GeoParquet)
reproducen el esquema PostGIS de `backend/databases/postgis/init/01_schema.sql`.

> Fuera de alcance por ahora: ingesta por API de FIRMS, API, chatbot y carga a base de datos.

## Estructura

```
backend/etls/
├── config/default.yaml        # TODOS los parámetros del algoritmo
├── data/sample/               # 20 filas reales de MODIS (MCD14DL) y 20 de VIIRS NOAA-20 (VJ114IMGTDL)
├── scripts/validate_real_fire.py
└── src/
    ├── pyproject.toml  .flake8  .pre-commit-config.yaml
    ├── fire_etl/
    │   ├── config.py       # dataclasses + carga YAML
    │   ├── ingest.py       # CSV FIRMS -> esquema común, validación, detection_id
    │   ├── confidence.py   # normalización y matriz de confianza fusionada (función pura)
    │   ├── geometry.py     # puntos, AOI, CRS métrico, footprints
    │   ├── graph.py        # sjoin + componentes conexas (scipy)
    │   ├── fusion.py       # ventanas temporales -> observaciones
    │   ├── events.py       # enlace entre ventanas -> fire_events, ids estables, fusiones
    │   ├── outputs.py      # GeoParquet (EPSG:4326) y lectura del estado previo
    │   └── pipeline.py     # orquestación + CLI
    └── tests/
```

## Instalación y uso

```bash
cd backend/etls/src
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,viz]"

# Pipeline sobre los ficheros de muestra
fire-etl --config ../config/default.yaml \
  --input ../data/sample/MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt \
  --input ../data/sample/J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt \
  --output-dir ../output

pytest --cov=fire_etl
pre-commit run --all-files --config .pre-commit-config.yaml
```

## Diseño

1. **Ingesta** (`ingest.py`). El sensor se detecta por las columnas (`brightness` → MODIS,
   `bright_ti4` → VIIRS), no por la extensión: FIRMS publica los NRT como `.txt`. Esquema común:
   `detection_id, source, satellite, acq_datetime (UTC tz-aware), acq_date, lat, lon, scan,
   track, frp, daynight, confidence_raw, confidence_norm, brightness, brightness_2, version`.
   `acq_time` admite `HH:MM` (NRT) y `HHMM` entero (API, p. ej. `304`). Las filas inválidas se
   descartan con su motivo (`ingest_discards.csv` y log).
2. **Confianza normalizada.** MODIS: `0-29 low`, `30-79 nominal`, `80-100 high` (umbrales en el
   YAML). VIIRS: `l/low`, `n/nominal`, `h/high`, sin distinguir mayúsculas. El valor original se
   conserva en `confidence_raw`.
3. **`detection_id`** = SHA-1 de `source|satellite|acq_datetime|lat|lon`, con coordenadas
   redondeadas a 5 decimales. Reingerir el mismo fichero no genera duplicados.
4. **Geometrías** (`geometry.py`). Punto en EPSG:4326 y cálculos en el CRS métrico (EPSG:3035 por
   defecto; `auto` = zona UTM del AOI). El footprint es un rectángulo `scan` (X) × `track` (Y) km
   ampliado con el buffer (400 m) en esquinas rectas, así que mide `scan·1000 + 2·buffer` m de
   ancho.
5. **Observaciones** (`fusion.py`). Las ventanas pueden ser `day_utc`, `hours_12` o
   `date_daynight`. Un único `sjoin(predicate="intersects")` del conjunto consigo mismo, del que se
   descartan los pares de ventanas distintas, y después componentes conexas. Para cada
   observación se calculan: sensores, número de detecciones, FRP total y máximo, centroide (media
   de los puntos), envolvente convexa, área de la unión de footprints y confianza por sensor (el
   máximo de sus detecciones). La detección representativa se elige después de agrupar:
   VIIRS > MODIS, luego mayor confianza y luego mayor FRP. Todas las detecciones MODIS siguen
   asociadas a la observación.
6. **Eventos** (`events.py`). Se enlazan las observaciones de ventanas consecutivas (diferencia
   de índice de 1 a `max_window_gap`) cuyas envolventes estén a menos de `max_distance_m`
   (`sjoin` con `dwithin`). Cada evento guarda primera y última detección, estado
   (`active`/`closed` según `inactive_after_hours` respecto a `reference_time`), centroide,
   envolvente, FRP máximo, `frp_series` (JSON por observación) y los parámetros usados
   (`param_*` + `params`).
7. **`event_id` estable.** Cada ejecución lee el estado previo del directorio de salida,
   reprocesa todas las detecciones y reutiliza los ids:
   - un evento que contiene detecciones de un evento previo conserva su id;
   - si una componente reúne varios eventos previos, se queda el id del más antiguo y la fusión
     se registra en `event_merges` y en `merged_event_ids`;
   - un evento nuevo recibe `evt_<hash(detección más antigua)>`, que es determinista.

### Matriz de confianza fusionada

Son categorías propias del algoritmo, **no** las oficiales de FIRMS. Para cada observación se
toma el nivel máximo de cada sensor.

| MODIS \ VIIRS | —  | low | nominal | high |
|---|---|---|---|---|
| **—**       | —  | low (aislada) | medium | medium¹ |
| **low**     | low (aislada) | low | medium | medium |
| **nominal** | medium | medium | high | high |
| **high**    | medium¹ | medium | high | very_high |

¹ `confidence.single_sensor_high_level` (por defecto `medium`): sin corroboración entre sensores
una observación nunca pasa de `medium`.

Las observaciones low aisladas (un solo sensor y `low`) se marcan con `is_isolated_low`. Con
`discard_isolated_low: true` no se enlazan a eventos, aunque sus detecciones se conservan.
Dos satélites del mismo sensor (Aqua + Terra o S-NPP + NOAA-20) no cuentan como corroboración.

## Biomasa

Cada detección lleva `biomass_kg_s = frp × 0,368` (coeficiente `biomass.coefficient_kg_per_mj`
en el YAML). Como el FRP está en MW (= MJ/s), el resultado es una **tasa de combustión de
biomasa en kg/s**, no una masa total: para obtener kg hay que integrar en el tiempo, p. ej.
multiplicando por la duración efectiva entre pasadas. Las observaciones y los eventos
incluyen `biomass_total_kg_s` (suma) y `biomass_max_kg_s` (máximo), y cada entrada de
`frp_series` añade `biomass_total_kg_s`. El coeficiente usado queda en `params`.

## Salidas (`output/`)

| Fichero | Contenido | Geometrías (EPSG:4326) |
|---|---|---|
| `detections.parquet` | detecciones + `observation_id`, `is_representative`, `footprint_area_m2` | `geom_point`, `geom_footprint` |
| `observations.parquet` | una fila por observación + `event_id` | `geom_centroid`, `geom_hull` |
| `fire_events.parquet` | eventos consolidados | `geom_centroid`, `geom_hull` |
| `event_detections.parquet` | relación evento ↔ detección ↔ observación | — |
| `event_merges.parquet` | histórico de fusiones de eventos | — |
| `ingest_discards.csv`, `run_parameters.json` | descartes y parámetros/recuentos de la ejecución | — |

Se usa GeoParquet porque GeoPackage solo admite una geometría por capa.

> ⚠️ **El área de los footprints (`footprint_area_m2`, `hull_area_m2`) NO es superficie
> quemada.** Es la huella de los píxeles con anomalía térmica más el buffer. Las áreas se
> calculan en el CRS métrico, y las geometrías se guardan en EPSG:4326.

## Limitaciones conocidas

- El footprint está alineado a los ejes. Ignora la inclinación de la órbita, el efecto *bow-tie*
  y la PSF del sensor.
- `date_daynight`: en Europa, una fecha UTC con `N` agrupa las pasadas de madrugada y de noche.
  El índice es absoluto (`2·día` para `N`, `2·día + 1` para `D`), así que las pasadas de la noche
  de `d` solo se enlazan con `N(d+1)` a través de `D(d)`.
- El estado previo se guarda en ficheros. Si se cambian los parámetros entre ejecuciones, los
  ids se conservan, pero el contenido de los eventos puede cambiar.

## Uso rápido (sin parámetros)

Los scripts de `scripts/` no usan argumentos de línea de comandos: se editan las constantes del
principio y se ejecutan.

```bash
cd backend/etls
python scripts/run_pipeline.py          # pipeline sobre las muestras; imprime recuentos y eventos
python scripts/validate_real_fire.py    # lo mismo + resumen de validación + mapa PNG
```

Con tus propios DataFrames (notebook o consola), basta una llamada:

```python
from fire_etl.pipeline import run_frames

tables = run_frames([df_modis, df_viirs])   # DataFrames FIRMS crudos, en cualquier orden
tables.events, tables.observations, tables.detections
```

## Validación con un incendio real

Edita `MODIS_FILE`, `VIIRS_FILE` y `PRESET` al principio de `scripts/validate_real_fire.py`
(p. ej. `PRESET = "culebra-2022"`, `FOCUS_LARGEST = True`) y ejecútalo:

```bash
python scripts/validate_real_fire.py
```

Los CSV de MODIS y VIIRS **del mismo periodo** se bajan a mano del archivo de FIRMS; el script
no descarga nada. La referencia sugerida es Sierra de la Culebra (Zamora), del 15 al 25 de junio
de 2022. Imprime el recuento de detecciones, observaciones y eventos y la distribución de la
confianza, y guarda `output/validation/validation_map.png`. Con las muestras por defecto (MODIS
de septiembre y VIIRS de noviembre de 2026, sin solape temporal) no se producen fusiones
MODIS + VIIRS.
