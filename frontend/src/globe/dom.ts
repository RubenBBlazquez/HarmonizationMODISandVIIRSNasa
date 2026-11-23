// Elementos fijos de index.html que usa el globo, en un solo sitio. Los paneles que se crean desde
// el código (popups, menús, ventanas) los crea cada módulo.
import { $ } from "../lib/dom.js";

export const ui = {
  // Panel de filtros (izquierda) y su botón para reabrirlo.
  panel: $(".globe-panel"),
  panelClose: $<HTMLButtonElement>("#panel-close"),
  filtersFab: $<HTMLButtonElement>("#filters-fab"),
  countryName: $("#country-name"),
  countryNote: $("#country-note"),
  worldButton: $<HTMLButtonElement>("#world-btn"),
  eventsToggle: $<HTMLInputElement>("#layer-events"),
  detectionsToggle: $<HTMLInputElement>("#layer-detections"),
  dateRange: $("#date-range"),
  allYearsToggle: $<HTMLInputElement>("#all-years-months"),
  allYearsHint: $("#all-years-hint"),
  yearsLegend: $("#years-legend"),
  playButton: $<HTMLButtonElement>("#play-btn"),
  selectButton: $<HTMLButtonElement>("#select-area"),
  status: $("#map-status"),
  // Sobre el mapa.
  legend: $("#legend"),
  legendFab: $<HTMLButtonElement>("#legend-fab"),
  coordinates: $("#coordinates"),
  alertsControl: $(".alerts-control"),
  alertsToggle: $<HTMLButtonElement>(".alerts-toggle"),
  alertsList: $(".alerts-list"),
  // Barra del incendio enfocado ("View on the globe").
  focusBar: $("#focus-bar"),
  focusTitle: $("#focus-title"),
  focusSub: $("#focus-sub"),
  focusBack: $<HTMLButtonElement>("#focus-back"),
  focusClose: $<HTMLButtonElement>("#focus-close"),
};
