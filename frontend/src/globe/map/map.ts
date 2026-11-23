// El mapa: MapLibre GL con proyección de globo. Se ve como un planeta al alejarse y pasa a mapa
// plano al acercarse. Dibuja con WebGL, así que maneja decenas de miles de detecciones sin tirones.
import * as maplibregl from "maplibre-gl";
// MapLibre busca su Web Worker junto a su propio fichero, pero Vite lo reempaqueta en otra
// carpeta y el worker no se encuentra (sin worker no se dibuja ninguna capa GeoJSON). Con `?url`
// Vite sirve y copia al build el fichero del worker, y se le indica a MapLibre dónde está.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";

import { COUNTRY_MAX_ZOOM, INITIAL_VIEW } from "../config.js";
import { BASE_STYLE } from "./baseStyle.js";

// Máximo alejamiento: el globo entero ocupando el alto (o el ancho) de la ventana, medido con el
// centro a esta latitud (Europa). En el globo de MapLibre el tamaño a un mismo zoom crece con la
// latitud, así que el zoom mínimo se calcula midiendo el globo, no con un número fijo.
const MIN_ZOOM_REFERENCE_LAT = 35;
const GLOBE_FILL = 0.96;
// Densidad de píxeles máxima del lienzo. En pantallas HiDPI/4K el lienzo a resolución nativa
// multiplica el trabajo de WebGL (el globo se redibuja entero en cada fotograma) sin que se note
// en unas imágenes de satélite que ya vienen ampliadas.
const MAX_PIXEL_RATIO = 1.5;

maplibregl.setWorkerUrl(workerUrl);

export const map = new maplibregl.Map({
  container: "globe",
  style: BASE_STYLE,
  center: INITIAL_VIEW.center,
  zoom: INITIAL_VIEW.zoom,
  attributionControl: { compact: true },
  pixelRatio: Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO),
  boxZoom: false, // Mayús fija tooltips y Ctrl selecciona zona: el zoom por recuadro sobra
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
map.on("style.load", () => map.setProjection({ type: "globe" }));
// Mientras el mapa se mueve, el CSS pausa las animaciones que hay sobre él (pulso de los incendios
// activos): así el navegador no las repinta a la vez que el mapa en cada fotograma.
map.on("movestart", () => map.getContainer().classList.add("map-moving"));
map.on("moveend", () => map.getContainer().classList.remove("map-moving"));
// Se crea ya para no perder el evento si llega antes de que lo esperemos.
export const mapLoaded = new Promise<void>((resolve) => map.once("load", () => resolve()));

/** Vista actual (para volver a ella después). */
export function currentView(): { center: maplibregl.LngLat; zoom: number; bearing: number; pitch: number } {
  return {
    center: map.getCenter(),
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
  };
}

/** Radio en píxeles del globo dibujado: hasta dónde, desde el centro, hay Tierra en pantalla. */
function globeRadiusPx(): number {
  const canvas = map.getCanvas();
  const [cx, cy] = [canvas.clientWidth / 2, canvas.clientHeight / 2];
  // Fuera del globo, ida y vuelta (unproject + project) ya no devuelve el mismo píxel.
  const onEarth = (x: number): boolean => Math.abs(map.project(map.unproject([x, cy])).x - x) <= 1;
  let [inside, outside] = [cx, cx * 4];
  for (let i = 0; i < 20; i += 1) {
    const middle = (inside + outside) / 2;
    if (onEarth(middle)) inside = middle;
    else outside = middle;
  }
  return inside - cx;
}

/**
 * Fija el zoom mínimo en el que el globo entero cabe en la ventana (no se puede alejar más).
 * Mide el globo en saltos síncronos (no llegan a pintarse) y devuelve la vista tal como estaba.
 */
export function updateMinZoom(): void {
  const view = currentView();
  const canvas = map.getCanvas();
  const target = (GLOBE_FILL * Math.min(canvas.clientWidth, canvas.clientHeight)) / 2;
  if (!(target > 0)) return; // lienzo aún sin tamaño: se medirá al redimensionar
  map.setMinZoom(null);
  let zoom = 1.5;
  for (let i = 0; i < 4; i += 1) {
    map.jumpTo({ center: [view.center.lng, MIN_ZOOM_REFERENCE_LAT], zoom, bearing: 0, pitch: 0 });
    const step = Math.log2(target / Math.max(globeRadiusPx(), 1));
    if (!Number.isFinite(step)) break;
    zoom = Math.min(Math.max(zoom + step, 0), COUNTRY_MAX_ZOOM);
  }
  map.jumpTo(view);
  // Entre 0 y el zoom de un país: una medición rara nunca impide acercarse o alejarse.
  map.setMinZoom(Math.min(Math.max(zoom, 0), COUNTRY_MAX_ZOOM));
}
