// Medidas de un incendio en texto: duración, pasadas, biomasa y antigüedad.
import type { Properties } from "../api/types.js";
import { formatDecimal, formatNumber } from "../lib/format.js";

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
  if (hours < 48) return `${formatDecimal(hours)} h`;
  return `${formatDecimal(hours / 24)} days`;
}

/** "20 passes · max gap 11.5 h": cuántas veces vio el fuego un satélite y el mayor silencio. */
export function passesLabel(properties: Properties): string {
  const passes = Number(properties.n_passes ?? 0);
  if (!passes) return "—";
  if (passes === 1) return "1 pass";
  return `${formatNumber(passes)} passes · max gap ${formatDecimal(Number(properties.max_pass_gap_hours ?? 0))} h`;
}

/** Biomasa quemada estimada: "≈ 95 kg", "≈ 12.4 t" o "≈ 99,556 t". */
export function biomassLabel(kg: string | number | null | undefined): string {
  const value = Number(kg);
  if (kg == null || !Number.isFinite(value)) return "—";
  if (value < 1000) return `≈ ${formatNumber(Math.round(value))} kg`;
  const tonnes = value / 1000;
  return tonnes >= 100 ? `≈ ${formatNumber(Math.round(tonnes))} t` : `≈ ${formatDecimal(tonnes)} t`;
}

/** Antigüedad de una detección: "fresh" (< 6 h), "recent" (< 24 h) o "old". */
export function freshnessClass(iso: string | number | null | undefined): "fresh" | "recent" | "old" {
  const hours = (Date.now() - Date.parse(String(iso))) / 3_600_000;
  if (hours < 6) return "fresh";
  return hours < 24 ? "recent" : "old";
}
