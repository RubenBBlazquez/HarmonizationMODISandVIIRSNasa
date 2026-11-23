// Detecciones de satélite: sensor, confianza, tamaño del píxel y orden de pintado.
import type { Properties } from "../api/types.js";
import { formatNumber } from "../lib/format.js";
import { escapeHtml } from "../lib/html.js";

// Código de satélite de FIRMS -> nombre. MODIS vuela en Aqua (A) y Terra (T); VIIRS, en Suomi NPP
// (N), NOAA-20 (N20) y NOAA-21 (N21).
const SATELLITE_NAMES: Record<string, string> = {
  A: "Aqua",
  T: "Terra",
  N: "Suomi NPP",
  N20: "NOAA-20",
  N21: "NOAA-21",
};

const CONFIDENCE_LABELS: Record<string, string> = {
  low: "low",
  medium: "medium",
  high: "high",
  very_high: "very high",
  nominal: "nominal",
};

// Orden de menor a mayor confianza (nominal, de VIIRS, equivale a una confianza media).
const CONFIDENCE_RANK: Record<string, number> = {
  low: 0,
  medium: 1,
  nominal: 1,
  high: 2,
  very_high: 3,
};

const KM_PER_DEGREE_LAT = 111.32;

/** Copia de la colección con VIIRS y las de mayor confianza al final (se dibujan encima). */
export function sortByConfidence<T extends GeoJSON.FeatureCollection>(collection: T): T {
  const rank = (feature: GeoJSON.Feature): number =>
    CONFIDENCE_RANK[String(feature.properties?.confidence)] ?? -1;
  // A igual confianza, mayor FRP encima (el color ya depende de él).
  const frp = (feature: GeoJSON.Feature): number => Number(feature.properties?.frp_mw) || 0;
  // MODIS siempre debajo de VIIRS: su píxel es mayor y taparía a los de VIIRS.
  const viirs = (feature: GeoJSON.Feature): number =>
    feature.properties?.source === "MODIS" ? 0 : 1;
  const features = [...collection.features].sort(
    (a, b) => viirs(a) - viirs(b) || rank(a) - rank(b) || frp(a) - frp(b),
  );
  return { ...collection, features };
}

/**
 * Medio ancho y medio alto (grados de longitud y latitud) del píxel de una detección
 * (``area_width_km`` × ``area_height_km``) centrado en la latitud ``lat`` (aprox. local).
 */
export function pixelHalfSizeDegrees(lat: number, properties: GeoJSON.GeoJsonProperties): [number, number] {
  const halfHeight = Number(properties?.area_height_km ?? 0) / 2 / KM_PER_DEGREE_LAT;
  const halfWidth =
    Number(properties?.area_width_km ?? 0) / 2 / (KM_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180));
  return [halfWidth, halfHeight];
}

/** "MODIS · Aqua" / "VIIRS · NOAA-20": sensor y satélite de una detección. */
export function sensorLabel(properties: Properties): string {
  const satellite = SATELLITE_NAMES[String(properties.satellite)] ?? properties.satellite;
  return `${escapeHtml(properties.source)} · ${escapeHtml(satellite)}`;
}

/** "MODIS,VIIRS" -> "MODIS + VIIRS". */
export function sensorsLabel(sensors: string | number | null | undefined): string {
  return escapeHtml(String(sensors ?? "").split(",").join(" + "));
}

export function confidenceLabel(value: string | number | null | undefined): string {
  return CONFIDENCE_LABELS[String(value)] ?? escapeHtml(value);
}

/** "1 detection" / "12 detections". */
export function detectionsLabel(value: string | number | null | undefined): string {
  const count = Number(value ?? 0);
  return `${formatNumber(count)} ${count === 1 ? "detection" : "detections"}`;
}

/** "12 detections in the zone" si el incendio se buscó dentro de una zona; si no, el total. */
export function zoneDetectionsLabel(properties: Properties): string {
  return properties.zone_detections == null
    ? detectionsLabel(properties.n_detections)
    : `${detectionsLabel(properties.zone_detections)} in the zone`;
}
