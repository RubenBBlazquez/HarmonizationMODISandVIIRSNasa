// Carga de datos: todo el rango de fechas del país elegido de una vez (/events y /detections con
// ?country=). Al mover o acercar el mapa no se pide nada más; lo que se oculta se filtra en el
// cliente (render.applyFilters).
import { getFireLayer, type FireLayer } from "../../api/endpoints.js";
import type { FireCollection } from "../../api/types.js";
import { ui } from "../dom.js";
import { pushData } from "../render.js";
import { state } from "../state.js";
import { clearBusyWhenDrawn, isLatestLoad, setBusy } from "../ui/busy.js";
import { setStatus, updateStatusUnlessPlaying } from "../ui/status.js";
import { rangeEnd, rangeStart } from "./dateRange.js";
import { exitFocus } from "./focus.js";
import { loadYearly, updateYearsLegend } from "./yearsComparison.js";

// Un año de España son ~60 000 detecciones.
const PRELOAD_LIMIT = 150000;
const REFRESH_DELAY_MS = 300;

let controller: AbortController | null = null;
let timer: number | undefined;
// Filtros con los que se cargó cada capa: si no cambian, se reutiliza lo ya descargado (aunque
// la capa se apague, para no pedirlo otra vez al encenderla).
const loaded: Record<FireLayer, { key: string; data: FireCollection } | null> = {
  events: null,
  detections: null,
};

async function refresh(): Promise<void> {
  if (!state.selectedCode) return;
  controller?.abort();
  controller = new AbortController();
  const { signal } = controller;
  const params: Record<string, string> = { limit: String(PRELOAD_LIMIT), country: state.selectedCode };
  if (rangeStart()) params.start = rangeStart();
  if (rangeEnd()) params.end = rangeEnd();
  // Mismas fechas del rango en todos los años: lo resuelve la API (necesita start y end).
  const allYears = ui.allYearsToggle.checked && Boolean(rangeStart() && rangeEnd());
  if (allYears) params.all_years = "true";

  const key = JSON.stringify(params);
  const load = async (layer: FireLayer, enabled: boolean): Promise<FireCollection | null> => {
    if (!enabled) return null;
    const cached = loaded[layer];
    if (cached?.key === key) return cached.data;
    const data = await getFireLayer(layer, params, signal);
    loaded[layer] = { key, data };
    return data;
  };

  const token = setBusy(true, allYears ? "Loading the same dates of every year…" : "Loading the fires…");
  try {
    if (allYears) void loadYearly(params.start, params.end, state.selectedCode); // no bloquea el mapa
    const [events, detections] = await Promise.all([
      load("events", ui.eventsToggle.checked),
      load("detections", ui.detectionsToggle.checked),
    ]);
    [state.events, state.detections, state.allYears] = [events, detections, allYears];
    updateYearsLegend();
    if (state.focus) {
      // Se pintará al quitar el incendio enfocado (queda en memoria).
      if (isLatestLoad(token)) setBusy(false);
      return;
    }
    setStatus("Drawing the fires on the map…");
    // Deja pintar el aviso antes de preparar las capas (con decenas de miles tarda un poco).
    await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
    if (signal.aborted) return;
    pushData();
    updateStatusUnlessPlaying();
    clearBusyWhenDrawn(token);
  } catch (error) {
    // Cancelada por una carga más nueva: el indicador sigue hasta que acabe esa.
    if ((error as Error).name === "AbortError") return;
    setBusy(false, "Could not load the data. Is the API running?");
  }
}

/** Vuelve a cargar en un momento (agrupa cambios seguidos de filtros). */
export function scheduleRefresh(): void {
  if (state.focus) exitFocus("remove", false); // el usuario cambia el rango: vuelve a verse el rango
  window.clearTimeout(timer);
  timer = window.setTimeout(() => void refresh(), REFRESH_DELAY_MS);
}

/** Carga ya, cancelando la carga programada. */
export function refreshNow(): Promise<void> {
  window.clearTimeout(timer);
  return refresh();
}

export function initDataLoader(): void {
  for (const input of [ui.eventsToggle, ui.detectionsToggle]) {
    input.addEventListener("change", scheduleRefresh);
  }
}
