// Rango de fechas del globo: el selector del panel, recordado entre visitas. Nunca queda vacío
// (sería todo el histórico): por defecto, el último año con datos.
import { DateRangePicker, type DateRange } from "../../components/dateRangePicker/dateRangePicker.js";
import { addDays, formatDayMonth } from "../../lib/dates.js";
import { isMobile } from "../../lib/responsive.js";
import { readStorage, writeStorage } from "../../lib/storage.js";
import { ui } from "../dom.js";
import { setPanelOpen } from "../ui/layout.js";

const DEFAULT_RANGE_DAYS = 365;
const RANGE_STORAGE_KEY = "fire-map:date-range";

let picker: DateRangePicker | null = null;
// Último día con datos (de /summary): el rango por defecto es el año que acaba en él.
let lastDataDay: string | null = null;

export const rangeStart = (): string => picker?.value?.start ?? "";
export const rangeEnd = (): string => picker?.value?.end ?? "";

function saveRange(value: DateRange | null): void {
  writeStorage(RANGE_STORAGE_KEY, value ? JSON.stringify(value) : null);
}

function loadRange(): DateRange | null {
  try {
    const value = JSON.parse(readStorage(RANGE_STORAGE_KEY) ?? "null");
    const valid = (day: unknown): boolean =>
      typeof day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(day);
    return value && valid(value.start) && valid(value.end) && value.start <= value.end
      ? value
      : null;
  } catch {
    return null;
  }
}

const defaultRange = (): DateRange | null =>
  lastDataDay ? { start: addDays(lastDataDay, -(DEFAULT_RANGE_DAYS - 1)), end: lastDataDay } : null;

/** Texto de "Compare with all years": las mismas fechas del rango en cada año. */
function updateMonthsHint(): void {
  const start = picker?.value?.start;
  const end = picker?.value?.end;
  ui.allYearsHint.textContent =
    start && end
      ? `Same dates (${formatDayMonth(start)} → ${formatDayMonth(end)}) in each year`
      : "Same dates of the range in each year";
}

/** Crea el selector; `onChange` se llama cada vez que el usuario cambia el rango. */
export function initDateRange(onChange: () => void): void {
  const instance = new DateRangePicker(ui.dateRange, (chosen) => {
    // Nunca sin rango: si se queda vacío, vuelve al último año.
    const value = chosen ?? defaultRange();
    if (!chosen && value) instance.setRange(value);
    saveRange(value);
    updateMonthsHint();
    onChange();
  });
  picker = instance;
  // Móvil: al terminar de elegir fechas ("Done" o un atajo como "Last week") se oculta el panel.
  ui.dateRange.addEventListener("click", (event) => {
    const target = event.target as HTMLElement;
    if (isMobile() && target.closest("[data-done], [data-preset]")) setPanelOpen(false);
  });
  updateMonthsHint();
}

/** Limita el calendario a los días con datos y fija el rango inicial (el recordado o el último año). */
export function setDataBounds(first: string, last: string): void {
  picker?.setBounds(first, last);
  lastDataDay = last;
  picker?.setRange(loadRange() ?? defaultRange());
  updateMonthsHint();
}
