-- Esquema PostGIS para las salidas del pipeline backend/etls/fire_etl.
-- Las columnas y tipos se corresponden con los GeoParquet de backend/etls/output:
--   detections.parquet, observations.parquet, fire_events.parquet,
--   event_detections.parquet, event_merges.parquet.
-- Geometrías almacenadas en EPSG:4326; las áreas (*_area_m2) se calculan en el CRS métrico
-- configurado (EPSG:25830 por defecto). El área de footprints NO es superficie quemada.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS fire;

CREATE TABLE IF NOT EXISTS fire.detections (
    detection_id        char(40)        PRIMARY KEY,    -- SHA-1 determinista
    source              text            NOT NULL CHECK (source IN ('MODIS', 'VIIRS')),
    satellite           text            NOT NULL,       -- A, T, N, N20, N21...
    acq_datetime        timestamptz     NOT NULL,
    acq_date            date            NOT NULL,       -- fecha UTC
    lat                 double precision NOT NULL,
    lon                 double precision NOT NULL,
    area_width_km       double precision NOT NULL,      -- ancho del área de la detección, km
    area_height_km      double precision NOT NULL,      -- alto del área de la detección, km
    frp                 double precision NOT NULL,      -- MW
    biomass_kg_s        double precision NOT NULL,      -- frp * coeficiente (kg/MJ): tasa en kg/s
    frp_class           smallint        CHECK (frp_class IN (0, 1, 2, 3)),  -- 0 discarded, 1 low, 2 medium, 3 high
    frp_category       text            CHECK (frp_category IN ('low', 'medium', 'high', 'discarded', 'unknown')),
    is_valid            boolean         NOT NULL DEFAULT true,
    skip_reason         text,                           -- NULL si is_valid; si no, la razón
    daynight            char(1)         NOT NULL CHECK (daynight IN ('D', 'N')),
    confidence_raw      text            NOT NULL,       -- valor original FIRMS
    confidence_norm     text            NOT NULL CHECK (confidence_norm IN ('low', 'nominal', 'high')),
    brightness          double precision,               -- MODIS brightness / VIIRS bright_ti4 (K)
    brightness_2        double precision,               -- MODIS bright_t31 / VIIRS bright_ti5 (K)
    version             text,
    observation_id      text,
    is_representative   boolean         NOT NULL DEFAULT false,
    footprint_area_m2   double precision NOT NULL,
    land_cover              text,                       -- SIGPAC normalizado (opcional)
    land_cover_confidence   double precision,
    land_cover_source       text,
    sigpac_use              text,                       -- código de uso SIGPAC original
    is_agricultural         boolean,
    is_forest               boolean,
    geom_point          geometry(Point, 4326)   NOT NULL,
    geom_footprint      geometry(Polygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS detections_acq_datetime_idx ON fire.detections (acq_datetime);
CREATE INDEX IF NOT EXISTS detections_geom_point_gix ON fire.detections USING gist (geom_point);
CREATE INDEX IF NOT EXISTS detections_geom_footprint_gix
    ON fire.detections USING gist (geom_footprint);

CREATE TABLE IF NOT EXISTS fire.fire_events (
    event_id                    text            PRIMARY KEY,
    first_detection             timestamptz     NOT NULL,
    last_detection              timestamptz     NOT NULL,
    status                      text            NOT NULL CHECK (status IN ('active', 'closed')),
    n_observations              integer         NOT NULL,
    n_detections                integer         NOT NULL,
    n_modis                     integer         NOT NULL,
    n_viirs                     integer         NOT NULL,
    sensors                     text            NOT NULL,   -- 'MODIS', 'VIIRS' o 'MODIS,VIIRS'
    frp_max                     double precision NOT NULL,
    frp_total                   double precision NOT NULL,
    biomass_total_kg_s          double precision NOT NULL,
    biomass_max_kg_s            double precision NOT NULL,
    n_passes                    integer,                    -- pasadas de satélite que vieron el fuego
    max_pass_gap_hours          double precision,           -- mayor hueco entre pasadas (0 si solo 1)
    fre_mj                      double precision,           -- energía radiada estimada (FRP integrado)
    biomass_burned_kg           double precision,           -- fre_mj * coeficiente (kg/MJ): ESTIMACIÓN
    confidence_modis            text            NOT NULL DEFAULT 'not_observed',  -- low/nominal/high
    confidence_viirs            text            NOT NULL DEFAULT 'not_observed',
    confidence_fused            text            NOT NULL
        CHECK (confidence_fused IN ('low', 'medium', 'high', 'very_high')),
    hull_area_m2                double precision NOT NULL,
    frp_series                  jsonb           NOT NULL,   -- serie de FRP por observación
    merged_event_ids            jsonb           NOT NULL DEFAULT '[]'::jsonb,
    param_window_mode           text            NOT NULL,
    param_footprint_buffer_m    double precision NOT NULL,
    param_link_max_distance_m   double precision NOT NULL,
    param_link_max_window_gap   integer         NOT NULL,
    params                      jsonb           NOT NULL,   -- todos los parámetros del algoritmo
    geom_centroid               geometry(Point, 4326)   NOT NULL,
    geom_hull                   geometry(Polygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS fire_events_status_idx ON fire.fire_events (status);
CREATE INDEX IF NOT EXISTS fire_events_geom_hull_gix ON fire.fire_events USING gist (geom_hull);

CREATE TABLE IF NOT EXISTS fire.observations (
    observation_id              text            PRIMARY KEY,
    event_id                    text            REFERENCES fire.fire_events (event_id),
    window_key                  text            NOT NULL,
    window_index                bigint          NOT NULL,
    window_start                timestamptz     NOT NULL,
    window_end                  timestamptz     NOT NULL,
    first_detection             timestamptz     NOT NULL,
    last_detection              timestamptz     NOT NULL,
    sensors                     text            NOT NULL,
    n_detections                integer         NOT NULL,
    n_modis                     integer         NOT NULL,
    n_viirs                     integer         NOT NULL,
    frp_total                   double precision NOT NULL,
    frp_max                     double precision NOT NULL,
    biomass_total_kg_s          double precision NOT NULL,
    biomass_max_kg_s            double precision NOT NULL,
    confidence_modis            text,
    confidence_viirs            text,
    confidence_fused            text            NOT NULL,
    is_isolated_low             boolean         NOT NULL,
    representative_detection_id char(40)        NOT NULL REFERENCES fire.detections (detection_id),
    representative_source       text            NOT NULL,
    footprint_area_m2           double precision NOT NULL,
    hull_area_m2                double precision NOT NULL,
    geom_centroid               geometry(Point, 4326)   NOT NULL,
    geom_hull                   geometry(Polygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS observations_event_idx ON fire.observations (event_id);
CREATE INDEX IF NOT EXISTS observations_geom_hull_gix ON fire.observations USING gist (geom_hull);

-- Idempotente: este script lo reaplica el cargador del pipeline en cada ejecución.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'detections_observation_fk') THEN
        ALTER TABLE fire.detections
            ADD CONSTRAINT detections_observation_fk
            FOREIGN KEY (observation_id) REFERENCES fire.observations (observation_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS fire.event_detections (
    event_id        text        NOT NULL REFERENCES fire.fire_events (event_id),
    detection_id    char(40)    NOT NULL REFERENCES fire.detections (detection_id),
    observation_id  text        NOT NULL REFERENCES fire.observations (observation_id),
    PRIMARY KEY (event_id, detection_id)
);

CREATE INDEX IF NOT EXISTS event_detections_detection_idx
    ON fire.event_detections (detection_id);

CREATE TABLE IF NOT EXISTS fire.event_merges (
    merged_event_id text        NOT NULL,
    kept_event_id   text        NOT NULL REFERENCES fire.fire_events (event_id),
    merged_at       timestamptz NOT NULL,
    PRIMARY KEY (merged_event_id, kept_event_id)
);

-- Migraciones idempotentes: columnas añadidas después de la primera versión del esquema.
-- (Las bases creadas con un esquema antiguo las reciben al reaplicar este script.)
-- Migración de nombres en castellano a inglés (bases creadas con el esquema anterior).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'fire'
               AND table_name = 'detections' AND column_name = 'frp_clase') THEN
        ALTER TABLE fire.detections RENAME COLUMN frp_clase TO frp_class;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'fire'
               AND table_name = 'detections' AND column_name = 'frp_categoria') THEN
        ALTER TABLE fire.detections RENAME COLUMN frp_categoria TO frp_category;
        ALTER TABLE fire.detections DROP CONSTRAINT IF EXISTS detections_frp_categoria_check;
        UPDATE fire.detections SET frp_category = CASE frp_category
            WHEN 'baja' THEN 'low' WHEN 'media' THEN 'medium' WHEN 'alta' THEN 'high'
            WHEN 'descartado' THEN 'discarded' ELSE frp_category END;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'fire'
               AND table_name = 'detections' AND column_name = 'sigpac_uso') THEN
        ALTER TABLE fire.detections RENAME COLUMN sigpac_uso TO sigpac_use;
    END IF;
END $$;

ALTER TABLE fire.detections
    ADD COLUMN IF NOT EXISTS biomass_kg_s double precision,
    ADD COLUMN IF NOT EXISTS frp_class smallint,
    ADD COLUMN IF NOT EXISTS frp_category text,
    ADD COLUMN IF NOT EXISTS is_valid boolean NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS skip_reason text,
    ADD COLUMN IF NOT EXISTS land_cover text,
    ADD COLUMN IF NOT EXISTS land_cover_confidence double precision,
    ADD COLUMN IF NOT EXISTS land_cover_source text,
    ADD COLUMN IF NOT EXISTS sigpac_use text,
    ADD COLUMN IF NOT EXISTS is_agricultural boolean,
    ADD COLUMN IF NOT EXISTS is_forest boolean;

ALTER TABLE fire.detections
    DROP CONSTRAINT IF EXISTS detections_frp_class_check,
    ADD CONSTRAINT detections_frp_class_check CHECK (frp_class IN (0, 1, 2, 3)),
    DROP CONSTRAINT IF EXISTS detections_frp_category_check,
    ADD CONSTRAINT detections_frp_category_check
        CHECK (frp_category IN ('low', 'medium', 'high', 'discarded', 'unknown'));

UPDATE fire.fire_events SET confidence_modis = 'not_observed' WHERE confidence_modis IS NULL;
UPDATE fire.fire_events SET confidence_viirs = 'not_observed' WHERE confidence_viirs IS NULL;

ALTER TABLE fire.observations
    ADD COLUMN IF NOT EXISTS biomass_total_kg_s double precision,
    ADD COLUMN IF NOT EXISTS biomass_max_kg_s double precision;

ALTER TABLE fire.fire_events
    ADD COLUMN IF NOT EXISTS biomass_total_kg_s double precision,
    ADD COLUMN IF NOT EXISTS biomass_max_kg_s double precision,
    ADD COLUMN IF NOT EXISTS n_passes integer,
    ADD COLUMN IF NOT EXISTS max_pass_gap_hours double precision,
    ADD COLUMN IF NOT EXISTS fre_mj double precision,
    ADD COLUMN IF NOT EXISTS biomass_burned_kg double precision;

-- Recintos SIGPAC (FEGA), cargados provincia a provincia con scripts/load_sigpac.py para
-- enriquecer las detecciones con la cobertura del suelo. Geometrías en EPSG:4326 (el GeoPackage
-- original está en ETRS89, EPSG:4258; la diferencia con WGS84 es inferior al metro).
CREATE TABLE IF NOT EXISTS fire.sigpac_recintos (
    provincia           integer         NOT NULL,
    municipio           smallint        NOT NULL,
    agregado            smallint        NOT NULL,
    zona                integer         NOT NULL,
    poligono            smallint        NOT NULL,
    parcela             integer         NOT NULL,
    recinto             integer         NOT NULL,
    dn_surface          double precision,           -- superficie del recinto (m2)
    pendiente_media     smallint,                   -- tal cual viene en SIGPAC
    altitud             smallint,                   -- m
    csp                 double precision,
    coef_regadio        smallint,
    uso_sigpac          text,                       -- código de uso original (TA, OV, FO...)
    land_cover          text,                       -- uso normalizado (ver sigpac_land_cover.py)
    incidencias         text,
    region              integer,
    geom                geometry(Geometry, 4326) NOT NULL,
    PRIMARY KEY (provincia, municipio, agregado, zona, poligono, parcela, recinto)
);

CREATE INDEX IF NOT EXISTS sigpac_recintos_geom_gix ON fire.sigpac_recintos USING gist (geom);
CREATE INDEX IF NOT EXISTS sigpac_recintos_uso_idx ON fire.sigpac_recintos (uso_sigpac);

-- Un registro por zip cargado: permite reanudar y no repetir descargas ya procesadas.
CREATE TABLE IF NOT EXISTS fire.sigpac_loads (
    source_file text        PRIMARY KEY,            -- nombre del zip
    provincia   integer     NOT NULL,
    n_recintos  integer     NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now()
);

-- Fronteras de países (Natural Earth 1:50m, dominio público), cargadas con
-- scripts/load_countries.py. Las usa la API para filtrar por país (?country=ESP) y el globo del
-- frontend para dibujarlos. No las vacía la carga del pipeline (no está en su TRUNCATE).
CREATE TABLE IF NOT EXISTS fire.countries (
    code        text        PRIMARY KEY,            -- ADM0_A3 de Natural Earth (ESP, PRT, FRA...)
    name        text        NOT NULL,               -- nombre en inglés
    name_es     text,                               -- nombre en castellano
    continent   text,
    geom        geometry(MultiPolygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS countries_geom_gix ON fire.countries USING gist (geom);
