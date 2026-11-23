// Pintar el estado en el mapa. `pushData` envía los datos (solo cuando cambian); `applyFilters`
// vuelve a aplicar lo visible (leyenda, años, reproducción) sin reenviarlos.
import { PIXEL_MIN_ZOOM } from "./config.js";
import { drawActiveMarkers } from "./features/activeMarkers.js";
import { applyLayerFilters } from "./map/filters.js";
import { detectionPixels, detectionPoints, EMPTY, eventsLayer } from "./map/geojson.js";
import { applyYearOutlines, layersReady, setData } from "./map/layers.js";
import { map } from "./map/map.js";
import { state } from "./state.js";

// Los píxeles (cuadrados ancho × alto) solo se ven desde PIXEL_MIN_ZOOM. Enviarlos al mapa junto
// con los puntos duplicaba el trabajo de cada carga (copiar decenas de miles de polígonos al
// worker de MapLibre) justo al cambiar de país. Se envían después: al acercarse a ese zoom o en
// cuanto el mapa queda quieto y el navegador está libre.
const PIXELS_AHEAD_ZOOM = PIXEL_MIN_ZOOM - 1.5;
let pendingPoints: GeoJSON.FeatureCollection | null = null;

function sendPendingPixels(): void {
  const points = pendingPoints;
  pendingPoints = null;
  // Con un incendio enfocado los píxeles son los suyos (focus.ts); al salir se llama a pushData.
  if (!points || state.focus) return;
  setData("pixels", detectionPixels(points));
}

function sendPixelsWhenIdle(): void {
  if (!pendingPoints) return;
  if (map.isMoving()) {
    map.once("idle", sendPixelsWhenIdle);
    return;
  }
  const send = (): void => {
    if (map.isMoving()) map.once("idle", sendPixelsWhenIdle);
    else sendPendingPixels();
  };
  // Safari no tiene requestIdleCallback.
  if ("requestIdleCallback" in window) window.requestIdleCallback(send, { timeout: 2000 });
  else setTimeout(send, 200);
}

/** Al acercarse al zoom de los píxeles, se envían ya si siguen pendientes. */
export function initPixelsOnDemand(): void {
  map.on("zoom", () => {
    if (pendingPoints && map.getZoom() >= PIXELS_AHEAD_ZOOM) sendPendingPixels();
  });
}

export function applyFilters(): void {
  if (!layersReady()) return;
  applyLayerFilters();
  drawActiveMarkers();
}

/** Envía al mapa lo último cargado del rango de fechas. */
export function pushData(): void {
  const points = detectionPoints(state.detections);
  setData("detections", points);
  setData("events", eventsLayer(state.events));
  pendingPoints = points;
  if (map.getZoom() >= PIXELS_AHEAD_ZOOM) sendPendingPixels();
  else {
    setData("pixels", EMPTY); // los del país o rango anterior ya no valen
    map.once("idle", sendPixelsWhenIdle);
  }
  applyYearOutlines();
  applyFilters();
}
