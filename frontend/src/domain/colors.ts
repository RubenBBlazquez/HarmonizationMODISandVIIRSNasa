// Paleta de los datos de incendios: la comparten el globo (MapLibre) y los mapas de las tarjetas
// (Leaflet), para que un mismo dato se vea igual en todas partes.

export const FRP_COLORS: Record<string, string> = {
  low: "#e0b43c",
  medium: "#e8833a",
  high: "#c0392b",
};
export const NO_CATEGORY_COLOR = "#8a8f98";
export const EVENT_COLORS: Record<string, string> = { active: "#c0392b", closed: "#5b6b62" };
export const CONFIDENCE_COLORS: Record<string, string> = {
  low: "#e0b43c",
  medium: "#e8833a",
  high: "#c0392b",
  very_high: "#8e1f2f",
};

export function confidenceColor(value: unknown): string {
  return CONFIDENCE_COLORS[String(value)] ?? NO_CATEGORY_COLOR;
}

// Color de cada año (contornos al comparar años en el globo y etiquetas del histórico).
const YEAR_COLORS = [
  "#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b",
  "#17becf", "#e377c2", "#bcbd22", "#7f7f7f", "#d62728",
];
export const yearColor = (year: number): string => YEAR_COLORS[year % YEAR_COLORS.length];

// Píxel de una detección. VIIRS (375 m) es más preciso que MODIS (1 km): sus píxeles destacan y
// los de MODIS quedan de fondo.
export const PIXEL_BORDER_COLOR = "#4a4f55";
export const PIXEL_FILL_OPACITY = 0.45;
export const MODIS_PIXEL_FILL_OPACITY = 0.1;
export const MODIS_PIXEL_BORDER_OPACITY = 0.35;
