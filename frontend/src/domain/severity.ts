// Severidad estimada de un incendio (la calcula la API a partir de la biomasa o del FRP).

export const SEVERITY_ORDER = ["low", "moderate", "high", "extreme"] as const;
export type Severity = (typeof SEVERITY_ORDER)[number];

export const SEVERITY_COLORS: Record<string, string> = {
  low: "#5fa86f",
  moderate: "#f0b429",
  high: "#e2603a",
  extreme: "#8a1c2b",
  none: "#b9c3d0",
};

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

/** Clase CSS de la pastilla de severidad: "sev-high", "sev-none"… */
export function severityClass(value: string | number | null | undefined): string {
  return `sev-${SEVERITY_ORDER.includes(value as never) ? value : "none"}`;
}

/** Posición en `SEVERITY_ORDER` (-1 si no está clasificado): para ordenar de más a menos grave. */
export function severityRank(value: string | number | null | undefined): number {
  return SEVERITY_ORDER.indexOf(String(value) as Severity);
}
