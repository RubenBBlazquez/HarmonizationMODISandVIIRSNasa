// Estado compartido del globo. Los módulos lo leen y lo cambian directamente; quien lo cambia es
// responsable de volver a pintar lo afectado (render.applyFilters, status.updateStatus…).
import type { LngLat } from "maplibre-gl";

import type { CountryCollection, FireCollection, FireFeature, YearlyRow } from "../api/types.js";
import type { SuspendedModals } from "../components/modal.js";

/**
 * Incendio concreto enfocado desde el histórico ("View on the globe"): mientras dura, las capas
 * muestran solo ese incendio y el rango de fechas cargado queda en memoria, sin pintar.
 */
export interface FocusState {
  previousView: { center: LngLat; zoom: number; bearing: number; pitch: number };
  modals: SuspendedModals | null;
  /** Llegó por la URL (?fire=) desde la pestaña de incendios: "Back" regresa a esa página. */
  fromUrl: boolean;
  /** Pasadas del satélite (hora de adquisición) y detecciones acumuladas hasta cada una. */
  timeline: { time: string; total: number }[];
}

export const state = {
  /** Fronteras de todos los países (/countries). */
  countries: null as CountryCollection | null,
  /** País elegido (código ISO3), también en el #hash de la URL. */
  selectedCode: null as string | null,
  /**
   * Lo que se oculta desde la leyenda o los chips de años ("frp:low", "status:closed", "lc:crops",
   * "year:2019"…). Se filtra en el cliente, sin volver a pedir datos. Por defecto los eventos
   * cerrados están ocultos: se activan desde la leyenda.
   */
  hiddenKeys: new Set<string>(["status:closed"]),
  /** Última carga de cada capa (null: capa apagada). */
  events: null as FireCollection | null,
  detections: null as FireCollection | null,
  /** La última carga fue de las mismas fechas en todos los años. */
  allYears: false,
  /** Contadores por año ("comparar con todos los años") y la búsqueda a la que corresponden. */
  yearly: [] as YearlyRow[],
  yearlyKey: "",
  /** Reproducción del rango: solo se ve lo ocurrido hasta este día (acumulado). */
  playDay: null as string | null,
  /** Reproducción del incendio enfocado: hora (ISO) hasta la que se ve. */
  playTime: null as string | null,
  focus: null as FocusState | null,
  /** Modo "seleccionar zona": el arrastre dibuja un recuadro en lugar de mover el mapa. */
  selecting: false,
};

/** Valores ocultos con un prefijo: hiddenOf("frp:") → ["low"]. */
export const hiddenOf = (prefix: string): string[] =>
  [...state.hiddenKeys].filter((key) => key.startsWith(prefix)).map((key) => key.slice(prefix.length));

/** Muestra u oculta una clave de la leyenda. */
export function toggleHidden(key: string): void {
  if (state.hiddenKeys.has(key)) state.hiddenKeys.delete(key);
  else state.hiddenKeys.add(key);
}

/** Los colores por año se aplican al comparar años, pero no al incendio enfocado. */
export const byYear = (): boolean => state.allYears && !state.focus;

/** Visible en la reproducción del rango (ocurrido hasta `playDay`). */
export const isVisibleByDate = (feature: FireFeature, field: string): boolean =>
  !state.playDay || String(feature.properties[field] ?? "9999").slice(0, 10) <= state.playDay;

/** Su año no está oculto en los chips de "comparar con todos los años". */
export const isVisibleYear = (feature: FireFeature, field: string): boolean =>
  !state.allYears || !state.hiddenKeys.has(`year:${String(feature.properties[field] ?? "").slice(0, 4)}`);
