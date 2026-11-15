#!/usr/bin/env sh
# Copia el esquema `fire` (tablas + datos) del PostGIS local de docker-compose a otra base PostGIS,
# normalmente la de Railway. Sustituye el contenido de destino: las tablas de `fire` se borran y
# se recrean. Los recintos SIGPAC (fire.sigpac_recintos) solo los usa el ETL y ocupan mucho: se
# copia la tabla vacía, sin sus datos (EXCLUDE_TABLE_DATA para cambiarlo).
#
# Uso:
#   TARGET_DATABASE_URL='postgresql://postgres:<pass>@<host>.proxy.rlwy.net:<puerto>/railway' \
#     sh backend/databases/sync_to_railway.sh
#
# TARGET_DATABASE_URL es la variable DATABASE_PUBLIC_URL del servicio PostGIS en Railway.
# Solo necesita Docker: pg_dump y pg_restore se ejecutan con la misma imagen postgis/postgis:16-3.4.
set -eu

: "${TARGET_DATABASE_URL:?Define TARGET_DATABASE_URL (DATABASE_PUBLIC_URL de Railway)}"
SOURCE_CONTAINER="${SOURCE_CONTAINER:-harmonization-postgis}"
SOURCE_DB="${SOURCE_DB:-fires}"
SOURCE_USER="${SOURCE_USER:-fires}"
PG_IMAGE="${PG_IMAGE:-postgis/postgis:16-3.4}"
EXCLUDE_TABLE_DATA="${EXCLUDE_TABLE_DATA:-fire.sigpac_recintos}"

echo "Creando la extensión postgis en destino (si falta)..."
docker run --rm "$PG_IMAGE" psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1 \
    -c 'CREATE EXTENSION IF NOT EXISTS postgis;'

echo "Volcando el esquema fire desde $SOURCE_CONTAINER y restaurándolo en destino..."
docker exec "$SOURCE_CONTAINER" \
    pg_dump -U "$SOURCE_USER" -d "$SOURCE_DB" --schema=fire --format=custom --no-owner --no-acl \
        --exclude-table-data="$EXCLUDE_TABLE_DATA" \
    | docker run --rm -i "$PG_IMAGE" \
        pg_restore --dbname="$TARGET_DATABASE_URL" --clean --if-exists --no-owner --no-acl \
        --exit-on-error

echo "Comprobando filas en destino..."
docker run --rm "$PG_IMAGE" psql "$TARGET_DATABASE_URL" -c "
    SELECT 'detections' AS tabla, count(*) FROM fire.detections
    UNION ALL SELECT 'fire_events', count(*) FROM fire.fire_events
    UNION ALL SELECT 'observations', count(*) FROM fire.observations
    UNION ALL SELECT 'sigpac_recintos', count(*) FROM fire.sigpac_recintos;"
