-- Esquema PostGIS para las salidas del pipeline backend/etls/src/fire_etl.
-- Las columnas y tipos se corresponden con los GeoParquet de backend/etls/output:
--   detections.parquet, observations.parquet, fire_events.parquet,
--   event_detections.parquet, event_merges.parquet.
-- Geometrías almacenadas en EPSG:4326; las áreas (*_area_m2) se calculan en el CRS métrico
-- configurado (EPSG:3035 por defecto). El área de footprints NO es superficie quemada.

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
    scan                double precision NOT NULL,      -- km
    track               double precision NOT NULL,      -- km
    frp                 double precision NOT NULL,      -- MW
    biomass_kg_s        double precision NOT NULL,      -- frp * coeficiente (kg/MJ): tasa en kg/s
    daynight            char(1)         NOT NULL CHECK (daynight IN ('D', 'N')),
    confidence_raw      text            NOT NULL,       -- valor original FIRMS
    confidence_norm     text            NOT NULL CHECK (confidence_norm IN ('low', 'nominal', 'high')),
    brightness          double precision,               -- MODIS brightness / VIIRS bright_ti4 (K)
    brightness_2        double precision,               -- MODIS bright_t31 / VIIRS bright_ti5 (K)
    version             text,
    observation_id      text,
    is_representative   boolean         NOT NULL DEFAULT false,
    footprint_area_m2   double precision NOT NULL,
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
    confidence_modis            text,
    confidence_viirs            text,
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

ALTER TABLE fire.detections
    ADD CONSTRAINT detections_observation_fk
    FOREIGN KEY (observation_id) REFERENCES fire.observations (observation_id)
    DEFERRABLE INITIALLY DEFERRED;

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
