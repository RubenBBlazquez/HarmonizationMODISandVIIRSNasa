# Backend

API (FastAPI) que sirve al mapa del frontend los datos **válidos** de PostGIS, más el pipeline
(`etls/`) y la base de datos (`databases/`).

## Arrancar todo

Desde la raíz del repo:

```bash
docker compose up --build
```

| Servicio | URL | Qué hace |
|---|---|---|
| `postgis` | `127.0.0.1:5432` | base de datos (esquema `fire`) |
| `api` | http://localhost:8000 (docs en `/docs`) | servidor FastAPI con recarga en `backend/api` |
| `frontend` | http://localhost:5173 | `npm install && npm run dev` (Vite + Leaflet) |

El frontend llama a `/api/...` y Vite lo reenvía a la API (`API_PROXY_TARGET`), así que no hay
problemas de CORS. En local, sin Docker: `uvicorn backend.main:app --reload` desde la raíz y
`npm install && npm run dev` en `frontend/`.

## Endpoints

Todos son de solo lectura y devuelven GeoJSON (`application/geo+json`).

| Endpoint | Devuelve |
|---|---|
| `GET /api/health` | `{"status": "ok"}` si llega a la base de datos |
| `GET /api/summary` | detecciones válidas / marcadas, eventos y rango de fechas |
| `GET /api/detections` | detecciones **válidas** (`is_valid = true`): punto (`geometry=point`) o área del píxel (`geometry=pixel`) |
| `GET /api/events` | eventos: centroide (`geometry=point`) o envolvente (`geometry=area`); con `bbox`, `match=intersects` (por defecto) o `match=inside` (ver abajo) |
| `GET /api/history` | incendios "similares" de una zona en años anteriores (ver abajo) |
| `GET /api/events/{event_id}` | un incendio: su envolvente y sus detecciones como píxeles (`limit`, 2000 por defecto); 404 si no existe |

Filtros comunes: `bbox=minLon,minLat,maxLon,maxLat`, `start=AAAA-MM-DD`, `end=AAAA-MM-DD`
(ambas incluidas), `source=MODIS|VIIRS` y `limit` (por defecto 5000, máximo 50000).
`/api/detections` admite además `frp_category=low|medium|high`.

`geometry=pixel` devuelve el rectángulo `scan` × `track` km centrado en el punto (sin el buffer de
400 m que el pipeline usa para agrupar), construido en el CRS métrico del pipeline (EPSG:25830) y
devuelto en EPSG:4326. Es una aproximación alineada a los ejes: no refleja la inclinación de la
órbita ni el solape *bow-tie*. Cada detección incluye `scan_km`, `track_km` y `pixel_area_km2`.
El mapa usa píxeles desde el zoom 10 y puntos por debajo (a menos zoom el píxel mide <1 px).

Cada incendio incluye `n_passes` (veces que un satélite lo vio), `max_pass_gap_hours` (mayor hueco
entre pasadas) y `biomass_burned_kg` (**estimación**: FRP integrado en el tiempo × 0,368 kg/MJ; ver
el README de `etls`). Valen `null` hasta que el pipeline los calcula. Con una sola pasada la
duración (`first_detection`–`last_detection`) no es medible.

La respuesta incluye `count` (devueltos), `total` (los que cumplen el filtro) y `truncated`. Las
detecciones marcadas con `skip_reason` (ruido de FRP) no se sirven. La huella
(`footprint_area_km2`) no es superficie quemada.

```bash
curl "http://localhost:8000/api/detections?bbox=-6.6,41.8,-5.9,42.15&start=2022-06-01&end=2022-06-30"
```

### Qué incendios cuentan dentro de una zona (`match`)

- `match=intersects` (por defecto): la **envolvente** del incendio toca el recuadro. Es lo que se
  ve en pantalla, pero puede incluir incendios que no tienen ninguna detección dentro.
- `match=inside` (necesita `bbox`): el incendio tiene **al menos una detección válida dentro del
  recuadro y de las fechas**. Es el criterio exacto para una zona seleccionada. Cada incendio
  añade `zone_detections` y `zone_frp_max_mw` (solo lo que cae en la zona), y con
  `geometry=point` el punto es el **centro de esas detecciones**, así que siempre queda dentro del
  recuadro. `n_detections` y `frp_max_mw` siguen siendo los del incendio completo.

### Incendios similares en años anteriores (`/api/history`)

Criterio de "similar": **misma zona** (`bbox`, obligatorio) y **mismas fechas del año**
(`start`–`end`, obligatorias, como mucho un año) en cada uno de los `years` años anteriores
(10 por defecto, hasta 30). Un incendio cuenta en un periodo si tiene detecciones válidas dentro
de la zona en esas fechas (`match=inside`).

Devuelve `periods` (el periodo elegido, con `is_reference: true`, y cada año anterior, también los
que no tienen incendios) con `events`, `detections` y `frp_max_mw` (siempre **dentro de la
zona**), y `events`: una FeatureCollection con los eventos de los años anteriores, con más
detecciones en la zona primero (`limit`, 200 por defecto), cada uno con su `period`.

```bash
curl "http://localhost:8000/api/history?bbox=-6.6,41.8,-5.9,42.15&start=2022-06-01&end=2022-06-30"
```

Una **detección** es un píxel en el que un satélite (MODIS o VIIRS) registró fuego activo en una
pasada; un incendio grande o largo suma muchas detecciones.

Desde los resultados de una zona se puede abrir **una pestaña con todos los incendios**
(`/fires.html`), cada uno en una tarjeta con su mapa (envolvente y píxeles de sus detecciones),
fechas, detecciones, FRP máximo, duración, huella, sensores y confianza. Los mapas se cargan al
hacerse visibles; se muestran como mucho los 100 mayores.

En el mapa: botón **Seleccionar zona** → arrastrar un recuadro → menú con *Incendios en esta
zona*, *Incendios similares en los últimos 10 años*, *Acercar a la zona* y *Quitar selección*.

## Configuración

- `DATABASE_URL` (la define `docker-compose.yml`; por defecto la base local de desarrollo).
- `CORS_ORIGINS` (por defecto `http://localhost:5173,http://127.0.0.1:5173`).

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests
```
