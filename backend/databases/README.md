# Bases de datos

Contenedores Docker de las bases de datos del proyecto.

## PostGIS

PostgreSQL 16 + PostGIS 3.4 con el esquema `fire` (`postgis/init/01_schema.sql`). Sus tablas
corresponden a las salidas de `backend/etls/`: `detections`, `observations`, `fire_events`,
`event_detections` y `event_merges`.

```bash
cd backend/databases
cp .env.example .env        # define una contraseña propia; .env no se versiona
docker compose up -d
docker compose ps           # espera a que el estado sea healthy
psql "postgresql://fires:<password>@localhost:5432/fires" -c '\dt fire.*'
```

Los scripts de `postgis/init/` solo se ejecutan al crear el volumen por primera vez. Para
reaplicar el esquema desde cero: `docker compose down -v && docker compose up -d` (borra los
datos).

La carga de los GeoParquet a estas tablas aún no está implementada.
