// "Compare with all years": las mismas fechas del rango en cada año, con el contorno coloreado por
// año y un chip por año (con sus totales en el país) para ocultarlo o mostrarlo.
import { getYearly } from "../../api/endpoints.js";
import { yearColor } from "../../domain/colors.js";
import { formatNumber } from "../../lib/format.js";
import { ui } from "../dom.js";
import { applyFilters } from "../render.js";
import { state, toggleHidden } from "../state.js";
import { updateStatusUnlessPlaying } from "../ui/status.js";
import { syncLegend } from "./legend.js";

/** Pinta los chips de años (todos activos salvo los ocultados con su chip). */
export function updateYearsLegend(): void {
  ui.yearsLegend.hidden = !state.allYears || state.yearly.length === 0;
  if (ui.yearsLegend.hidden) return;
  ui.yearsLegend.querySelector<HTMLElement>(".years-chips")!.innerHTML = state.yearly
    .map(
      (row) => `<button type="button" class="year-chip" data-year="${row.year}"
        aria-pressed="${!state.hiddenKeys.has(`year:${row.year}`)}"
        title="${row.year}: ${formatNumber(row.events)} fires (events) and ${formatNumber(row.detections)} satellite detections in the country">
        <i style="--c:${yearColor(row.year)}"></i><b>${row.year}</b>
        <span>${formatNumber(row.events)} events · ${formatNumber(row.detections)} detections</span></button>`,
    )
    .join("");
}

// Contadores por año del país: los cachea la API y solo se piden al cambiar rango o país. Van
// aparte de los datos del mapa para no retrasarlos.
let yearlyRequested = "";
export async function loadYearly(start: string, end: string, country: string): Promise<void> {
  const key = `${start}|${end}|${country}`;
  if (key === state.yearlyKey || key === yearlyRequested) return;
  yearlyRequested = key;
  try {
    const data = await getYearly(start, end, country);
    if (yearlyRequested !== key) return; // llegó tarde: ya se pidió otro rango
    state.yearly = data.years;
    state.yearlyKey = key;
    if (state.allYears) {
      updateYearsLegend();
      updateStatusUnlessPlaying();
    }
  } catch {
    yearlyRequested = ""; // se reintentará en la siguiente carga
  }
}

/** `onToggle` se llama al marcar o desmarcar la casilla (hay que volver a cargar los datos). */
export function initYearsComparison(onToggle: () => void): void {
  ui.yearsLegend.addEventListener("click", (event) => {
    const chip = (event.target as HTMLElement).closest<HTMLButtonElement>("button.year-chip");
    if (!chip) return;
    const key = `year:${chip.dataset.year}`;
    toggleHidden(key);
    chip.setAttribute("aria-pressed", String(!state.hiddenKeys.has(key)));
    syncLegend();
    applyFilters();
  });
  ui.allYearsToggle.addEventListener("change", () => {
    // Al entrar o salir del modo se olvida la selección de años: vuelven a estar todos activos.
    for (const key of [...state.hiddenKeys]) if (key.startsWith("year:")) state.hiddenKeys.delete(key);
    syncLegend();
    onToggle();
  });
}
