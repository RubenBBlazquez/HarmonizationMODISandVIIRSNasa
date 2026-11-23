// Utilidades comunes del mapa principal (index.ts) y de la pestaña de incendios (fires.ts).
import type L from "leaflet";

// El navegador llama a /api/... y Vite lo reenvía a la API (ver vite.config.ts).
export const API_BASE = "/api";
export const HISTORY_YEARS = 10;

export const FRP_COLORS: Record<string, string> = {
  low: "#e0b43c",
  medium: "#e8833a",
  high: "#c0392b",
};
export const NO_CATEGORY_COLOR = "#8a8f98";
export const EVENT_COLORS: Record<string, string> = { active: "#c0392b", closed: "#5b6b62" };
// Color de cada año (contornos al comparar años en el globo y etiquetas del histórico).
const YEAR_COLORS = [
  "#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b",
  "#17becf", "#e377c2", "#bcbd22", "#7f7f7f", "#d62728",
];
export const yearColor = (year: number): string => YEAR_COLORS[year % YEAR_COLORS.length];
export const PIXEL_BORDER_COLOR = "#4a4f55";
export const PIXEL_FILL_OPACITY = 0.45;
// VIIRS (375 m) es más preciso que MODIS (1 km): sus píxeles destacan y los de MODIS quedan de fondo.
export const MODIS_PIXEL_FILL_OPACITY = 0.1;
export const MODIS_PIXEL_BORDER_OPACITY = 0.35;

const KM_PER_DEGREE_LAT = 111.32;

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

/** Estilo de un píxel: VIIRS opaco y con borde marcado; MODIS muy transparente y de fondo. */
export function pixelStyle(feature: GeoJSON.Feature | undefined): L.PathOptions {
  const isModis = feature?.properties?.source === "MODIS";
  return {
    color: PIXEL_BORDER_COLOR,
    weight: isModis ? 1 : 1.5,
    opacity: isModis ? MODIS_PIXEL_BORDER_OPACITY : 1,
    fillColor: FRP_COLORS[String(feature?.properties?.frp_category)] ?? NO_CATEGORY_COLOR,
    fillOpacity: isModis ? MODIS_PIXEL_FILL_OPACITY : PIXEL_FILL_OPACITY,
  };
}

/** Cómo se estima la biomasa quemada, para explicarlo en la interfaz. */
export const BIOMASS_HELP =
  "Burned biomass (estimate): radiated energy of the fire (the FRP of each satellite pass " +
  "× the time it represents, up to 6 h) × 0.368 kg/MJ. Useful to compare fires; it " +
  "does not include what satellites do not detect.";

/** Cómo se mide la duración, para explicarlo en la interfaz. */
export const DURATION_HELP =
  "Duration: time between the first and the last satellite detection. It is a minimum: the fire " +
  "starts before the first pass and continues after the last one.";

/** Qué es una detección, para explicarlo en la interfaz. */
export const DETECTION_HELP =
  "A detection is a pixel in which a satellite (MODIS or VIIRS) recorded active fire during " +
  "a pass. A large or long fire adds up many detections.";

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

export const CONFIDENCE_COLORS: Record<string, string> = {
  low: "#e0b43c",
  medium: "#e8833a",
  high: "#c0392b",
  very_high: "#8e1f2f",
};

// Orden de menor a mayor confianza (nominal, de VIIRS, equivale a una confianza media).
const CONFIDENCE_RANK: Record<string, number> = {
  low: 0,
  medium: 1,
  nominal: 1,
  high: 2,
  very_high: 3,
};

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

export type Properties = Record<string, string | number | null>;
export type FireFeature = GeoJSON.Feature<GeoJSON.Geometry, Properties>;

export interface FireCollection {
  type: "FeatureCollection";
  features: FireFeature[];
  count: number;
  total: number;
  truncated: boolean;
}

export interface HistoryPeriod {
  label: string;
  start: string;
  end: string;
  is_reference: boolean;
  events: number;
  detections: number;
  frp_max_mw: number | null;
  /** Incendios del periodo por nivel de severidad (``low`` … ``extreme``, ``none``). */
  severity: Record<string, number>;
}

export interface HistoryResponse {
  bbox: number[];
  periods: HistoryPeriod[];
  events: Pick<FireCollection, "type" | "count" | "features">;
}

export interface EventDetail {
  event: FireFeature;
  detections: FireCollection;
}

export function escapeHtml(value: string | number | null | undefined): string {
  return String(value ?? "—").replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}

export function formatNumber(value: number): string {
  return value.toLocaleString("en-US");
}

export function formatDate(iso: string | number | null | undefined): string {
  return typeof iso === "string" ? `${iso.slice(0, 16).replace("T", " ")} UTC` : "—";
}

/** Día UTC de un instante ISO (2022-08-01T14:30:00Z) en formato día-mes-año (01-08-2022). */
export function formatDay(iso: string | number | null | undefined): string {
  if (typeof iso !== "string") return "—";
  const [year, month, day] = iso.slice(0, 10).split("-");
  return `${day}-${month}-${year}`;
}

/** "AAAA-MM-DD" -> "DD-MM" (para describir las mismas fechas en otros años). */
export function formatDayMonth(isoDate: string): string {
  const [, month, day] = isoDate.split("-");
  return `${day}-${month}`;
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

/** Texto al pasar el ratón por una detección: sensor, satélite y día-mes-año. */
export function detectionTooltip(properties: Properties): string {
  return `${sensorLabel(properties)} · ${formatDay(properties.acq_datetime)}`;
}

/** "1 detección" / "12 detecciones". */
export function detectionsLabel(value: string | number | null | undefined): string {
  const count = Number(value ?? 0);
  return `${formatNumber(count)} ${count === 1 ? "detection" : "detections"}`;
}

export function confidenceLabel(value: string | number | null | undefined): string {
  return CONFIDENCE_LABELS[String(value)] ?? escapeHtml(value);
}

function decimal(value: number): string {
  return value.toFixed(1);
}

/**
 * Duración entre la primera y la última detección. Con una sola pasada de satélite no hay forma
 * de saber cuánto duró: se dice así en lugar de "0 h".
 */
export function durationLabel(
  first: string | number | null,
  last: string | number | null,
  passes?: string | number | null,
): string {
  if (typeof first !== "string" || typeof last !== "string") return "—";
  if (Number(passes) === 1) return "seen in 1 pass";
  const hours = (Date.parse(last) - Date.parse(first)) / 3_600_000;
  if (hours < 1) return "less than 1 h";
  if (hours < 48) return `${decimal(hours)} h`;
  return `${decimal(hours / 24)} days`;
}

/** "20 pasadas · hueco máx. 11,5 h": cuántas veces vio el fuego un satélite y el mayor silencio. */
export function passesLabel(properties: Properties): string {
  const passes = Number(properties.n_passes ?? 0);
  if (!passes) return "—";
  if (passes === 1) return "1 pass";
  return `${formatNumber(passes)} passes · max gap ${decimal(Number(properties.max_pass_gap_hours ?? 0))} h`;
}

/** "12 min ago", "3 h ago", "2 d ago": cuánto hace de un instante ISO (vacío si no es válido). */
export function timeAgoLabel(iso: string | number | null | undefined): string {
  const time = typeof iso === "string" ? Date.parse(iso) : NaN;
  if (Number.isNaN(time)) return "—";
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

/** Antigüedad de una detección: "fresh" (< 6 h), "recent" (< 24 h) o "old". */
export function freshnessClass(iso: string | number | null | undefined): "fresh" | "recent" | "old" {
  const hours = (Date.now() - Date.parse(String(iso))) / 3_600_000;
  if (hours < 6) return "fresh";
  return hours < 24 ? "recent" : "old";
}

export const SEVERITY_COLORS: Record<string, string> = {
  low: "#5fa86f",
  moderate: "#f0b429",
  high: "#e2603a",
  extreme: "#8a1c2b",
  none: "#b9c3d0",
};

export const SEVERITY_ORDER = ["low", "moderate", "high", "extreme"] as const;
const SEVERITY_LABELS: Record<string, string> = {
  low: "Mild",
  moderate: "Moderate",
  high: "Severe",
  extreme: "Extreme",
};
export const SEVERITY_HELP =
  "Estimated severity based on burned biomass (or max FRP if there is no estimate). Indicative only.";

export function severityLabel(value: string | number | null | undefined): string {
  return SEVERITY_LABELS[String(value)] ?? "Unclassified";
}

const LAND_COVER_LABELS: Record<string, string> = {
  crops: "Cropland",
  trees: "Forest / trees",
  shrub_and_scrub: "Shrubland",
  grass: "Grassland",
  built: "Urban area",
  bare: "Bare ground",
  water: "Water",
  flooded_vegetation: "Flooded vegetation",
  other: "Other uses",
};

/** Land-cover classes in legend order, with their colour (SIGPAC classes plus "none"). */
export const LAND_COVER_KEYS = [
  { key: "crops", label: "Cropland", color: "#d9b44a" },
  { key: "trees", label: "Forest / trees", color: "#2f7d4f" },
  { key: "shrub_and_scrub", label: "Shrubland", color: "#8a9a3b" },
  { key: "grass", label: "Grassland", color: "#9ccc65" },
  { key: "built", label: "Urban area", color: "#8d6e63" },
  { key: "bare", label: "Bare ground", color: "#bfae98" },
  { key: "water", label: "Water", color: "#4a90c2" },
  { key: "flooded_vegetation", label: "Flooded vegetation", color: "#3fa7a0" },
  { key: "other", label: "Other uses", color: "#8a8f98" },
  { key: "none", label: "No data", color: "#ffffff" },
] as const;

/**
 * Legend keys of a `land_cover` value. A boundary parcel has several classes (`crops|trees`): it
 * counts as all of them, so it stays visible while any of its classes is shown. No value → "none".
 */
export function landCoverKeys(value: string | number | null | undefined): string[] {
  if (value == null || value === "") return ["none"];
  const known = new Set<string>(LAND_COVER_KEYS.map((item) => item.key));
  return [...new Set(String(value).split("|").map((part) => (known.has(part) ? part : "other")))];
}

/** Tipo de zona: "Forestal / arbolado" (varias clases se unen con " / "); "No data" si falta. */
export function landCoverLabel(value: string | number | null | undefined): string {
  if (value == null || value === "") return "No data";
  return String(value)
    .split("|")
    .map((part) => LAND_COVER_LABELS[part] ?? part)
    .join(" / ");
}

/** Biomasa quemada estimada: "≈ 95 kg", "≈ 12,4 t" o "≈ 99.556 t". */
export function biomassLabel(kg: string | number | null | undefined): string {
  const value = Number(kg);
  if (kg == null || !Number.isFinite(value)) return "—";
  if (value < 1000) return `≈ ${formatNumber(Math.round(value))} kg`;
  const tonnes = value / 1000;
  return tonnes >= 100 ? `≈ ${formatNumber(Math.round(tonnes))} t` : `≈ ${decimal(tonnes)} t`;
}

/** URL de la pestaña con todos los incendios de una búsqueda (ver fires.ts). */
export function firesPageUrl(params: Record<string, string>): string {
  return `/fires.html?${new URLSearchParams(params)}`;
}

export async function fetchJson<T>(
  path: string,
  params: Record<string, string>,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}?${new URLSearchParams(params)}`, { signal });
  if (!response.ok) {
    let message = `${path}: HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // Respuesta sin JSON: se queda el código HTTP.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export function detectionPopup(properties: Properties): string {
  return `<b>Detection ${sensorLabel(properties)}</b><br>
    ${formatDate(properties.acq_datetime)}<br>
    FRP: <b>${escapeHtml(properties.frp_mw)} MW</b> (${escapeHtml(properties.frp_category)})<br>
    Confidence: ${confidenceLabel(properties.confidence)} ·
    ${properties.daynight === "D" ? "day" : "night"}<br>
    Biomass: ${escapeHtml(properties.biomass_kg_s)} kg/s<br>
    Area: ${escapeHtml(properties.area_width_km)} × ${escapeHtml(properties.area_height_km)} km<br>
    Land cover: ${escapeHtml(landCoverLabel(properties.land_cover))}`;
}

/** "12 detecciones en la zona" si el incendio se buscó dentro de una zona; si no, el total. */
export function zoneDetectionsLabel(properties: Properties): string {
  return properties.zone_detections == null
    ? detectionsLabel(properties.n_detections)
    : `${detectionsLabel(properties.zone_detections)} in the zone`;
}

export function eventPopup(properties: Properties): string {
  const zone =
    properties.zone_detections == null
      ? ""
      : `In the zone: <b>${detectionsLabel(properties.zone_detections)}</b>
        (max FRP ${escapeHtml(properties.zone_frp_max_mw)} MW)<br>`;
  return `<b>Fire ${properties.status === "active" ? "active" : "closed"}</b>
    (${sensorsLabel(properties.sensors)})<br>
    ${formatDate(properties.first_detection)} → ${formatDate(properties.last_detection)}<br>
    ${zone}Whole fire: ${detectionsLabel(properties.n_detections)} · max FRP:
    <b>${escapeHtml(properties.frp_max_mw)} MW</b> · burned biomass
    <b title="${escapeHtml(BIOMASS_HELP)}">${biomassLabel(properties.biomass_burned_kg)}</b><br>
    Duration: ${durationLabel(properties.first_detection, properties.last_detection, properties.n_passes)}
    (${passesLabel(properties)})<br>
    Severity: <b title="${escapeHtml(SEVERITY_HELP)}">${severityLabel(properties.severity)}</b> ·
    Zone type: <b>${escapeHtml(landCoverLabel(properties.land_cover))}</b><br>
    Confidence: ${confidenceLabel(properties.confidence)}<br>
    Footprint: ${escapeHtml(properties.footprint_area_km2)} km² (not burned area)`;
}
