// Disposición de los paneles: en escritorio, panel de filtros y leyenda a la vista; en móvil,
// ocultos tras sus botones de la barra superior (Filters · Alerts · Legend). La leyenda y la lista
// de alertas no se abren a la vez en móvil, para que no se pisen.
import { isolateFromMap } from "../../lib/dom.js";
import { isMobile, mobileQuery } from "../../lib/responsive.js";
import { ui } from "../dom.js";

/** Muestra u oculta el panel de filtros; el botón del embudo aparece cuando está oculto. */
export function setPanelOpen(open: boolean): void {
  ui.panel.classList.toggle("panel-hidden", !open);
  ui.filtersFab.hidden = open;
}

export function setAlertsOpen(open: boolean): void {
  ui.alertsList.hidden = !open;
  ui.alertsToggle.setAttribute("aria-expanded", String(open));
}

/** Móvil: la leyenda se abre con su botón (icono de capas) y se cierra con la ✕. */
export function setLegendOpen(open: boolean): void {
  ui.legend.classList.toggle("legend-hidden", isMobile() && !open);
  ui.legendFab.setAttribute("aria-expanded", String(open));
  if (open && isMobile()) setAlertsOpen(false);
}

/** Disposición inicial de cada modo: escritorio con panel y leyenda a la vista; móvil, ocultos. */
function applyLayoutMode(): void {
  if (isMobile()) {
    ui.legend.querySelector<HTMLDetailsElement>(".legend-all")!.open = true; // se cierra entero
    setLegendOpen(false);
    setPanelOpen(false);
  } else {
    ui.legend.classList.remove("legend-hidden");
    setPanelOpen(true);
  }
}

/** Margen del encuadre para que lo encuadrado no quede debajo de los paneles. */
export function viewPadding(): { top: number; right: number; bottom: number; left: number } {
  const activityOpen = document.body.classList.contains("activity-open");
  if (isMobile()) {
    // Móvil: el panel de filtros es una hoja superior y la actividad, una inferior.
    return { top: 70, right: 20, bottom: activityOpen ? window.innerHeight * 0.45 + 20 : 40, left: 20 };
  }
  return { top: 60, right: activityOpen ? 380 : 60, bottom: 40, left: ui.panel.offsetWidth + 36 };
}

/** Necesita la leyenda ya pintada (ver legend.ts). */
export function initLayout(): void {
  // Los clics y la rueda sobre los paneles no llegan al mapa.
  isolateFromMap(ui.panel, ui.legend, ui.alertsControl, ui.legendFab, ui.filtersFab);

  ui.alertsToggle.addEventListener("click", () => {
    const open = ui.alertsList.hidden;
    setAlertsOpen(open);
    if (isMobile() && open) setLegendOpen(false);
  });
  ui.legendFab.addEventListener("click", () => setLegendOpen(ui.legend.classList.contains("legend-hidden")));
  ui.legend.querySelector("[data-legend-close]")!.addEventListener("click", () => setLegendOpen(false));
  ui.filtersFab.addEventListener("click", () => setPanelOpen(true));
  ui.panelClose.addEventListener("click", () => setPanelOpen(false));

  applyLayoutMode();
  mobileQuery.addEventListener("change", applyLayoutMode);
}
