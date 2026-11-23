// Una función por endpoint de la API: aquí están todas las rutas y parámetros que usa el frontend.
import { MAX_CARDS } from "../domain/config.js";
import { fetchJson } from "./client.js";
import type {
  ActivityResponse,
  CountryCollection,
  EventDetail,
  FireCollection,
  HistoryResponse,
  Summary,
  YearlyRow,
} from "./types.js";

export type FireLayer = "events" | "detections";

export function getSummary(): Promise<Summary> {
  return fetchJson<Summary>("/summary", {});
}

export function getCountries(): Promise<CountryCollection> {
  return fetchJson<CountryCollection>("/countries", {});
}

/** Eventos o detecciones con los filtros de `params` (país, fechas, bbox, all_years…). */
export function getFireLayer(
  layer: FireLayer,
  params: Record<string, string>,
  signal?: AbortSignal,
): Promise<FireCollection> {
  return fetchJson<FireCollection>(`/${layer}`, params, signal);
}

/** Contadores por año de un país para las mismas fechas del rango. */
export function getYearly(start: string, end: string, country: string): Promise<{ years: YearlyRow[] }> {
  return fetchJson<{ years: YearlyRow[] }>("/yearly", { start, end, country });
}

/** Un incendio y (como mucho `limit`) sus detecciones. */
export function getEventDetail(eventId: string, limit: number): Promise<EventDetail> {
  return fetchJson<EventDetail>(`/events/${encodeURIComponent(eventId)}`, { limit: String(limit) });
}

/** Zona y fechas del histórico ("las mismas fechas en los años anteriores"). */
export interface ZoneHistoryQuery {
  /** Recuadro ``minLon,minLat,maxLon,maxLat``. */
  bbox: string;
  start: string;
  end: string;
  years: number;
}

export function getZoneHistory(query: ZoneHistoryQuery, sort?: string): Promise<HistoryResponse> {
  const params: Record<string, string> = {
    bbox: query.bbox,
    start: query.start,
    end: query.end,
    years: String(query.years),
    limit: String(MAX_CARDS),
  };
  if (sort) params.sort = sort;
  return fetchJson<HistoryResponse>("/history", params);
}

export function getActivity(limit: number): Promise<ActivityResponse> {
  return fetchJson<ActivityResponse>("/activity", { limit: String(limit) });
}
