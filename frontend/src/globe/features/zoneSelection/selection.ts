// Selección de zona: el recuadro dibujado en el mapa, su menú (histórico de la zona) y el modo
// "seleccionando" del botón del panel. Los gestos que dibujan el recuadro están en gestures.ts.
import * as maplibregl from "maplibre-gl";

import { ICONS } from "../../../components/icons.js";
import { openModal } from "../../../components/modal.js";
import { loadHistoryInto } from "../../../components/zoneHistory/zoneHistory.js";
import { HISTORY_YEARS } from "../../../domain/config.js";
import { firesPageUrl } from "../../../domain/urls.js";
import { isMobile, mobileQuery } from "../../../lib/responsive.js";
import { ui } from "../../dom.js";
import { boxPolygon, EMPTY, type Bounds } from "../../map/geojson.js";
import { LAYERS, setData } from "../../map/layers.js";
import { map } from "../../map/map.js";
import { hideTip } from "../../map/tooltip.js";
import { state } from "../../state.js";
import { setPanelOpen } from "../../ui/layout.js";
import { rangeEnd, rangeStart } from "../dateRange.js";

// Móvil: en lugar de la tecla Ctrl, un icono de dedo pulsando (mantener pulsado el mapa).
const HOLD_BADGE = `<span class="hold-badge" title="Or press and hold the map, then drag" aria-label="Press and hold the map">
  ${ICONS.hold}</span>`;

/** Esquina donde empezó el arrastre (null: no se está arrastrando). */
export const drag = { anchor: null as maplibregl.LngLat | null };
let menuPopup: maplibregl.Popup | null = null;
let selectedBounds: Bounds | null = null; // zona dibujada (sigue en el mapa aunque el menú se cierre)
/** Aviso flotante en móvil mientras se dibuja la zona (el panel de filtros está oculto). */
let selectHint: HTMLElement | null = null;

export function boundsOf(a: maplibregl.LngLat, b: maplibregl.LngLat): Bounds {
  return {
    west: Math.min(a.lng, b.lng),
    east: Math.max(a.lng, b.lng),
    south: Math.min(a.lat, b.lat),
    north: Math.max(a.lat, b.lat),
  };
}

export function drawSelection(bounds: Bounds | null): void {
  setData(
    "selection",
    bounds
      ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: boxPolygon(bounds) }] }
      : EMPTY,
  );
}

export function closeMenu(): void {
  const popup = menuPopup;
  menuPopup = null; // antes de remove(): su evento "close" no debe borrar la zona
  popup?.remove();
}

export function clearSelection(): void {
  closeMenu();
  selectedBounds = null;
  drawSelection(null);
}

// "Años anteriores": misma zona y mismas fechas del año en los años anteriores, en una ventana
// grande con el resumen por años y el mapa de cada incendio (un clic en un mapa lo amplía).
async function showHistory(bounds: Bounds): Promise<void> {
  const modal = openModal("Fires from previous years", "history-modal", "Zone history");
  if (!rangeStart() || !rangeEnd()) {
    modal.body.innerHTML =
      '<p class="history-empty">Choose a date range to compare with previous years.</p>';
    return;
  }
  const query = {
    bbox: [bounds.west, bounds.south, bounds.east, bounds.north].map((v) => v.toFixed(5)).join(","),
    start: rangeStart(),
    end: rangeEnd(),
  };
  await loadHistoryInto(modal.body, {
    ...query,
    years: HISTORY_YEARS,
    tabUrl: firesPageUrl({ mode: "history", ...query, years: String(HISTORY_YEARS) }),
  });
}

/** Menú de la zona dibujada (es la zona: su ✕ también la quita). */
export function openSelectionMenu(bounds: Bounds): void {
  const element = document.createElement("div");
  element.className = "selection-menu";
  element.innerHTML = `
    <b>Selected zone</b>
    <small>Lat ${bounds.south.toFixed(3)} to ${bounds.north.toFixed(3)} ·
      Lon ${bounds.west.toFixed(3)} to ${bounds.east.toFixed(3)}</small>
    <button type="button" data-action="history">Fires of the last ${HISTORY_YEARS} years</button>
    <button type="button" data-action="predict" disabled
      title="Time-series forecast: will there be fire today or in X time in this zone?">
      Predict fire in the zone (time series) <em>· Coming soon</em></button>
    <button type="button" data-action="clear">Clear selection</button>`;
  element.querySelectorAll<HTMLButtonElement>("button[data-action]").forEach((button) =>
    button.addEventListener("click", () => {
      // El histórico se abre encima y el menú se queda: al volver sigue a mano "Clear selection".
      if (button.dataset.action === "history") void showHistory(bounds);
      else clearSelection();
    }),
  );
  closeMenu();
  selectedBounds = bounds;
  // closeOnClick: false -> el clic que termina el arrastre no cierra el menú nada más abrirlo.
  const popup = new maplibregl.Popup({ closeOnClick: false, maxWidth: "280px" })
    .setLngLat([(bounds.west + bounds.east) / 2, (bounds.south + bounds.north) / 2])
    .setDOMContent(element)
    .addTo(map);
  popup.on("close", () => {
    if (menuPopup === popup) clearSelection();
  });
  menuPopup = popup;
}

function showSelectHint(show: boolean): void {
  if (!show) {
    selectHint?.remove();
    selectHint = null;
    return;
  }
  if (selectHint) return;
  selectHint = document.createElement("div");
  selectHint.className = "select-hint";
  selectHint.innerHTML = `<span>Drag on the map to select a zone</span>
    <button type="button" data-cancel>Cancel</button>`;
  selectHint.querySelector("[data-cancel]")!.addEventListener("click", () => {
    clearSelection();
    setSelecting(false);
  });
  document.body.append(selectHint);
}

/** Entra o sale del modo selección: el arrastre dibuja el recuadro y el mapa deja de moverse. */
export function setSelecting(active: boolean): void {
  state.selecting = active;
  drag.anchor = null;
  ui.selectButton.classList.toggle("active", active);
  ui.selectButton.innerHTML = `${ICONS.select}<span>${
    active ? "Drag on the map… (Esc cancels)" : "Select zone"
  }</span>${active ? "" : isMobile() ? HOLD_BADGE : "<kbd>Ctrl</kbd>"}`;
  map.getContainer().classList.toggle("selecting", active);
  if (active) {
    hideTip();
    map.dragPan.disable();
    map.touchZoomRotate.disable(); // con el dedo, el arrastre dibuja el recuadro (no hace zoom)
  } else {
    map.dragPan.enable();
    map.touchZoomRotate.enable();
  }
  showSelectHint(active && isMobile());
}

export function initSelection(): void {
  // Tocar el recuadro de una zona sin menú (p. ej. tras cerrar el histórico) vuelve a abrirlo.
  map.on("click", LAYERS.selectionFill, () => {
    if (!state.selecting && !menuPopup && selectedBounds) openSelectionMenu(selectedBounds);
  });
  setSelecting(false); // pinta el botón (icono + texto)
  // Al pasar de móvil a escritorio (o al revés) cambia el distintivo del botón (dedo / Ctrl).
  mobileQuery.addEventListener("change", () => {
    if (!state.selecting) setSelecting(false);
  });
  ui.selectButton.addEventListener("click", () => {
    if (state.selecting && drag.anchor) clearSelection(); // arrastre a medias: se descarta
    setSelecting(!state.selecting);
    if (state.selecting && isMobile()) setPanelOpen(false); // móvil: deja ver el mapa para dibujar
  });
}
