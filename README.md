# Harmonization MODIS and VIIRS

Wildfire monitoring built on NASA satellite data. The project downloads active-fire detections
from **NASA FIRMS** (the **MODIS** and **VIIRS** instruments), harmonizes the two sources, merges
the detections into **fire events** with a stable identity, and shows them on an **interactive
globe**, covering both today's fires and the last 25 years of history.

- [1. Description](#1-description)
- [2. Goal](#2-goal)
- [3. Architecture](#3-architecture)
- [4. How to run it](#4-how-to-run-it)
- [5. Reading the data correctly](#5-reading-the-data-correctly)
- [6. Further documentation](#6-further-documentation)
- [7. Data sources and acknowledgements](#7-data-sources-and-acknowledgements)

---

## 1. Description

NASA publishes the thermal anomalies seen by its satellites for free through **FIRMS** (*Fire
Information for Resource Management System*). Every time a satellite passes over an area and sees
a pixel much hotter than its surroundings, it records a **detection**: a "hot pixel" with a
position, a timestamp, a pixel size and the **FRP** (*Fire Radiative Power*, in MW) it emits.

That raw feed is hard to use directly:

| Problem | Why it matters |
|---|---|
| **Two incompatible instruments** | MODIS (Aqua/Terra, ~1 km pixels, since 2000) and VIIRS (Suomi NPP, NOAA-20, NOAA-21, ~375 m pixels, since 2012) measure differently and express confidence differently (a 0–100 number vs. `low`/`nominal`/`high`). |
| **A detection is not a fire** | A large fire produces hundreds or thousands of detections across several days and satellite passes. Nobody groups them. |
| **Not everything hot is a wildfire** | Industry, volcanoes (La Palma 2021), offshore platforms and weak low-FRP signals all show up as detections. |
| **Two delivery channels that do not line up** | A yearly per-country archive (2000 → last full year) and a Near Real-Time (NRT) feed that only keeps the last ~60 days. |

This project solves those problems end to end. It contains:

- **`fire_etl`**, a Python data pipeline that downloads, cleans, fuses and groups the detections
  into fire events.
- A **PostGIS** database that stores the results with their geometry.
- A read-only **FastAPI** service that serves them as GeoJSON.
- A **web globe** (TypeScript + MapLibre) where users explore fires by country, date, land type
  and severity.

### What the user can do on the map

- Pick a **country** on a 3D globe and see its **active and closed fires** and the **detections**
  coloured by intensity. Detections are drawn as points when zoomed out and as the **actual
  satellite pixel** when zoomed in.
- Filter by **date range** (last week, month, year, all dates or a custom range), by **land type**
  (cropland, forest, shrubland, grassland, urban…, from SIGPAC) and by **severity**.
- Follow **recent activity** and **active fire alerts**, and jump to any fire.
- **Play back** a fire day by day or satellite pass by satellite pass.
- **Select a zone** by dragging a box, then:
  - list the fires with at least one detection inside it;
  - compare with the **same zone and the same dates in each of the previous 10 years**
    ("is this normal here at this time of year?"), with a chart stacked by severity and
    filters/sorting by year, severity, recency and size.
- Open a **fire cards** page (`fires.html`) with one card per fire: its own minimap (envelope and
  pixels), dates, detections, peak FRP, duration, footprint, sensors and confidence.
- Use it on desktop or **mobile**.

---

## 2. Goal

The repository name sums it up: **harmonize MODIS and VIIRS** so both sources speak the same
language, and build something useful on top of that:

1. **One common detection model** for both instruments and both delivery channels, with no
   duplicates when the archive and the NRT feed overlap.
2. **Separate real vegetation fires from noise**, without losing data: non-fire detections are
   *flagged* with a reason, never deleted, so they can be audited.
3. **Turn detections into fires**: group the pixels seen by both instruments into daily
   observations, chain them over consecutive days into events, and keep each event's identifier
   **stable** across pipeline runs.
4. **A confidence level you can trust**: a fused confidence where only cross-sensor agreement
   (MODIS *and* VIIRS) reaches `high` or `very_high`.
5. **Context for every fire**: what was burning (SIGPAC land cover), how much vegetation burned
   (biomass estimate) and how severe it was.
6. **Make it explorable**: a fast map for today's fires and a 25-year history to compare against.
7. **Reproducible and configurable**: same input, same output; every threshold lives in one YAML
   file, not in the code.

The current scope is an **MVP centred on Spain** (Peninsula, Balearic and Canary Islands, Ceuta and
Melilla), with the globe and country borders ready for other countries. A future **chatbot** over
the fires is planned: the pipeline already produces a flat per-fire JSON with a text summary for
it.

---

## 3. Architecture

### 3.1 Overview

```
                        ┌──────────────────────── local machine ───────────────────────┐
                        │                                                              │
 NASA FIRMS ───────────►│  fire_etl pipeline (Python)                                  │
  · yearly archive      │    extract ─► transform ─► load                              │
  · NRT (Earthdata)     │                              │                               │
 SIGPAC (FEGA) ────────►│                              ├─► GeoParquet  (output/)       │
 Natural Earth ────────►│                              ├─► clean data  (clean_data/)   │
                        │                              └─► PostGIS     (schema `fire`) │
                        └──────────────────────────────────────┬───────────────────────┘
                                                               │ read-only SQL
                                                    ┌──────────▼──────────┐
                                                    │  FastAPI  /api/*    │  GeoJSON, gzip,
                                                    │  (backend/api)      │  in-memory TTL cache
                                                    └──────────▲──────────┘
                                                               │ /api proxy (Vite in dev,
                                                               │ Caddy in production)
                                                    ┌──────────┴──────────┐
                                                    │  Web app (frontend) │  MapLibre globe +
                                                    │  index / fires.html │  Leaflet fire cards
                                                    └─────────────────────┘
```

The pipeline runs **offline** (locally) and writes to PostGIS. The API and the frontend only
**read**. Because the browser always calls `/api/...` on its own origin and a proxy forwards it to
the API, no CORS setup is needed in development or production.

### 3.2 Tech stack

| Layer | Technology |
|---|---|
| Pipeline | Python ≥ 3.11, pandas, GeoPandas, Shapely, SciPy (connected components), PyArrow / GeoParquet, PyYAML |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| API | FastAPI, Uvicorn, psycopg, GZip middleware, in-memory TTL cache |
| Frontend | TypeScript (no framework), Vite, MapLibre GL (globe, OpenFreeMap vector tiles), Leaflet (cards) |
| Tooling | pytest, Black / isort / flake8 (line length 99) via pre-commit |
| Infrastructure | Docker Compose (local), Railway (production: PostGIS + API + Caddy-served frontend) |

### 3.3 Data pipeline (`backend/etls/fire_etl`)

The pipeline follows an **extract → transform → load** layout:

```
fire_etl/
├── config.py            # dataclasses + YAML loading (config/default.yaml)
├── pipeline.py          # orchestration + `fire-etl` CLI
├── extract/firms/       # yearly archive download, NRT download, CSV reader + sensor detection
├── transform/
│   ├── firms/           # normalization, validation (flags), standardization (dedup, FRP)
│   ├── processing/      # confidence, FRP classes, geometry, graph, fusion, events, energy
│   ├── sigpac_land_cover.py · static_sources.py · clean_data.py
└── load/                # GeoParquet, clean data, PostGIS, SIGPAC, countries, static sources
```

What each stage does:

1. **Download.** The yearly FIRMS archive per country (no account needed) for MODIS, VIIRS S-NPP
   and VIIRS NOAA-20, plus daily NRT files for Europe (requires a free Earthdata token). The last
   two NRT days are always re-downloaded because they keep growing during the day.
2. **Standardize.** Each file is mapped to one common schema. The sensor is detected from the
   columns, not the file name. Satellite names are unified (`Aqua` → `A`). Each detection gets a
   deterministic `detection_id` (SHA-1 of source, satellite, time and rounded coordinates), so
   reprocessing never duplicates rows. Invalid rows are discarded with a reason.
3. **Area of interest.** Detections outside the configured area (Spain) are dropped.
4. **Flag noise.** Detections are kept but marked `is_valid = false` with a `skip_reason`:
   - `below_frp_threshold`: FRP under the per-instrument day/night minimum (forest pixels are kept
     so they can still raise an alert);
   - `static_land_source`, `active_volcano`, `offshore_detection`: the FIRMS `type` field;
   - `volcanic_eruption`: configurable exclusion zones (e.g. La Palma 2021);
   - `persistent_thermal_source`: industry cells found in the 2000–2025 history.

   Valid detections are classified as `low`, `medium` or `high` intensity.
5. **Fuse into observations.** Each pixel footprint is drawn in a metric CRS (EPSG:25830) with a
   400 m buffer. Footprints that touch within the same time window (by default the same UTC day)
   form one **observation** (spatial join + connected components).
6. **Fused confidence.** A confidence matrix combines the best MODIS and the best VIIRS level of
   each observation. Without cross-sensor confirmation an observation never goes above `medium`.
   Isolated single-sensor `low` observations do not create fires.
7. **Chain into events.** Observations in consecutive windows whose envelopes are less than
   1 km apart are linked into a **fire event**. An event becomes `closed` after 48 h without
   detections. Event IDs are **stable**: an existing event keeps its ID, and when two events merge
   the oldest ID wins and the merge is recorded.
8. **Enrich.** Optional **SIGPAC** land cover per detection (agricultural vs. forest), **biomass
   burned** (FRP integrated over satellite passes × 0.368 kg/MJ) and severity.
9. **Load.** The pipeline writes three outputs:
   - full algorithm tables as GeoParquet (`output/`), used for auditing and as state for stable IDs;
   - map/chatbot-ready data (`clean_data/`, GeoJSON + JSON with a per-fire summary);
   - the PostGIS tables. If the database load fails, the files remain as a fallback.

All parameters (thresholds, distances, time windows, URLs…) live in
[`backend/etls/config/default.yaml`](backend/etls/config/default.yaml).

### 3.4 Database (`backend/databases`)

PostGIS schema `fire`, created on first start from
[`postgis/init/01_schema.sql`](backend/databases/postgis/init/01_schema.sql):

| Table | Content |
|---|---|
| `detections` | every detection, with `is_valid`, `skip_reason`, point and pixel footprint |
| `observations` | detections fused per time window |
| `fire_events` | consolidated fires: dates, status, centroid, envelope, FRP, biomass, land cover |
| `event_detections` | event ↔ detection ↔ observation relation |
| `event_merges` | history of event merges |
| `countries` | Natural Earth 1:50m borders, used by the globe and the `country` filter |
| `sigpac_recintos`, `sigpac_loads` | SIGPAC parcels and land use (pipeline only) |
| `static_sources` | grid cells flagged as persistent thermal sources (industry) |

### 3.5 API (`backend/api`, `backend/main.py`)

A small read-only FastAPI app: `routes.py` (endpoints), `queries.py` (SQL), `db.py` (connection)
and `cache.py` (TTL cache). Caches are warmed in the background at start-up, and responses are
gzip-compressed (the map preloads tens of thousands of detections).

| Endpoint | Returns |
|---|---|
| `GET /api/health` | database connectivity check |
| `GET /api/summary` | counts and date range |
| `GET /api/countries` | countries with data |
| `GET /api/activity` | recent activity |
| `GET /api/detections` | valid detections, as points or pixels |
| `GET /api/events` | fire events, as centroids or envelopes (`match=intersects` or `inside`) |
| `GET /api/events/{event_id}` | one fire with its envelope and detection pixels |
| `GET /api/history` | fires in a zone on the same dates of previous years |
| `GET /api/yearly` | yearly aggregates |

Common filters: `bbox=minLon,minLat,maxLon,maxLat`, `start` / `end` (`YYYY-MM-DD`, inclusive),
`country` (ISO `ADM0_A3`, e.g. `ESP`), `source=MODIS|VIIRS` and `limit`. Interactive docs are at
`/docs`. See [`backend/README.md`](backend/README.md) for the full parameter reference.

### 3.6 Frontend (`frontend`)

Framework-less TypeScript bundled by Vite, with two pages:

| Page | Entry | What it is |
|---|---|---|
| `index.html` | `src/pages/globe.ts` | MapLibre globe with the selected country's fires |
| `fires.html` | `src/pages/fires.ts` | cards for the fires of a zone or of its history (Leaflet) |

The code is split into layers, and each layer only imports from the ones below it:

```
pages/       entry points: load styles, start and wire the modules
globe/       globe-specific code: map/, features/ (one feature per file), ui/, state, render
components/  shared UI: modal, date picker, fire cards, zone history
domain/      fire knowledge without DOM: colours, severity, land cover, labels, popups
api/         API contract: response types and one function per endpoint
lib/         generic helpers: dates, numbers, HTML escaping, DOM, storage, responsive
styles/      global CSS
```

See [`frontend/src/README.md`](frontend/src/README.md) for the data flow.

### 3.7 Deployment

| Environment | How |
|---|---|
| Local | `docker-compose.yml`: PostGIS, API (auto-reload on `backend/api`) and Vite dev server |
| Production | Railway, three services on the private network: PostGIS (template with a volume), API (`backend/Dockerfile.api`, API dependencies only) and frontend (`frontend/Dockerfile`, Vite build served by Caddy, which proxies `/api/*` to the API) |

The ETL is never deployed. Data is copied from the local PostGIS to Railway with
[`backend/databases/sync_to_railway.sh`](backend/databases/sync_to_railway.sh) (SIGPAC tables
excluded).

---

## 4. How to run it

### 4.1 Prerequisites

- **Docker** and **Docker Compose**.
- **Python 3.11+** to run the pipeline.
- **Node.js 22+**, only to run the frontend outside Docker.
- Optional: a free **[NASA Earthdata](https://urs.earthdata.nasa.gov/) token** for NRT data (the
  last ~60 days).

### 4.2 Start the database, API and web app

From the repository root:

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| Web app | http://localhost:5173 |
| API | http://localhost:8000 (interactive docs at http://localhost:8000/docs) |
| PostGIS | `127.0.0.1:5432`, database `fires`, user `fires` (local development credentials only) |

The `fire` schema is created automatically the first time the database volume is created. To reset
it from scratch (**this deletes all data**): `docker compose down -v && docker compose up --build`.

The map is empty until you load data (next step).

### 4.3 Set up the pipeline

```bash
cd backend/etls
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,viz]"
```

By default the pipeline connects to the local PostGIS started by Docker Compose.

### 4.4 Load data

Run these from `backend/etls` with the virtualenv active and the database running.

**Quick test with the bundled samples** (no download, a few rows):

```bash
fire-etl --config config/default.yaml \
  --input data/sample/MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt \
  --input data/sample/J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt \
  --output-dir output
```

**Full load:**

```bash
python scripts/load_countries.py   # country borders for the globe (run once)
python scripts/run_pipeline.py     # downloads FIRMS 2000 → today and runs the whole pipeline
```

The first run downloads the full yearly archive and about 230 NRT files, so it takes a while.
Later runs reuse what is already on disk.

**Optional steps:**

```bash
python scripts/load_static_sources.py   # detect industrial heat sources from the history
python scripts/load_sigpac.py           # SIGPAC parcels (~34 GB, one province at a time)
python scripts/validate_real_fire.py    # sanity check on a real fire (Sierra de la Culebra, 2022)
```

After a new load, refresh the map. Restart the `api` service if you want cached aggregates such as
`/api/yearly` to update immediately.

### 4.5 NRT token (optional)

The token is **never** stored in code, YAML or git. The pipeline reads it, in this order, from:

1. the `EARTHDATA_TOKEN` environment variable;
2. the local file `backend/etls/.secrets/earthdata_token` (git-ignored):

   ```bash
   cd backend/etls
   mkdir -p .secrets && chmod 700 .secrets
   printf '%s\n' '<your-token>' > .secrets/earthdata_token && chmod 600 .secrets/earthdata_token
   ```

Without a token the NRT step is skipped with a warning, and only the yearly archive is used.

### 4.6 Run without Docker (optional)

```bash
# API, from the repository root (needs a reachable PostGIS)
pip install -r backend/requirements-api.txt
DATABASE_URL=postgresql://fires:fires@localhost:5432/fires uvicorn backend.main:app --reload

# Frontend
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to http://localhost:8000
```

### 4.7 Configuration

| Variable | Used by | Default |
|---|---|---|
| `DATABASE_URL` | API | local development database |
| `CORS_ORIGINS` | API | `http://localhost:5173,http://127.0.0.1:5173` |
| `PORT` | API, frontend (Caddy) | `8000` / `8080` |
| `API_PROXY_TARGET` | Vite dev server | `http://localhost:8000` |
| `API_URL` | Caddy (production) | `http://localhost:8000` |
| `EARTHDATA_TOKEN` | pipeline (NRT download) | unset |

Algorithm parameters live in [`backend/etls/config/default.yaml`](backend/etls/config/default.yaml).

### 4.8 Tests and code quality

```bash
cd backend/etls && pytest --cov=fire_etl       # pipeline
python -m pytest backend/tests                 # API, from the repository root
pre-commit run --all-files                     # Black, isort, flake8 on backend/etls
cd frontend && npm run build                   # frontend type check
```

### 4.9 Deploy to Railway

1. Create a Railway project with three services: **PostGIS** (Railway's PostGIS template, with a
   volume), **api** (root directory `/backend`, config `/backend/railway.json`) and **frontend**
   (root directory `/frontend`, config `/frontend/railway.json`).
2. Set `DATABASE_URL=${{postgis.DATABASE_URL}}` and `PORT=8000` on `api`, and
   `API_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}` on `frontend`. Expose only the
   frontend publicly.
3. Copy the data from the local database:

   ```bash
   TARGET_DATABASE_URL='<postgis DATABASE_PUBLIC_URL>' sh backend/databases/sync_to_railway.sh
   ```

Full details are in [`backend/README.md`](backend/README.md#despliegue-en-railway).

---

## 5. Reading the data correctly

- The **footprint** is the area of the hot pixels plus a buffer, **not the burned area**. A MODIS
  pixel covers at least 1 km² even if the fire is much smaller.
- The **duration** is a lower bound: the time between the first and the last satellite pass that
  saw the fire. A fire seen once shows "seen in 1 pass".
- **Biomass burned** and **severity** are estimates for comparing fires, not official figures.
  For reference, the Sierra de la Culebra fire (2022) comes out at about 100,000 t.
- **NRT data is provisional**. NASA later replaces it with the standard processing.
- **Satellites miss things**: clouds, thick smoke and very small fires may go undetected.
- `very_high` confidence is a category defined by this project, not by FIRMS.
- **Coverage gap:** the yearly archive ends on 31 December of the last full year and NRT only keeps
  ~60 days, so nothing covers the period in between yet. The pipeline computes this gap and warns
  about it. It will be filled by the FIRMS API or by the next yearly file.

---

## 6. Further documentation

| Document | Content |
|---|---|
| [`PROYECTO.md`](PROYECTO.md) | Non-technical explanation of the project (Spanish) |
| [`backend/README.md`](backend/README.md) | API endpoints and parameters, configuration, Railway deployment |
| [`backend/etls/README.md`](backend/etls/README.md) | Algorithm details, confidence matrix, FIRMS download, SIGPAC, biomass, outputs |
| [`backend/databases/README.md`](backend/databases/README.md) | PostGIS container |
| [`frontend/src/README.md`](frontend/src/README.md) | Frontend layers and data flow |

---

## 7. Data sources and acknowledgements

- [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/): MODIS Collection 6.1 and VIIRS Collection 2
  active-fire detections (Aqua, Terra, Suomi NPP, NOAA-20, NOAA-21).
- [SIGPAC (FEGA)](https://sigpac-hubcloud.es/): Spanish agricultural parcels and land use.
- [Natural Earth](https://www.naturalearthdata.com/): 1:50m country borders (public domain).
- [OpenFreeMap](https://openfreemap.org/): vector base map for the globe.

*We acknowledge the use of data and imagery from LANCE FIRMS operated by NASA's Earth Science Data
and Information System (ESDIS) with funding provided by NASA Headquarters.*
