# ETLs: fusión de detecciones MODIS + VIIRS (NASA FIRMS)

Pipeline que ingiere CSV de FIRMS de MODIS y VIIRS, los fusiona espacial y temporalmente con
GeoPandas y genera eventos de incendio con una confianza fusionada. Las salidas (GeoParquet)
reproducen el esquema PostGIS de `backend/databases/postgis/init/01_schema.sql`.

> Fuera de alcance por ahora: ingesta por API de FIRMS, API, chatbot y carga a base de datos.

## Estructura

```
backend/etls/
├── pyproject.toml  .flake8  requirements.txt  (el .pre-commit-config.yaml está en la raíz)
├── config/default.yaml        # TODOS los parámetros del algoritmo
├── data/sample/               # 20 filas reales de MODIS (MCD14DL) y 20 de VIIRS NOAA-20 (VJ114IMGTDL)
├── scripts/                   # run_pipeline.py, validate_real_fire.py (+ _paths.py)
├── fire_etl/
│   ├── config.py       # dataclasses + carga YAML
│   ├── pipeline.py     # orquestación extract -> transform -> load + CLI
│   ├── stage_log.py    # log de inicio/fin (con tiempo) de cada etapa
│   ├── extract/
│   │   ├── main.py               # extract(): CSV FIRMS + estado previo
│   │   └── firms/
│   │       ├── archive.py        # descarga del archivo anual por país (MODIS y VIIRS)
│   │       ├── nrt.py            # descarga de los ficheros NRT del año en curso
│   │       └── reader.py         # lectura del CSV y detección del sensor
│   ├── transform/
│   │   ├── main.py               # transform(): ingesta -> eventos -> datos finales
│   │   ├── tables.py             # PipelineTables (resultado que consume load)
│   │   ├── clean_data.py         # construcción de los datos finales para el mapa y el chatbot
│   │   ├── sigpac_land_cover.py  # cobertura del suelo con SIGPAC
│   │   ├── firms/
│   │   │   ├── normalization.py  # CSV crudo -> esquema común, detection_id
│   │   │   ├── validation.py     # motivos de invalidez, filtros de fuente, descartes
│   │   │   └── standardization.py # tablas crudas -> detecciones estándar (dedup, FRP)
│   │   └── processing/           # algoritmo de fusión
│   │       ├── confidence.py         # normalización y matriz de confianza fusionada
│   │       ├── frp_classification.py # umbrales de FRP: ruido y clase baja/media/alta
│   │       ├── geometry.py           # puntos, AOI, CRS métrico, footprints
│   │       ├── graph.py              # sjoin + componentes conexas (scipy)
│   │       ├── fusion.py             # ventanas temporales -> observaciones
│   │       ├── events.py             # enlace entre ventanas -> fire_events, ids, fusiones
│   │       └── energy.py             # energía radiada y biomasa quemada por evento
│   └── load/
│       ├── main.py               # load(): GeoParquet + clean_data + PostGIS
│       ├── geoparquet.py         # GeoParquet (EPSG:4326) y lectura del estado previo
│       ├── clean_data.py         # escritura de los datos finales (GeoJSON + JSON)
│       ├── postgres.py           # carga de las tablas en PostGIS
│       └── sigpac.py             # carga de recintos SIGPAC en PostGIS (proceso aparte)
└── tests/
```

Los scripts de `scripts/` se ejecutan desde `backend/etls` con `python scripts/<script>.py`:
`scripts/_paths.py` añade `backend/etls` al `sys.path`, así que no hace falta instalar el
paquete (sí sus dependencias).

## Alcance: solo España

El MVP trabaja únicamente con detecciones en España. `config/default.yaml` define un área de
interés (`area_of_interest.polygon_wkt`) con la Península + Baleares, Canarias y Melilla, y usa
EPSG:25830 (ETRS89 / UTM 30N) como CRS métrico. Las detecciones fuera del área se descartan y se
cuentan como `outside_aoi` en `ingest_discards.csv`. Los rectángulos son generosos para no
perder detecciones españolas, así que incluyen franjas de Portugal, Francia y Marruecos; para un
recorte exacto, sustituye el WKT por el polígono de la frontera. En Canarias (UTM 28N) la escala
del CRS se distorsiona un 2-3 %, aceptable para el MVP.

Las muestras de `data/sample` son de toda Europa: con esta configuración solo se conservan las
filas españolas.

## Instalación y uso

```bash
cd backend/etls
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,viz]"     # o, sin instalar el paquete: pip install -r requirements.txt

# Pipeline sobre los ficheros de muestra
fire-etl --config config/default.yaml \
  --input data/sample/MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt \
  --input data/sample/J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt \
  --output-dir output

pytest --cov=fire_etl
pre-commit run --all-files     # desde cualquier carpeta del repo; solo revisa backend/etls
```

## Diseño

1. **Estandarización** (`transform/firms/`). El sensor se detecta por las columnas (`brightness` → MODIS,
   `bright_ti4` → VIIRS), no por la extensión: FIRMS publica los NRT como `.txt`. Esquema común:
   `detection_id, source, satellite, acq_datetime (UTC tz-aware), acq_date, lat, lon,
   area_width_km, area_height_km, frp, daynight, confidence_raw, confidence_norm, brightness, brightness_2, version`.
   `acq_time` admite `HH:MM` (NRT) y `HHMM` entero (API, p. ej. `304`). Las filas inválidas se
   descartan con su motivo (`ingest_discards.csv` y log).
2. **Confianza normalizada.** MODIS: `0-29 low`, `30-79 nominal`, `80-100 high` (umbrales en el
   YAML). VIIRS: `l/low`, `n/nominal`, `h/high`, sin distinguir mayúsculas. El valor original se
   conserva en `confidence_raw`.
3. **`detection_id`** = SHA-1 de `source|satellite|acq_datetime|lat|lon`, con coordenadas
   redondeadas a 5 decimales. Reingerir el mismo fichero no genera duplicados.
4. **Geometrías** (`geometry.py`). Punto en EPSG:4326 y cálculos en el CRS métrico (EPSG:25830 por
   defecto; `auto` = zona UTM del AOI). cada detección guarda `area_width_km` y
   `area_height_km` (km, genéricos: no dependen del sensor). Hoy se rellenan con el tamaño
   nominal del píxel (`geometry.pixel_size_km`: MODIS 1 km, VIIRS 0,375 km); no se usan `scan` ni
   `track` de FIRMS. El footprint es un rectángulo `area_width_km` (X) × `area_height_km` (Y)
   ampliado con el buffer (400 m) en esquinas rectas, así que mide `ancho·1000 + 2·buffer` m de
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

## Datos finales: `clean_data/`

Es lo que consumen el **mapa** y el **chatbot**: el resultado ya depurado, sin columnas del
algoritmo ni detecciones que no pertenezcan a un evento. Se genera en cada ejecución
(`output.clean_data_directory`, por defecto `clean_data/`).

| Fichero | Para | Contenido |
|---|---|---|
| `fire_events.geojson` | mapa | un punto (centroide) por evento, con sus propiedades |
| `fire_event_areas.geojson` | mapa | envolvente (polígono) de cada evento |
| `fire_detections.geojson` | mapa | detecciones de los eventos (puntos) |
| `fire_events.json` | chatbot | ficha plana por evento + `summary` en castellano + `frp_series` |
| `metadata.json` | ambos | fecha de generación, recuentos, parámetros, fuentes y avisos |

Propiedades de cada evento: `event_id`, `status` (`active`/`closed`), `first_detection`,
`last_detection`, `duration_hours`, `lat`, `lon`, `n_detections`, `sensors`, `frp_max_mw`,
`frp_category` (`low`/`medium`/`high`), `biomass_max_kg_s`, `confidence`, `footprint_area_km2`,
`land_cover`, `is_agricultural`, `is_forest` y `summary`. Fechas en UTC
(`2026-11-14T02:15:00Z`), coordenadas en EPSG:4326 (`[lon, lat]` en GeoJSON) y nulos como `null`.

**Qué se considera ruido y no llega aquí:**
- Detecciones por debajo del FRP mínimo de su instrumento y de día/noche:
  VIIRS: 3,7 MW de día y 0,7 MW de noche; MODIS: 5,7 y 3,7. **No se descartan, se marcan**: en
  `output/detections.parquet`
  llevan `is_valid = false` y `skip_reason = "below_frp_threshold"` (en las demás,
  `is_valid = true` y `skip_reason = null`). Se saltan en las observaciones y los eventos. Los
  umbrales están en `validation.frp_thresholds_mw` de la configuración YAML y se desactivan con
  `validation.apply_frp_thresholds: false`. El número de marcadas queda en
  `run_parameters.json` como `skipped_detections`.
  **Excepción en bosque:** si la detección cae en bosque (`is_forest`, SIGPAC), se mantiene válida
  con `frp_category = "low"` aunque su FRP sea bajo, porque el riesgo es alto y debe poder alertar
  (la confianza baja tampoco la descarta). En agricultura se sigue descartando. Se desactiva con
  `validation.keep_forest_below_frp: false` y necesita la cobertura del suelo activada.
- Observaciones `low` aisladas (un solo sensor y confianza baja).
- Detecciones fuera de España.

`footprint_area_km2` es la huella de los píxeles más el buffer, **no superficie quemada**, y
`biomass_max_kg_s` es una tasa (kg/s), no una masa total.

## Tablas completas (`output/`)

`output/` conserva todo el detalle del algoritmo (observaciones, relación evento-detección,
fusiones, parámetros) y el estado que usa el pipeline para mantener los `event_id` estables
entre ejecuciones. No es para consumir directamente: usa `clean_data/`.


| Fichero | Contenido | Geometrías (EPSG:4326) |
|---|---|---|
| `detections.parquet` | todas las detecciones + `is_valid`, `skip_reason`, `observation_id`, `is_representative`, `footprint_area_m2` | `geom_point`, `geom_footprint` |
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
- `date_daynight`: en España, una fecha UTC con `N` agrupa las pasadas de madrugada y de noche.
  El índice es absoluto (`2·día` para `N`, `2·día + 1` para `D`), así que las pasadas de la noche
  de `d` solo se enlazan con `N(d+1)` a través de `D(d)`.
- El estado previo se guarda en ficheros. Si se cambian los parámetros entre ejecuciones, los
  ids se conservan, pero el contenido de los eventos puede cambiar.

## Descarga de datos: archivo anual (2000 → último año completo) + NRT (año en curso)

Hay dos fuentes de NASA FIRMS, que se descargan con `scripts/download_firms.py` (o desde
`scripts/run_pipeline.py`):

| Fuente | Cubre | Token | Dónde queda |
|---|---|---|---|
| **Archivo anual** por país y año (ya recortado a España) | del 2000 al último año **completo** | no | `data/raw/*.csv` |
| **NRT** (casi tiempo real, toda Europa, se recorta a España al procesar) | **solo los últimos ~60 días** | **sí** | `data/raw/nrt/*.txt` |

### Archivo anual (sin token)

| Fuente (`download.sources`) | Años |
|---|---|
| `modis` (Aqua + Terra) | 2000 – 2025 |
| `viirs-snpp` | 2012 – 2025 |
| `viirs-jpss1` (NOAA-20) | 2018 – 2025 |

- Ruta: `<base_url>/<fuente>/<año>/<fuente>_<año>_Spain.csv`; todo editable en la sección
  `download` de `config/default.yaml`. Idempotente y atómico (`.part` + renombrado).
- **Nunca descarga el año en curso**: el archivo anual solo existe para años completos. Aunque se
  ponga `download.end_year` en el año actual, se recorta al último año completo y se avisa.
- Los ficheros anuales llaman `Aqua` / `Terra` a los satélites MODIS; `validation.satellite_aliases`
  los unifica con los `A` / `T` de los NRT para que el `detection_id` no duplique detecciones.

### NRT del año en curso (con token)

- Carpetas: `https://nrt3.modaps.eosdis.nasa.gov/archive/FIRMS/<fuente>/Europe/` para `modis-c6.1`,
  `suomi-npp-viirs-c2`, `noaa-20-viirs-c2` y `noaa-21-viirs-c2` (sección `nrt` del YAML). Un fichero
  por día (`..._NRT_AAAADDD.txt`, con el día del año).
- El **listado** es público, pero **cada fichero exige un token de Earthdata**. El token **nunca**
  va en el YAML, en el código ni en git (el repo tiene remoto en GitHub). Se lee, por orden, de:
  1. la variable de entorno `EARTHDATA_TOKEN` (el nombre se configura con `nrt.token_env`);
  2. el fichero local `backend/etls/.secrets/earthdata_token` (una línea; la carpeta `.secrets/`
     está en `.gitignore`; se crea con permisos solo para el usuario):

  ```bash
  mkdir -p .secrets && chmod 700 .secrets
  printf '%s\n' '<tu-token>' > .secrets/earthdata_token && chmod 600 .secrets/earthdata_token
  python scripts/download_firms.py
  ```

- Sin token se omite el NRT y se avisa. Si el servidor rechaza el token (caducado o no válido) se
  detiene sin seguir la redirección al login, así que el token no se envía a ningún otro servidor.
- Los ficheros de los últimos `nrt.refetch_days` (2) días se **vuelven a bajar siempre**, porque
  crecen durante el día; los más antiguos, una vez bajados, no. La primera descarga tarda unos
  minutos (≈ 230 ficheros); las siguientes, segundos.
- Los NRT son provisionales (NASA los sustituye luego por el procesamiento estándar).

### ⚠️ Hueco sin cubrir

El archivo anual acaba el 31-dic del último año completo y el NRT solo guarda ~60 días, así que
**lo que queda entre ambos no lo cubre ninguna fuente**. A 14-nov-2026 son los **257 días del
01-01-2026 al 14-09-2026**. `download_firms.py` lo calcula y lo avisa al terminar. Opciones: la API
de FIRMS (requiere una `MAP_KEY` gratuita; se pide por tramos de pocos días) o esperar a que
publiquen el fichero anual de 2026 (en enero de 2027).

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

## Qué cuenta como incendio, duración y biomasa

**Fuentes que no son incendios de vegetación** (se *marcan* con `is_valid = false` y `skip_reason`;
no entran en observaciones ni eventos):

| `skip_reason` | Origen |
|---|---|
| `below_frp_threshold` | FRP por debajo del mínimo del instrumento (`frp_classification.py`) |
| `static_land_source`, `active_volcano`, `offshore_detection` | campo `type` de FIRMS (2, 1 y 3; solo en los ficheros anuales). `type 0` = incendio de vegetación |
| `volcanic_eruption` | `validation.exclusion_zones`: zona y fechas configuradas |

`type` **no** separa todo: la erupción de La Palma (2021) está clasificada casi entera como `type 0`
(el 98,7 % de sus detecciones), por eso hay una zona de exclusión configurable
(`validation.exclusion_zones`, con `bbox`, fechas y motivo). Se incluye la de La Palma; añade otras
del mismo modo. Si varios motivos coinciden, manda el de fuente sobre el FRP bajo. Se desactiva con
`validation.apply_source_filters: false`.

**Duración** = tiempo entre la primera y la última detección de satélite de un incendio. Es un
mínimo: el fuego empieza antes de la primera pasada y sigue después de la última. Un incendio visto
en una sola pasada se muestra como "visto en 1 pasada" (no "0 h"). `n_passes` y
`max_pass_gap_hours` (mayor hueco entre pasadas) indican cuánta confianza tiene ese dato.

**Biomasa quemada (estimación)**, `biomass_burned_kg` (en `clean_data`, `biomass_burned_t`):
1. Las detecciones de un evento a menos de `biomass.pass_gap_minutes` (30) forman una **pasada**.
2. El FRP de la pasada es la suma de sus detecciones; si MODIS y VIIRS ven lo mismo en la misma
   pasada se toma el mayor, no la suma.
3. Cada pasada representa hasta `biomass.pass_window_hours` (6) horas, repartidas a la mitad con la
   pasada vecina. FRE (MJ) = Σ FRP(pasada) × horas × 3600.
4. Biomasa = FRE × `biomass.coefficient_kg_per_mj` (0,368).

Supone el FRP constante en cada ventana, ignora el ciclo diario del fuego y no incluye lo que los
satélites no ven (fuegos pequeños, nubes, humo): sirve para **comparar incendios**, no como medida
exacta. Con datos reales, la Sierra de la Culebra (2022) sale en ≈ 100.000 t. La tasa instantánea
`biomass_max_kg_s` (kg/s) es otra cosa: no es una masa.

## Carga de recintos SIGPAC en PostGIS

El FEGA publica un `.zip` por provincia (con un `.gpkg`, capa `recinto`) en
<https://sigpac-hubcloud.es/html/sdsigpac/descargas/exploradorArchivos.html?path=2026%2Frecintos%2F>.
`scripts/load_sigpac.py` lista los 50 zips de esa URL (`SIGPAC_BASE_URL`, en `load/sigpac.py`) y los
procesa **de uno en uno**: descarga, descomprime, lee con geopandas por bloques, reproyecta a
EPSG:4326, carga en `fire.sigpac_recintos` y borra zip y `.gpkg`. No necesita parámetros:

```bash
python scripts/load_sigpac.py                  # descarga y carga todas las provincias
python scripts/load_sigpac.py --only 20 28     # solo algunas (código INE)
python scripts/load_sigpac.py data/sigpac/     # zips locales en vez de descargar
```

Otras opciones: `--force`, `--keep-zips`, `--download-dir`, `--work-dir`. Los zips se bajan a
`data/sigpac/` (ignorado por git). Son ~34 GB en total, pero solo hay uno en disco a la vez.
- Cada provincia se sustituye en una transacción (si falla, queda como estaba).
- `fire.sigpac_loads` registra los zips cargados: se saltan al repetir (`--force` los recarga).
- Se guarda el uso original (`uso_sigpac`) y su normalización (`land_cover`), más superficie,
  altitud, pendiente, etc. Para enriquecer: `ST_Intersects`/`ST_Within` de un punto con `geom`.
- Cada zip descomprimido ocupa ~2x (Guipúzcoa: 118 MB -> 240 MB; las provincias grandes, varios GB);
  `--work-dir` elige dónde descomprimir.

## Fronteras de países en PostGIS

El globo del frontend (`frontend/index.html`) dibuja los países y filtra los incendios por país
(`/api/detections?country=ESP`) con las fronteras de Natural Earth 1:50m (dominio público),
guardadas en `fire.countries`. Se cargan una vez (la carga del pipeline no vacía esa tabla):

```bash
python scripts/load_countries.py              # descarga el zip a data/natural_earth/ y lo carga
python scripts/load_countries.py ruta/al.zip  # usa un zip local
```

La clave de cada país es `ADM0_A3` (`ESP`, `PRT`, `FRA`...). Recargar sustituye todos los países.

## Cobertura del suelo con SIGPAC

> **Si `land_cover`, `land_cover_confidence`, `sigpac_use`, `is_agricultural` e `is_forest` salen
> vacíos es porque este paso está desactivado** (`land_cover.sigpac_dir: null`) **o porque no hay
> recintos SIGPAC en disco.** No los descarga el pipeline: hay que bajarlos del FEGA, descomprimirlos
> y apuntar `sigpac_dir` a esa carpeta. Con el paso desactivado el pipeline lo avisa en el log
> (`Cobertura del suelo desactivada`) y las columnas ni se crean.

Paso opcional del pipeline principal: si `land_cover.sigpac_dir` apunta a una carpeta con los
recintos SIGPAC (`.shp` / `.gpkg` descargados del FEGA; no se descargan), cada detección recibe
`land_cover`, `land_cover_confidence`, `land_cover_source` (`sigpac`), `sigpac_use`,
`is_agricultural` e `is_forest`, y se escriben en `detections.parquet`. Con `sigpac_dir: null`
(por defecto) el paso no se ejecuta.

```yaml
land_cover:
  sigpac_dir: /ruta/sigpac
  sigpac_confidence: 0.95
  min_confidence: 0.0
```

```bash
pip install -r requirements.txt
python scripts/run_pipeline.py   # con land_cover.sigpac_dir definido en el YAML
```

1. Los puntos con lat/lon fuera de rango quedan vacíos y se cuentan.
2. Cruza cada capa **en su propio CRS** con `sjoin(predicate="within")`. El campo de uso se
   busca como `uso_sigpac` o `uso`, sin distinguir mayúsculas. Si un punto cae en varios
   recintos se usa el primero y se avisa.
3. El código se normaliza con `SIGPAC_TO_LAND_COVER` (editable); los desconocidos pasan a
   `other` y se registran en el log.
4. **Sin recinto, los campos quedan vacíos.** No hay fuente de respaldo en el MVP.
5. `is_agricultural` / `is_forest` = clase `crops` / `trees` con confianza ≥ `min_confidence`.
6. Las columnas que ya existan con esos nombres se renombran con `_prev` y se avisa.

Tests: `pytest tests/test_sigpac_land_cover.py tests/test_pipeline_land_cover.py`.

> Land cover: SIGPAC (FEGA / Ministerio de Agricultura, Pesca y Alimentación)
>
> ⚠️ Revisa las condiciones de reutilización de SIGPAC en el aviso legal del FEGA antes de
> redistribuir los resultados.

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
