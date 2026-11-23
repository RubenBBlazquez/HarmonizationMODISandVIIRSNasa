// Tipo de suelo (parcela SIGPAC) donde está un incendio o una detección.

/** Clases en el orden de la leyenda, con su color (clases SIGPAC más "none"). */
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

const KNOWN_KEYS = new Set<string>(LAND_COVER_KEYS.map((item) => item.key));
const LAND_COVER_LABELS: Record<string, string> = Object.fromEntries(
  LAND_COVER_KEYS.filter((item) => item.key !== "none").map((item) => [item.key, item.label]),
);

/**
 * Claves de la leyenda de un valor `land_cover`. Una parcela de linde tiene varias clases
 * (`crops|trees`): cuenta como todas, así que se ve mientras alguna de ellas esté visible. Sin
 * valor → "none".
 */
export function landCoverKeys(value: string | number | null | undefined): string[] {
  if (value == null || value === "") return ["none"];
  return [...new Set(String(value).split("|").map((part) => (KNOWN_KEYS.has(part) ? part : "other")))];
}

/** Tipo de zona: "Forest / trees" (varias clases se unen con " / "); "No data" si falta. */
export function landCoverLabel(value: string | number | null | undefined): string {
  if (value == null || value === "") return "No data";
  return String(value)
    .split("|")
    .map((part) => LAND_COVER_LABELS[part] ?? part)
    .join(" / ");
}
