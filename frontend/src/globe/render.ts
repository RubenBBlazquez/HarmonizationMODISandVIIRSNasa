// Pintar el estado en el mapa. `pushData` envía los datos (solo cuando cambian); `applyFilters`
// vuelve a aplicar lo visible (leyenda, años, reproducción) sin reenviarlos.
import { drawActiveMarkers } from "./features/activeMarkers.js";
import { applyLayerFilters } from "./map/filters.js";
import { detectionLayers, eventsLayer } from "./map/geojson.js";
import { applyYearOutlines, layersReady, setData } from "./map/layers.js";
import { state } from "./state.js";

export function applyFilters(): void {
  if (!layersReady()) return;
  applyLayerFilters();
  drawActiveMarkers();
}

/** Envía al mapa lo último cargado del rango de fechas. */
export function pushData(): void {
  const layers = detectionLayers(state.detections);
  setData("detections", layers.points);
  setData("pixels", layers.pixels);
  setData("events", eventsLayer(state.events));
  applyYearOutlines();
  applyFilters();
}
