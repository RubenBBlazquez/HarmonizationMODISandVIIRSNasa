// Contrato de la API: forma de las respuestas que usa el frontend.

export type Properties = Record<string, string | number | null>;
export type FireFeature = GeoJSON.Feature<GeoJSON.Geometry, Properties>;

/** Detecciones o eventos (/detections, /events): GeoJSON con el recuento y si se recortó. */
export interface FireCollection {
  type: "FeatureCollection";
  features: FireFeature[];
  count: number;
  total: number;
  truncated: boolean;
}

/** Un incendio con sus detecciones (/events/{id}). */
export interface EventDetail {
  event: FireFeature;
  detections: FireCollection;
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

/** Mismas fechas de una zona en los años anteriores (/history). */
export interface HistoryResponse {
  bbox: number[];
  periods: HistoryPeriod[];
  events: Pick<FireCollection, "type" | "count" | "features">;
}

/** Primer y último día con datos (/summary). */
export interface Summary {
  first_detection: string | null;
  last_detection: string | null;
}

export interface CountryProperties {
  code: string;
  name: string;
  continent: string | null;
  has_data: boolean;
}
export type CountryFeature = GeoJSON.Feature<GeoJSON.Geometry, CountryProperties>;
export type CountryCollection = GeoJSON.FeatureCollection<GeoJSON.Geometry, CountryProperties>;

/** Contadores de un año para las mismas fechas (/yearly). */
export interface YearlyRow {
  year: number;
  events: number;
  detections: number;
  frp_max_mw: number | null;
}

/** Últimos incendios registrados (/activity). */
export interface ActivityItem {
  event_id: string;
  status: string;
  sensors: string;
  n_detections: number;
  frp_max_mw: number | null;
  first_detection: string | null;
  last_detection: string | null;
  lat: number;
  lon: number;
}

export interface ActivityResponse {
  count: number;
  items: ActivityItem[];
}
