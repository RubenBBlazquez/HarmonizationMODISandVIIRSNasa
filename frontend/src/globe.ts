// Mapa principal (index.html): el globo con los incendios del país elegido y todas las
// herramientas (leyenda, comparar años, avance día a día, selección de zona, alertas, actividad).
//
// MapLibre GL con proyección de globo: se ve como un planeta al alejarse y pasa a mapa plano al
// acercarse. Dibuja con WebGL, así que maneja decenas de miles de detecciones sin tirones. Los
// datos del rango de fechas se descargan una vez por país (/api/detections y /api/events con
// ?country=) y lo que oculta la leyenda, los años o el avance se aplica como filtro de las capas,
// sin volver a enviar los datos al mapa.
import * as maplibregl from "maplibre-gl";
import type {
  ExpressionSpecification,
  FilterSpecification,
  GeoJSONSource,
  MapGeoJSONFeature,
  StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre busca su Web Worker junto a su propio fichero, pero Vite lo reempaqueta en otra
// carpeta y el worker no se encuentra (sin worker no se dibuja ninguna capa GeoJSON). Con `?url`
// Vite sirve y copia al build el fichero del worker, y se le indica a MapLibre dónde está.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";

import "./globe.css";
import "./mapControls.css";
import { initActivityPanel } from "./activity.js";
import { DateRangePicker, addDays } from "./dateRange.js";
import {
  MAX_CARDS,
  dateRangeLabel,
  historyLoadingHtml,
  openModal,
  renderHistory,
  setShowOnGlobe,
  suspendModals,
} from "./fireCards.js";
import {
  CONFIDENCE_COLORS,
  EVENT_COLORS,
  FRP_COLORS,
  HISTORY_YEARS,
  MODIS_PIXEL_BORDER_OPACITY,
  MODIS_PIXEL_FILL_OPACITY,
  NO_CATEGORY_COLOR,
  PIXEL_BORDER_COLOR,
  PIXEL_FILL_OPACITY,
  detectionPopup,
  detectionTooltip,
  LAND_COVER_KEYS,
  SEVERITY_HELP,
  SEVERITY_ORDER,
  freshnessClass,
  formatDate,
  timeAgoLabel,
  landCoverKeys,
  confidenceLabel,
  detectionsLabel,
  escapeHtml,
  landCoverLabel,
  severityLabel,
  eventPopup,
  fetchJson,
  firesPageUrl,
  formatDay,
  formatDayMonth,
  formatNumber,
  pixelHalfSizeDegrees,
  sortByConfidence,
  yearColor,
  type FireCollection,
  type EventDetail,
  type FireFeature,
  type HistoryResponse,
  type Properties,
} from "./shared.js";

maplibregl.setWorkerUrl(workerUrl);

// --- Constantes ---------------------------------------------------------------------------------
// Países que descarga el ETL (download.country en backend/etls/config/default.yaml). En el resto
// solo hay, como mucho, detecciones sueltas de la frontera.
const COVERED_COUNTRIES = new Set(["ESP"]);
// País con el que se abre la página (sin #CÓDIGO en la URL): ya encuadrado y con sus incendios.
const DEFAULT_COUNTRY = "ESP";
const WORLD_VIEW = { center: [-3.7, 30] as [number, number] }; // zoom: el mínimo (globo entero)
// Vista mientras cargan las fronteras: ya sobre España, para no ver antes el mundo entero.
const INITIAL_VIEW = { center: [-3.7, 40.2] as [number, number], zoom: 5 };
const COUNTRY_MAX_ZOOM = 6;
// Máximo alejamiento: el globo entero ocupando el alto (o el ancho) de la ventana, medido con el
// centro a esta latitud (Europa). En el globo de MapLibre el tamaño a un mismo zoom crece con la
// latitud, así que el zoom mínimo se calcula midiendo el globo, no con un número fijo.
const MIN_ZOOM_REFERENCE_LAT = 35;
const GLOBE_FILL = 0.96;
// Se precarga todo el rango de fechas del país de una vez: al mover o acercar el mapa no se pide
// nada más. Un año de España son ~60 000 detecciones.
const PRELOAD_LIMIT = 150000;
const REFRESH_DELAY_MS = 300;
const DEFAULT_RANGE_DAYS = 365;
const RANGE_STORAGE_KEY = "fire-map:date-range";
// Desde este zoom las detecciones se dibujan como su píxel (área ancho × alto); por debajo, puntos.
const PIXEL_MIN_ZOOM = 10;
const ALERT_ZOOM = 10;
const PLAY_STEP_MS = 1000;

// Leyenda interactiva: cada elemento se puede ocultar/mostrar pulsándolo.
const LEGEND_ITEMS = [
  { key: "frp:low", label: "Low FRP detection", color: FRP_COLORS.low },
  { key: "frp:medium", label: "Medium FRP detection", color: FRP_COLORS.medium },
  { key: "frp:high", label: "High FRP detection", color: FRP_COLORS.high },
  { key: "status:active", label: "Active event", color: EVENT_COLORS.active },
  { key: "status:closed", label: "Closed event", color: EVENT_COLORS.closed },
];

const SELECT_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/>
  <path d="M12 8v8M8 12h8"/></svg>`;
// Móvil: en lugar de la tecla Ctrl, un icono de dedo pulsando (mantener pulsado el mapa).
const HOLD_BADGE = `<span class="hold-badge" title="Or press and hold the map, then drag" aria-label="Press and hold the map">
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V11"/>
    <path d="M12 10.5a1.5 1.5 0 0 1 3 0V12a1.5 1.5 0 0 1 3 0v3.5a5.5 5.5 0 0 1-5.5 5.5h-1A5.5 5.5 0 0 1 7 16.5L5.6 13.4a1.4 1.4 0 0 1 2.3-1.5L9 13"/></svg></span>`;
const PLAY_ICON = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"
  aria-hidden="true"><path d="M7 4l13 8-13 8z"/></svg>`;
const STOP_ICON = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"
  aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>`;

// --- Tipos --------------------------------------------------------------------------------------
interface CountryProperties {
  code: string;
  name: string;
  continent: string | null;
  has_data: boolean;
}
type CountryCollection = GeoJSON.FeatureCollection<GeoJSON.Geometry, CountryProperties>;

interface YearlyRow {
  year: number;
  events: number;
  detections: number;
  frp_max_mw: number | null;
}

interface Summary {
  first_detection: string | null;
  last_detection: string | null;
}

interface Bounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

// --- Estilo del mapa base -----------------------------------------------------------------------
// Desde el espacio, la Tierra real: NASA Blue Marble con relieve y batimetría (GIBS, público y
// sin clave). Esas imágenes solo llegan a zoom 8, así que al acercarse se funden con
// OpenStreetMap (pueblos y carreteras para situar los incendios) entre OSM_FADE_START y _END.
const GIBS_BLUE_MARBLE =
  "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/" +
  "default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg";
const OSM_FADE_START = 5.5;
const OSM_FADE_END = 7;

const BASE_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    earth: {
      type: "raster",
      tiles: [GIBS_BLUE_MARBLE],
      tileSize: 256,
      maxzoom: 8,
      attribution: "Imagery: NASA Blue Marble (GIBS)",
    },
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "&copy; OpenStreetMap contributors · Borders: Natural Earth",
    },
  },
  // Atmósfera: halo azul alrededor del globo, que se desvanece al acercarse.
  sky: {
    "sky-color": "#0b1d3a",
    "horizon-color": "#7fb2ff",
    "fog-color": "#cfe3ff",
    "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0],
  },
  layers: [
    { id: "earth", type: "raster", source: "earth", maxzoom: OSM_FADE_END + 1 },
    {
      id: "osm",
      type: "raster",
      source: "osm",
      minzoom: OSM_FADE_START,
      paint: {
        "raster-opacity": ["interpolate", ["linear"], ["zoom"], OSM_FADE_START, 0, OSM_FADE_END, 1],
      },
    },
  ],
};

const frpColor: ExpressionSpecification = [
  "match",
  ["get", "frp_category"],
  "low",
  FRP_COLORS.low,
  "medium",
  FRP_COLORS.medium,
  "high",
  FRP_COLORS.high,
  NO_CATEGORY_COLOR,
];
const isModis: ExpressionSpecification = ["==", ["get", "source"], "MODIS"];
const selectedState: ExpressionSpecification = ["boolean", ["feature-state", "selected"], false];
const hoveredState: ExpressionSpecification = ["boolean", ["feature-state", "hover"], false];
const hasData: ExpressionSpecification = ["boolean", ["get", "has_data"], false];
const pointStroke: ExpressionSpecification = ["interpolate", ["linear"], ["zoom"], 4, 0, 7, 0.5];

const DETECTION_LAYERS = ["detection-pixels", "detection-points"];
const EVENT_LAYER = "events-closed";
const COUNTRY_LAYER = "countries-fill";

// --- Mapa -------------------------------------------------------------------------------------
const map = new maplibregl.Map({
  container: "globe",
  style: BASE_STYLE,
  center: INITIAL_VIEW.center,
  zoom: INITIAL_VIEW.zoom,
  attributionControl: { compact: true },
  boxZoom: false, // Mayús fija tooltips y Ctrl selecciona zona: el zoom por recuadro sobra
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
map.on("style.load", () => map.setProjection({ type: "globe" }));
// Se crea ya para no perder el evento si llega antes de que lo esperemos.
const mapLoaded = new Promise<void>((resolve) => map.once("load", () => resolve()));

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
function updateMinZoom(): void {
  const view = {
    center: map.getCenter(),
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
  };
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

// --- Elementos de la página --------------------------------------------------------------------
const $ = <T extends HTMLElement>(selector: string): T => document.querySelector<T>(selector)!;
const countryName = $<HTMLElement>("#country-name");
const countryNote = $<HTMLElement>("#country-note");
const panel = $<HTMLElement>(".globe-panel");
const worldButton = $<HTMLButtonElement>("#world-btn");
const eventsToggle = $<HTMLInputElement>("#layer-events");
const detectionsToggle = $<HTMLInputElement>("#layer-detections");
const allYearsToggle = $<HTMLInputElement>("#all-years-months");
const allYearsHint = $<HTMLElement>("#all-years-hint");
const yearsLegend = $<HTMLElement>("#years-legend");
const playButton = $<HTMLButtonElement>("#play-btn");
const selectButton = $<HTMLButtonElement>("#select-area");
const statusElement = $<HTMLElement>("#map-status");
const legendElement = $<HTMLElement>("#legend");
const coordinatesElement = $<HTMLElement>("#coordinates");
const alertsToggle = $<HTMLButtonElement>(".alerts-toggle");
const alertsList = $<HTMLElement>(".alerts-list");

// Indicador de carga (barra del panel y spinner del estado): se enciende al pedir datos y se
// apaga cuando el mapa termina de pintarlos (evento "idle"), no solo cuando llega la respuesta.
// Cada carga tiene su número: si llega otra más nueva, la antigua no apaga el indicador.
let busyToken = 0;
// Pantalla de carga inicial (index.html): se retira cuando el mapa está pintado (ver `start`) o con
// la primera carga terminada; como red de seguridad, a los 12 s para no dejarla puesta si algo falla.
function hideSplash(): void {
  if (document.body.classList.contains("app-ready")) return;
  document.body.classList.add("app-ready");
  const splash = document.querySelector<HTMLElement>("#splash");
  splash?.classList.add("done");
  window.setTimeout(() => splash?.remove(), 800);
}
const SPLASH_MAX_WAIT_MS = 3000;
window.setTimeout(hideSplash, 12_000); // red de seguridad si el arranque falla

function setBusy(busy: boolean, text?: string): number {
  if (!busy) hideSplash();
  busyToken += 1;
  panel.classList.toggle("busy", busy);
  panel.setAttribute("aria-busy", String(busy));
  statusElement.classList.toggle("loading", busy);
  if (text) statusElement.textContent = text;
  return busyToken;
}
function clearBusyWhenDrawn(token: number): void {
  const clear = (): void => {
    if (token === busyToken) setBusy(false);
  };
  if (map.loaded()) map.once("idle", clear);
  else clear();
}

// Los clics y la rueda sobre los paneles no llegan al mapa.
for (const element of [panel, legendElement, $<HTMLElement>(".alerts-control")]) {
  for (const type of ["mousedown", "click", "dblclick", "wheel"]) {
    element.addEventListener(type, (event) => event.stopPropagation());
  }
}

// --- Estado -------------------------------------------------------------------------------------
let countries: CountryCollection | null = null;
let selectedCode: string | null = null;
// Lo que se oculta desde la leyenda se filtra en el cliente, sin volver a pedir datos.
// Por defecto los eventos cerrados están ocultos: se activan desde la leyenda.
const hiddenKeys = new Set<string>(["status:closed"]);
let lastEvents: FireCollection | null = null;
let lastDetections: FireCollection | null = null;
let lastAllYears = false; // la última carga fue de las mismas fechas en todos los años
let lastYearly: YearlyRow[] = [];
let lastYearlyKey = "";
// Con la reproducción activa solo se ve lo ocurrido hasta `playDay` (acumulado).
let playDay: string | null = null;
// Incendio concreto enfocado desde el histórico ("Ver en el globo"): mientras dura, las capas
// muestran solo ese incendio y el rango de fechas cargado queda en memoria, sin pintar.
interface Focus {
  previousView: { center: maplibregl.LngLat; zoom: number; bearing: number; pitch: number };
  modals: ReturnType<typeof suspendModals>;
  /** Llegó por la URL (?fire=) desde la pestaña de incendios: "Volver" regresa a esa página. */
  fromUrl: boolean;
  /** Pasadas del satélite (hora de adquisición) y detecciones acumuladas hasta cada una. */
  timeline: { time: string; total: number }[];
}
let focus: Focus | null = null;

// --- Rango de fechas (recordado entre visitas) ------------------------------------------------
function saveRange(value: { start: string; end: string } | null): void {
  try {
    if (value) localStorage.setItem(RANGE_STORAGE_KEY, JSON.stringify(value));
    else localStorage.removeItem(RANGE_STORAGE_KEY);
  } catch {
    // localStorage no disponible: simplemente no se recuerda.
  }
}

function loadRange(): { start: string; end: string } | null {
  try {
    const value = JSON.parse(localStorage.getItem(RANGE_STORAGE_KEY) ?? "null");
    const valid = (day: unknown): boolean =>
      typeof day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(day);
    return value && valid(value.start) && valid(value.end) && value.start <= value.end
      ? value
      : null;
  } catch {
    return null;
  }
}

// Un único selector para el rango: primer clic, día inicial; segundo clic, día final.
// Último día con datos (de /summary): el rango por defecto es el año que acaba en él.
let lastDataDay: string | null = null;
const defaultRange = (): { start: string; end: string } | null =>
  lastDataDay ? { start: addDays(lastDataDay, -(DEFAULT_RANGE_DAYS - 1)), end: lastDataDay } : null;

const picker = new DateRangePicker($<HTMLElement>("#date-range"), (chosen) => {
  stopPlayback();
  // Nunca sin rango (sería todo el histórico): si se queda vacío, vuelve al último año.
  const value = chosen ?? defaultRange();
  if (!chosen && value) picker.setRange(value);
  saveRange(value);
  updateMonthsHint();
  scheduleRefresh();
});
// Móvil: al terminar de elegir fechas ("Done" o un atajo como "Last week") se oculta el panel.
$<HTMLElement>("#date-range").addEventListener("click", (event) => {
  const target = event.target as HTMLElement;
  if (isMobile() && target.closest("[data-done], [data-preset]")) setPanelOpen(false);
});
const rangeStart = (): string => picker.value?.start ?? "";
const rangeEnd = (): string => picker.value?.end ?? "";

function updateMonthsHint(): void {
  const start = picker.value?.start;
  const end = picker.value?.end;
  allYearsHint.textContent =
    start && end
      ? `Same dates (${formatDayMonth(start)} → ${formatDayMonth(end)}) in each year`
      : "Same dates of the range in each year";
}
updateMonthsHint();

// --- Capas --------------------------------------------------------------------------------------
function addLayers(): void {
  map.addSource("countries", { type: "geojson", data: EMPTY, promoteId: "code" });
  map.addSource("detections", { type: "geojson", data: EMPTY });
  map.addSource("pixels", { type: "geojson", data: EMPTY });
  map.addSource("events", { type: "geojson", data: EMPTY });
  map.addSource("selection", { type: "geojson", data: EMPTY });

  map.addLayer({
    id: COUNTRY_LAYER,
    type: "fill",
    source: "countries",
    paint: {
      "fill-color": ["case", hasData, "#e2603a", "#ffffff"],
      "fill-opacity": [
        "case",
        selectedState,
        0.03,
        hoveredState,
        ["case", hasData, 0.4, 0.18],
        hasData,
        0.22,
        0,
      ],
    },
  });
  map.addLayer({
    id: "countries-line",
    type: "line",
    source: "countries",
    paint: {
      "line-color": ["case", selectedState, "#ff7a4d", "#ffffff"],
      "line-width": ["case", selectedState, 2.4, 0.6],
      "line-opacity": ["case", selectedState, 1, 0.45],
    },
  });

  // Eventos cerrados: círculo del tamaño de su FRP (los activos van como marcador, encima).
  map.addLayer({
    id: EVENT_LAYER,
    type: "circle",
    source: "events",
    paint: {
      "circle-radius": [
        "min",
        14,
        ["+", 6, ["/", ["sqrt", ["max", 0, ["to-number", ["get", "frp_max_mw"], 0]]], 2]],
      ],
      "circle-color": EVENT_COLORS.closed,
      "circle-opacity": 0.25,
      "circle-stroke-color": EVENT_COLORS.closed,
      "circle-stroke-width": 2,
    },
  });

  // Píxeles (zoom alto): VIIRS opaco con borde marcado y MODIS transparente y de fondo.
  // `_order` mantiene el orden de pintado (MODIS debajo, más confianza y FRP encima).
  map.addLayer({
    id: "detection-pixels",
    type: "fill",
    source: "pixels",
    minzoom: PIXEL_MIN_ZOOM,
    layout: { "fill-sort-key": ["get", "_order"] },
    paint: {
      "fill-color": frpColor,
      "fill-opacity": ["case", isModis, MODIS_PIXEL_FILL_OPACITY, PIXEL_FILL_OPACITY],
    },
  });
  map.addLayer({
    id: "detection-pixel-borders",
    type: "line",
    source: "pixels",
    minzoom: PIXEL_MIN_ZOOM,
    layout: { "line-sort-key": ["get", "_order"] },
    paint: {
      "line-color": PIXEL_BORDER_COLOR,
      "line-width": ["case", isModis, 1, 1.5],
      "line-opacity": ["case", isModis, MODIS_PIXEL_BORDER_OPACITY, 1],
    },
  });
  // Puntos (zoom bajo): crecen al acercarse.
  map.addLayer({
    id: "detection-points",
    type: "circle",
    source: "detections",
    maxzoom: PIXEL_MIN_ZOOM,
    layout: { "circle-sort-key": ["get", "_order"] },
    paint: {
      "circle-color": frpColor,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 1.4, 5, 2.6, 8, 4, 10, 5],
      "circle-opacity": 0.85,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": pointStroke,
    },
  });

  // Contorno del incendio enfocado: halo naranja y línea discontinua blanca.
  map.addSource("focus", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "focus-glow",
    type: "line",
    source: "focus",
    paint: { "line-color": "#e2603a", "line-width": 7, "line-blur": 4, "line-opacity": 0.55 },
  });
  map.addLayer({
    id: "focus-hull",
    type: "line",
    source: "focus",
    paint: { "line-color": "#ffffff", "line-width": 2.2, "line-dasharray": [2, 1.5] },
  });

  // Recuadro de la selección de zona.
  map.addLayer({
    id: "selection-fill",
    type: "fill",
    source: "selection",
    paint: { "fill-color": "#e2603a", "fill-opacity": 0.3 },
  });
  map.addLayer({
    id: "selection-line",
    type: "line",
    source: "selection",
    paint: { "line-color": "#e2603a", "line-width": 2 },
  });
}

function setData(source: string, data: GeoJSON.FeatureCollection): void {
  map.getSource<GeoJSONSource>(source)?.setData(data);
}

/** Colores de contorno: el del año en "comparar con todos los años"; si no, los normales. */
function applyYearOutlines(): void {
  if (!map.getLayer("detection-points")) return;
  const yearOutline: ExpressionSpecification = ["get", "_yc"];
  const lastAllYears = byYear(); // en el incendio enfocado, colores normales
  map.setPaintProperty("detection-points", "circle-stroke-color", lastAllYears ? yearOutline : "#ffffff");
  map.setPaintProperty("detection-points", "circle-stroke-width", lastAllYears ? 1.5 : pointStroke);
  map.setPaintProperty(
    "detection-pixel-borders",
    "line-color",
    lastAllYears ? yearOutline : PIXEL_BORDER_COLOR,
  );
  map.setPaintProperty(
    "detection-pixel-borders",
    "line-width",
    lastAllYears ? 2 : ["case", isModis, 1, 1.5],
  );
  map.setPaintProperty(
    "detection-pixel-borders",
    "line-opacity",
    lastAllYears ? 1 : ["case", isModis, MODIS_PIXEL_BORDER_OPACITY, 1],
  );
  map.setPaintProperty(EVENT_LAYER, "circle-stroke-color", lastAllYears ? yearOutline : EVENT_COLORS.closed);
  map.setPaintProperty(EVENT_LAYER, "circle-stroke-width", lastAllYears ? 3 : 2);
}

// --- Datos → capas ----------------------------------------------------------------------------
/** Propiedades auxiliares para filtrar y pintar sin recalcular: año, día y color del año. */
function withHelpers(properties: Properties, dateField: string, order: number): Properties {
  const date = String(properties[dateField] ?? "");
  const year = Number(date.slice(0, 4));
  return {
    ...properties,
    _order: order,
    _year: date.slice(0, 4),
    _lcs: `|${landCoverKeys(properties.land_cover).join("|")}|`, // "|crops|trees|"
    _day: date.slice(0, 10) || "9999-12-31",
    _t: date || "9999-12-31T23:59:59Z", // hora exacta: avance pasada a pasada de un incendio
    _yc: year ? yearColor(year) : NO_CATEGORY_COLOR,
  };
}

/** Puntos ordenados para pintarlos y sus píxeles (polígonos ancho × alto). */
function detectionLayers(collection: FireCollection | null): {
  points: GeoJSON.FeatureCollection;
  pixels: GeoJSON.FeatureCollection;
} {
  if (!collection) return { points: EMPTY, pixels: EMPTY };
  const points: GeoJSON.Feature[] = [];
  const pixels: GeoJSON.Feature[] = [];
  sortByConfidence(collection).features.forEach((feature, order) => {
    if (feature.geometry.type !== "Point") return;
    const [lng, lat] = feature.geometry.coordinates;
    const properties = withHelpers(feature.properties, "acq_datetime", order);
    points.push({ type: "Feature", geometry: feature.geometry, properties });
    const [halfWidth, halfHeight] = pixelHalfSizeDegrees(lat, feature.properties);
    const [west, east, south, north] = [lng - halfWidth, lng + halfWidth, lat - halfHeight, lat + halfHeight];
    pixels.push({
      type: "Feature",
      properties,
      geometry: {
        type: "Polygon",
        coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
      },
    });
  });
  return {
    points: { type: "FeatureCollection", features: points },
    pixels: { type: "FeatureCollection", features: pixels },
  };
}

function eventsLayer(collection: FireCollection | null): GeoJSON.FeatureCollection {
  if (!collection) return EMPTY;
  return {
    type: "FeatureCollection",
    features: collection.features.map((feature, order) => ({
      ...feature,
      properties: withHelpers(feature.properties, "first_detection", order),
    })),
  };
}

/** Envía al mapa lo último cargado (solo al cambiar los datos; el resto son filtros). */
function pushData(): void {
  const layers = detectionLayers(lastDetections);
  setData("detections", layers.points);
  setData("pixels", layers.pixels);
  setData("events", eventsLayer(lastEvents));
  applyYearOutlines();
  applyFilters();
}

// --- Filtros: leyenda, años y avance día a día -------------------------------------------------
const hiddenOf = (prefix: string): string[] =>
  [...hiddenKeys].filter((key) => key.startsWith(prefix)).map((key) => key.slice(prefix.length));

/** Los años y el avance día a día no se aplican al incendio enfocado. */
const byYear = (): boolean => lastAllYears && !focus;

function visibilityFilter(extra: FilterSpecification[]): FilterSpecification {
  const conditions: FilterSpecification[] = [...extra];
  const hiddenYears = hiddenOf("year:");
  if (byYear() && hiddenYears.length) {
    conditions.push(["!", ["in", ["get", "_year"], ["literal", hiddenYears]]]);
  }
  // Una parcela con varias clases se oculta solo cuando todas sus clases están ocultas.
  const hiddenCovers = hiddenOf("lc:");
  if (hiddenCovers.length && !focus) {
    const visible = LAND_COVER_KEYS.filter((item) => !hiddenCovers.includes(item.key));
    conditions.push([
      "any",
      ...visible.map((item) => ["in", `|${item.key}|`, ["get", "_lcs"]]),
    ] as FilterSpecification);
  }
  if (playDay && !focus) conditions.push(["<=", ["get", "_day"], playDay]);
  if (playTime && focus) conditions.push(["<=", ["get", "_t"], playTime]);
  return ["all", ...conditions] as FilterSpecification;
}

function applyFilters(): void {
  if (!map.getLayer("detection-points")) return;
  const detections = visibilityFilter([
    ["!", ["in", ["coalesce", ["get", "frp_category"], ""], ["literal", hiddenOf("frp:")]]],
  ]);
  for (const layer of ["detection-points", "detection-pixels", "detection-pixel-borders"]) {
    map.setFilter(layer, detections);
  }
  // Los activos van como marcadores; la capa solo dibuja los cerrados (si no están ocultos).
  map.setFilter(
    EVENT_LAYER,
    hiddenKeys.has("status:closed")
      ? ["==", ["get", "status"], "__none__"]
      : visibilityFilter([["!=", ["get", "status"], "active"]]),
  );
  drawActiveMarkers();
}

// Incendios activos: marcador con pulso, como en las alertas.
let activeMarkers: maplibregl.Marker[] = [];

const isVisibleByDate = (feature: FireFeature, field: string): boolean =>
  !playDay || String(feature.properties[field] ?? "9999").slice(0, 10) <= playDay;
const isVisibleYear = (feature: FireFeature, field: string): boolean =>
  !lastAllYears || !hiddenKeys.has(`year:${String(feature.properties[field] ?? "").slice(0, 4)}`);

function confidenceColor(value: unknown): string {
  return CONFIDENCE_COLORS[String(value)] ?? NO_CATEGORY_COLOR;
}

function eventPopupHtml(properties: Properties, point: GeoJSON.Position | null): string {
  const report =
    properties.status === "active" && point
      ? `<br><a class="google-maps-report" target="_blank" rel="noopener noreferrer"
          href="https://www.google.com/maps/search/?api=1&query=${point[1]},${point[0]}">
          Report on Google Maps</a>`
      : "";
  return `${eventPopup(properties)}${report}`;
}

function drawActiveMarkers(): void {
  for (const marker of activeMarkers) marker.remove();
  activeMarkers = [];
  if (focus) return; // con un incendio enfocado no se muestran los del rango
  const active = (lastEvents?.features ?? []).filter(
    (feature) =>
      feature.properties.status === "active" &&
      isVisibleByDate(feature, "first_detection") &&
      landCoverKeys(feature.properties.land_cover).some((key) => !hiddenKeys.has(`lc:${key}`)),
  );
  updateAlerts(active);
  if (hiddenKeys.has("status:active")) return;
  for (const feature of active) {
    if (feature.geometry.type !== "Point" || !isVisibleYear(feature, "first_detection")) continue;
    const point = feature.geometry.coordinates;
    const color = confidenceColor(feature.properties.confidence);
    const element = document.createElement("div");
    element.className = "active-zone-marker";
    element.innerHTML = `<span class="active-zone-pulse" style="--alert-color:${color}"></span>
      <span class="active-zone-icon" style="--alert-color:${color}" aria-label="Active zone">!</span>`;
    const popup = new maplibregl.Popup({ maxWidth: "320px", offset: 20 }).setHTML(
      eventPopupHtml(feature.properties, point),
    );
    activeMarkers.push(
      new maplibregl.Marker({ element }).setLngLat([point[0], point[1]]).setPopup(popup).addTo(map),
    );
  }
}

// --- Alertas activas ----------------------------------------------------------------------------
alertsToggle.addEventListener("click", () => {
  const open = alertsList.hidden;
  alertsList.hidden = !open;
  alertsToggle.setAttribute("aria-expanded", String(open));
});

/** "37.12° N, 4.55° W" para una posición [lng, lat]. */
function coordinatesLabel(position: GeoJSON.Position): string {
  const [lng, lat] = position;
  return `${Math.abs(lat).toFixed(2)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lng).toFixed(2)}° ${lng >= 0 ? "E" : "W"}`;
}

type AlertSort = "recent" | "severity";
let alertsSort: AlertSort = "recent"; // lo urgente es lo último que se ha visto
let lastAlerts: FireFeature[] = [];

/** "recent": última detección primero; "severity": la más grave primero. En ambos, el otro criterio desempata. */
function sortAlerts(features: FireFeature[], mode: AlertSort): FireFeature[] {
  const rank = (feature: FireFeature): number =>
    SEVERITY_ORDER.indexOf(String(feature.properties.severity) as (typeof SEVERITY_ORDER)[number]);
  const recency = (feature: FireFeature): string => String(feature.properties.last_detection ?? "");
  const byRecency = (a: FireFeature, b: FireFeature): number => recency(b).localeCompare(recency(a));
  const bySeverity = (a: FireFeature, b: FireFeature): number => rank(b) - rank(a);
  return [...features].sort(mode === "recent" ? (a, b) => byRecency(a, b) || bySeverity(a, b) : (a, b) => bySeverity(a, b) || byRecency(a, b));
}

function alertItemHtml(feature: FireFeature, index: number): string {
  const p = feature.properties;
  const place = feature.geometry.type === "Point" ? coordinatesLabel(feature.geometry.coordinates) : "";
  const severity = String(p.severity ?? "none");
  return `<button type="button" class="alert-item" data-index="${index}">
    <span class="severity-pill sev-${escapeHtml(severity)}" title="${escapeHtml(SEVERITY_HELP)}">${escapeHtml(severityLabel(p.severity))}</span>
    <span class="alert-body"><b>${escapeHtml(landCoverLabel(p.land_cover))}</b>
      <small>${detectionsLabel(p.n_detections)} · max FRP ${escapeHtml(p.frp_max_mw)} MW</small>
      <small>${place} · started ${formatDay(p.first_detection)}</small></span>
    <span class="alert-time ${freshnessClass(p.last_detection)}" title="Last seen ${escapeHtml(formatDate(p.last_detection))}">
      <b>${escapeHtml(timeAgoLabel(p.last_detection))}</b><small>last seen</small></span>
  </button>`;
}

function renderAlerts(): void {
  const active = sortAlerts(lastAlerts, alertsSort);
  alertsToggle.querySelector<HTMLElement>(".alerts-count")!.textContent = String(active.length);
  alertsToggle.classList.toggle("has-alerts", active.length > 0);
  const tab = (mode: AlertSort, label: string): string =>
    `<button type="button" class="alerts-tab" data-sort="${mode}" aria-pressed="${alertsSort === mode}">${label}</button>`;
  alertsList.innerHTML = active.length
    ? `<div class="alerts-tabs" role="group" aria-label="Sort alerts">${tab("recent", "Most recent")}${tab("severity", "Most severe")}</div>` +
      active.map(alertItemHtml).join("")
    : `<div class="alerts-empty">No active zones</div>`;
  alertsList.querySelectorAll<HTMLButtonElement>(".alerts-tab").forEach((button) =>
    button.addEventListener("click", () => {
      alertsSort = button.dataset.sort as AlertSort;
      renderAlerts();
    }),
  );
  alertsList.querySelectorAll<HTMLButtonElement>(".alert-item").forEach((button) => {
    button.addEventListener("click", () => {
      const geometry = active[Number(button.dataset.index)].geometry;
      if (geometry.type === "Point") {
        map.flyTo({ center: [geometry.coordinates[0], geometry.coordinates[1]], zoom: ALERT_ZOOM });
        if (isMobile()) {
          alertsList.hidden = true; // móvil: deja ver el mapa tras elegir
          alertsToggle.setAttribute("aria-expanded", "false");
        }
      }
    });
  });
}

function updateAlerts(features: FireFeature[]): void {
  lastAlerts = features;
  renderAlerts();
}

// --- Leyenda interactiva ------------------------------------------------------------------------
legendElement.innerHTML = `<details class="legend-all" open><summary>Legend</summary>
  <button type="button" class="legend-close" data-legend-close aria-label="Hide legend">✕</button>${LEGEND_ITEMS.map(
  (item) => `<button type="button" class="legend-item" data-key="${item.key}"
    aria-pressed="true" title="Click to hide or show">
    <i style="--c:${item.color}"></i>${item.label}</button>`,
).join("")}
  <details class="legend-group" open>
    <summary>Land type (parcel)</summary>
    ${LAND_COVER_KEYS.map(
      (item) => `<button type="button" class="legend-item" data-key="lc:${item.key}"
        aria-pressed="true" title="Click to hide or show">
        <i style="--c:${item.color}"></i>${item.label}</button>`,
    ).join("")}
  </details>
  <button type="button" class="legend-reset" data-reset hidden>Show all</button>
  <div class="legend-note">Click an item to hide or show it.<br>
    Zoom ≥ ${PIXEL_MIN_ZOOM}: detection area (width × height, km)</div></details>`;
// Móvil = ancho de 650 px o menos. Se consulta en cada acción (no solo al cargar) para que cambiar
// el tamaño de la ventana o girar el dispositivo no deje activo el comportamiento equivocado.
const mobileQuery = window.matchMedia("(max-width: 650px)");
const isMobile = (): boolean => mobileQuery.matches;
// Móvil: la leyenda se abre con su botón (icono de capas) y se cierra con la ✕; abrirla cierra la
// lista de alertas y al revés, para que no se pisen. En escritorio está siempre visible.
const legendFab = document.querySelector<HTMLButtonElement>("#legend-fab")!;
function setLegendOpen(open: boolean): void {
  legendElement.classList.toggle("legend-hidden", isMobile() && !open);
  legendFab.setAttribute("aria-expanded", String(open));
  if (open && isMobile()) {
    alertsList.hidden = true;
    alertsToggle.setAttribute("aria-expanded", "false");
  }
}
legendFab.addEventListener("click", () => setLegendOpen(legendElement.classList.contains("legend-hidden")));
legendElement.querySelector("[data-legend-close]")!.addEventListener("click", () => setLegendOpen(false));
alertsToggle.addEventListener("click", () => {
  if (isMobile() && !alertsList.hidden) setLegendOpen(false);
});
for (const type of ["mousedown", "click", "dblclick", "wheel"]) {
  legendFab.addEventListener(type, (event) => event.stopPropagation());
}
const filtersFab = document.querySelector<HTMLButtonElement>("#filters-fab")!;
/** Muestra u oculta el panel de filtros; el botón del embudo aparece cuando está oculto. */
function setPanelOpen(open: boolean): void {
  panel.classList.toggle("panel-hidden", !open);
  filtersFab.hidden = open;
}
/** Disposición inicial de cada modo: escritorio con panel y leyenda a la vista; móvil, ocultos. */
function applyLayoutMode(): void {
  if (isMobile()) {
    legendElement.querySelector<HTMLDetailsElement>(".legend-all")!.open = true; // se cierra entero
    setLegendOpen(false);
    setPanelOpen(false);
  } else {
    legendElement.classList.remove("legend-hidden");
    setPanelOpen(true);
  }
}
applyLayoutMode();
mobileQuery.addEventListener("change", applyLayoutMode);
filtersFab.addEventListener("click", () => setPanelOpen(true));
document.querySelector<HTMLButtonElement>("#panel-close")!.addEventListener("click", () => setPanelOpen(false));
for (const type of ["mousedown", "click", "dblclick", "wheel"]) {
  filtersFab.addEventListener(type, (event) => event.stopPropagation());
}

function syncLegend(): void {
  legendElement.querySelectorAll<HTMLButtonElement>("button.legend-item").forEach((button) => {
    button.setAttribute("aria-pressed", String(!hiddenKeys.has(button.dataset.key ?? "")));
  });
  legendElement.querySelector<HTMLButtonElement>(".legend-reset")!.hidden = hiddenKeys.size === 0;
}
syncLegend();

legendElement.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button");
  if (!button) return;
  if (button.dataset.reset !== undefined) {
    hiddenKeys.clear();
    updateYearsLegend(); // los chips de años reflejan que vuelven a estar todos activos
  } else if (button.dataset.key) {
    const key = button.dataset.key;
    if (hiddenKeys.has(key)) hiddenKeys.delete(key);
    else hiddenKeys.add(key);
  }
  syncLegend();
  applyFilters();
  if (playDay === null) updateStatus();
});

// --- Comparar con todos los años --------------------------------------------------------------
function updateYearsLegend(): void {
  yearsLegend.hidden = !lastAllYears || lastYearly.length === 0;
  if (yearsLegend.hidden) return;
  // Por defecto todos los años están activos; cada uno se oculta pulsando su chip.
  yearsLegend.querySelector<HTMLElement>(".years-chips")!.innerHTML = lastYearly
    .map(
      (row) => `<button type="button" class="year-chip" data-year="${row.year}"
        aria-pressed="${!hiddenKeys.has(`year:${row.year}`)}"
        title="${row.year}: ${formatNumber(row.events)} fires (events) and ${formatNumber(row.detections)} satellite detections in the country">
        <i style="--c:${yearColor(row.year)}"></i><b>${row.year}</b>
        <span>${formatNumber(row.events)} events · ${formatNumber(row.detections)} detections</span></button>`,
    )
    .join("");
}

yearsLegend.addEventListener("click", (event) => {
  const chip = (event.target as HTMLElement).closest<HTMLButtonElement>("button.year-chip");
  if (!chip) return;
  const key = `year:${chip.dataset.year}`;
  if (hiddenKeys.has(key)) hiddenKeys.delete(key);
  else hiddenKeys.add(key);
  chip.setAttribute("aria-pressed", String(!hiddenKeys.has(key)));
  syncLegend();
  applyFilters();
});

allYearsToggle.addEventListener("change", () => {
  // Al entrar o salir del modo se olvida la selección de años: vuelven a estar todos activos.
  for (const key of [...hiddenKeys]) if (key.startsWith("year:")) hiddenKeys.delete(key);
  syncLegend();
  scheduleRefresh();
});

// Contadores por año del país: los cachea la API y solo se piden al cambiar rango o país. Van
// aparte de los datos del mapa para no retrasarlos.
let yearlyRequested = "";
async function loadYearly(start: string, end: string, country: string): Promise<void> {
  const key = `${start}|${end}|${country}`;
  if (key === lastYearlyKey || key === yearlyRequested) return;
  yearlyRequested = key;
  try {
    const data = await fetchJson<{ years: YearlyRow[] }>("/yearly", { start, end, country });
    if (yearlyRequested !== key) return; // llegó tarde: ya se pidió otro rango
    lastYearly = data.years;
    lastYearlyKey = key;
    if (lastAllYears) {
      updateYearsLegend();
      if (playDay === null) updateStatus();
    }
  } catch {
    yearlyRequested = ""; // se reintentará en la siguiente carga
  }
}

// --- Estado (línea de texto bajo los controles) -------------------------------------------------
function describe(label: string, collection: FireCollection | null): string | null {
  if (!collection) return null;
  const shown = formatNumber(collection.count);
  return collection.truncated
    ? `${label}: ${shown} of ${formatNumber(collection.total)} (narrow the date range)`
    : `${label}: ${shown}`;
}

const FOCUS_STATUS =
  "Viewing a single fire. Remove it to go back to the fires of the date range.";

function updateStatus(): void {
  if (focus) {
    statusElement.textContent = FOCUS_STATUS;
    return;
  }
  if (lastAllYears) {
    const key = `${rangeStart()}|${rangeEnd()}|${selectedCode}`;
    if (lastYearlyKey !== key) {
      statusElement.textContent = "Loading the per-year counts…";
      return;
    }
    const total = (field: "events" | "detections"): number =>
      lastYearly.reduce((sum, row) => sum + row[field], 0);
    statusElement.textContent =
      `${lastYearly.length} years · ${formatNumber(total("events"))} events · ` +
      `${formatNumber(total("detections"))} detections (per-year detail above)`;
    return;
  }
  const parts = [describe("Events", lastEvents), describe("Detections", lastDetections)];
  let text = parts.filter(Boolean).join(" · ") || "No active layers";
  if (hiddenKeys.size > 0) text += " · some items are hidden (legend)";
  if (lastDetections && map.getZoom() < PIXEL_MIN_ZOOM) {
    text += ` · zoom the map in (zoom ${PIXEL_MIN_ZOOM}) to see the pixel`;
  }
  statusElement.textContent = text;
}

// --- Carga de datos: todo el rango de fechas del país de una vez --------------------------------
let controller: AbortController | null = null;
let timer: number | undefined;
// Filtros con los que se cargó cada capa: si no cambian, se reutiliza lo ya descargado.
const loaded: Record<"events" | "detections", { key: string; data: FireCollection } | null> = {
  events: null,
  detections: null,
};

async function refresh(): Promise<void> {
  if (!selectedCode) return;
  controller?.abort();
  controller = new AbortController();
  const { signal } = controller;
  const params: Record<string, string> = { limit: String(PRELOAD_LIMIT), country: selectedCode };
  if (rangeStart()) params.start = rangeStart();
  if (rangeEnd()) params.end = rangeEnd();
  // Mismas fechas del rango en todos los años: lo resuelve la API (necesita start y end).
  const allYears = allYearsToggle.checked && Boolean(rangeStart() && rangeEnd());
  if (allYears) params.all_years = "true";

  const key = JSON.stringify(params);
  // Lo descargado se guarda aunque la capa se apague, para no pedirlo otra vez al encenderla.
  const load = async (
    layer: "events" | "detections",
    enabled: boolean,
  ): Promise<FireCollection | null> => {
    if (!enabled) return null;
    const cached = loaded[layer];
    if (cached?.key === key) return cached.data;
    const data = await fetchJson<FireCollection>(`/${layer}`, params, signal);
    loaded[layer] = { key, data };
    return data;
  };

  const token = setBusy(
    true,
    allYears ? "Loading the same dates of every year…" : "Loading the fires…",
  );
  try {
    if (allYears) void loadYearly(params.start, params.end, selectedCode); // no bloquea el mapa
    const [events, detections] = await Promise.all([
      load("events", eventsToggle.checked),
      load("detections", detectionsToggle.checked),
    ]);
    [lastEvents, lastDetections, lastAllYears] = [events, detections, allYears];
    updateYearsLegend();
    if (focus) {
      // Se pintará al quitar el incendio enfocado (queda en memoria).
      if (token === busyToken) setBusy(false);
      return;
    }
    statusElement.textContent = "Drawing the fires on the map…";
    // Deja pintar el aviso antes de preparar las capas (con decenas de miles tarda un poco).
    await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));
    if (signal.aborted) return;
    pushData();
    if (playDay === null) updateStatus();
    clearBusyWhenDrawn(token);
  } catch (error) {
    // Cancelada por una carga más nueva: el indicador sigue hasta que acabe esa.
    if ((error as Error).name === "AbortError") return;
    setBusy(false, "Could not load the data. Is the API running?");
  }
}

function scheduleRefresh(): void {
  if (focus) exitFocus("remove", false); // el usuario cambia el rango: vuelve a verse el rango
  window.clearTimeout(timer);
  timer = window.setTimeout(() => void refresh(), REFRESH_DELAY_MS);
}

for (const input of [eventsToggle, detectionsToggle]) {
  input.addEventListener("change", scheduleRefresh);
}

// El aviso de "acerca el mapa" depende del zoom.
let wasPixels = false;
map.on("zoomend", () => {
  const pixels = map.getZoom() >= PIXEL_MIN_ZOOM;
  if (pixels !== wasPixels && playDay === null) updateStatus();
  wasPixels = pixels;
});

// --- Reproducción ----------------------------------------------------------------------------------
// Con el rango de fechas: un día por segundo. Con un incendio enfocado ("Ver en el globo"): pasada a
// pasada del satélite, porque la mayoría duran horas o pocos días (un día por paso apenas daría
// uno o dos fotogramas). En ambos casos se filtran los datos ya cargados, sin pedir más.
let playTimer: number | undefined;
// Hora (ISO) hasta la que se ve el incendio enfocado durante su avance.
let playTime: string | null = null;
const FOCUS_STEP_MS = 900;
const PASS_TIME = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

function resetPlayButton(label?: string): void {
  playButton.classList.remove("playing");
  const text = label ?? (focus ? "Watch the fire progress" : "Watch day-by-day progress");
  playButton.innerHTML = `${PLAY_ICON}<span>${text}</span>`;
  playButton.title = focus
    ? "Shows how the fire grew, satellite pass by satellite pass"
    : "Shows day by day how the fires in the chosen range progress";
}
resetPlayButton();

function stopPlayback(): void {
  window.clearTimeout(playTimer);
  playTimer = undefined;
  if (playDay === null && playTime === null) return;
  playDay = null;
  playTime = null;
  resetPlayButton();
  applyFilters();
  updateStatus();
}

const countUpTo = (collection: FireCollection | null, field: string): number =>
  collection?.features.filter((feature) => isVisibleByDate(feature, field)).length ?? 0;

function startFocusPlayback(timeline: Focus["timeline"]): void {
  if (timeline.length === 0) return;
  playButton.classList.add("playing");
  playButton.innerHTML = `${STOP_ICON}<span>Detener</span>`;
  let index = 0;
  const step = (): void => {
    if (!focus || playButton.classList.contains("playing") === false) return;
    const { time, total } = timeline[index];
    playTime = time;
    applyFilters();
    const when = PASS_TIME.format(new Date(time)).replace(/\./g, "");
    statusElement.textContent =
      `Fire progress: pass ${index + 1} of ${timeline.length} · ${when} UTC · ` +
      `${formatNumber(total)} ${total === 1 ? "detection" : "detections"}`;
    if (index >= timeline.length - 1) {
      playTimer = undefined;
      resetPlayButton("Replay the fire progress");
      return; // se queda mostrando el incendio completo; pulsar de nuevo vuelve a empezar
    }
    index += 1;
    playTimer = window.setTimeout(step, FOCUS_STEP_MS);
  };
  step();
}

async function startPlayback(): Promise<void> {
  if (focus) {
    startFocusPlayback(focus.timeline);
    return;
  }
  if (!rangeStart() || !rangeEnd()) {
    statusElement.textContent = "Choose a date range to see the progress";
    return;
  }
  const first = rangeStart();
  const last = rangeEnd();
  playButton.classList.add("playing");
  playButton.innerHTML = `${STOP_ICON}<span>Detener</span>`;
  playDay = first;
  window.clearTimeout(timer);
  await refresh(); // carga de una vez todo el rango; los días se filtran sin pedir más datos
  if (playDay === null) return; // detenido mientras cargaba
  const step = (): void => {
    if (playDay === null) return;
    applyFilters();
    const shown =
      countUpTo(lastEvents, "first_detection") + countUpTo(lastDetections, "acq_datetime");
    statusElement.textContent =
      `Progress: ${formatDay(playDay)} (until ${formatDay(last)}) · ${formatNumber(shown)} items`;
    if (playDay >= last) {
      playTimer = undefined;
      resetPlayButton("Replay progress");
      return; // se queda mostrando el rango completo; pulsar de nuevo vuelve a empezar
    }
    playDay = addDays(playDay, 1);
    playTimer = window.setTimeout(step, PLAY_STEP_MS);
  };
  step();
}

playButton.addEventListener("click", () => {
  if (playButton.classList.contains("playing")) stopPlayback();
  else {
    window.clearTimeout(playTimer);
    playDay = null;
    playTime = null;
    void startPlayback();
  }
});

// --- Países: selección y encuadre --------------------------------------------------------------
function countryByCode(code: string): GeoJSON.Feature<GeoJSON.Geometry, CountryProperties> | null {
  return countries?.features.find((feature) => feature.properties.code === code) ?? null;
}

/**
 * Encuadre del país: el recuadro de su polígono mayor. Así Francia encuadra la metrópoli (y no
 * también la Guayana o Reunión) y Rusia o EE. UU. no se estiran al cruzar el antimeridiano.
 */
function countryBounds(geometry: GeoJSON.Geometry): maplibregl.LngLatBounds | null {
  const polygons =
    geometry.type === "Polygon"
      ? [geometry.coordinates]
      : geometry.type === "MultiPolygon"
        ? geometry.coordinates
        : [];
  let best: GeoJSON.Position[] | null = null;
  let bestArea = -1;
  for (const polygon of polygons) {
    const ring = polygon[0];
    let area = 0; // fórmula del área (shoelace) en grados²: basta para comparar
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      area += (ring[j][0] + ring[i][0]) * (ring[j][1] - ring[i][1]);
    }
    if (Math.abs(area) > bestArea) [best, bestArea] = [ring, Math.abs(area)];
  }
  if (!best) return null;
  const bounds = new maplibregl.LngLatBounds();
  for (const [lng, lat] of best) bounds.extend([lng, lat]);
  return bounds;
}

/** Margen del encuadre para que el país no quede debajo de los paneles. */
function viewPadding(): maplibregl.PaddingOptions {
  if (window.innerWidth <= 650) {
    // Móvil: el panel de filtros es una hoja superior y la actividad, una inferior.
    const activityOpen = document.body.classList.contains("activity-open");
    return { top: 70, right: 20, bottom: activityOpen ? window.innerHeight * 0.45 + 20 : 40, left: 20 };
  }
  const activityOpen = document.body.classList.contains("activity-open");
  return { top: 60, right: activityOpen ? 380 : 60, bottom: 40, left: panel.offsetWidth + 36 };
}

function fitCountry(code: string, animate: boolean): void {
  const country = countryByCode(code);
  const bounds = country && countryBounds(country.geometry);
  if (!bounds) return;
  map.fitBounds(bounds, {
    padding: viewPadding(),
    maxZoom: COUNTRY_MAX_ZOOM,
    duration: animate ? 1600 : 0,
  });
}

function selectCountry(code: string, animate = true): void {
  const country = countryByCode(code);
  if (!country) return;
  if (code === selectedCode) {
    // El mismo país: solo se vuelve a encuadrar si se está viendo el mundo entero.
    if (map.getZoom() < COUNTRY_MAX_ZOOM - 2) fitCountry(code, animate);
    return;
  }

  if (selectedCode) map.setFeatureState({ source: "countries", id: selectedCode }, { selected: false });
  selectedCode = code;
  map.setFeatureState({ source: "countries", id: code }, { selected: true });
  history.replaceState(null, "", `#${code}`);

  const { name, has_data } = country.properties;
  countryName.textContent = name;
  const covered = COVERED_COUNTRIES.has(code);
  countryNote.classList.toggle("warning", !covered);
  countryNote.textContent = covered
    ? "Fires detected by the MODIS and VIIRS satellites (NASA FIRMS)."
    : has_data
      ? "For now only fires in Spain are loaded: only stray detections near the " +
        "border appear here."
      : "For now only fires in Spain are loaded: this country has no data.";

  stopPlayback();
  lastYearlyKey = "";
  fitCountry(code, animate);
  scheduleRefresh();
}

worldButton.addEventListener("click", () => {
  map.flyTo({ center: WORLD_VIEW.center, zoom: map.getMinZoom(), duration: 1600 });
});

// --- Ratón: tooltip (fijable con Mayús), cursor y clics -----------------------------------------
const tip = new maplibregl.Popup({
  closeButton: false,
  closeOnClick: false,
  className: "globe-tip",
  offset: 12,
  maxWidth: "280px",
});
let tipContent: { html: string; lngLat: maplibregl.LngLat } | null = null;
const pinnedTips: maplibregl.Popup[] = [];
let hoveredCode: string | null = null;

function setHoveredCountry(code: string | null): void {
  if (code === hoveredCode) return;
  if (hoveredCode) map.setFeatureState({ source: "countries", id: hoveredCode }, { hover: false });
  hoveredCode = code;
  if (code) map.setFeatureState({ source: "countries", id: code }, { hover: true });
}

function hideTip(): void {
  tip.remove();
  tipContent = null;
}

/** Lo que hay bajo el cursor, por prioridad: evento, detección y país. */
function featureAt(point: maplibregl.PointLike): {
  kind: "event" | "detection" | "country";
  feature: MapGeoJSONFeature;
} | null {
  const layers = [EVENT_LAYER, ...DETECTION_LAYERS, COUNTRY_LAYER].filter((id) => map.getLayer(id));
  const [feature] = map.queryRenderedFeatures(point, { layers });
  if (!feature) return null;
  const layer = feature.layer.id;
  const kind = layer === EVENT_LAYER ? "event" : DETECTION_LAYERS.includes(layer) ? "detection" : "country";
  return { kind, feature };
}

map.on("mousemove", (event) => {
  coordinatesElement.innerHTML =
    `Latitude: <b>${event.lngLat.lat.toFixed(5)}°</b><br>` +
    `Longitude: <b>${event.lngLat.lng.toFixed(5)}°</b>`;
  if (selecting) return;
  const hit = featureAt(event.point);
  const code = hit?.kind === "country" ? String(hit.feature.properties.code) : null;
  const otherCountry = code !== null && code !== selectedCode;
  map.getCanvas().style.cursor = hit && (hit.kind !== "country" || otherCountry) ? "pointer" : "";
  setHoveredCountry(otherCountry ? code : null);
  const properties = hit?.feature.properties as Properties | undefined;
  const html =
    hit?.kind === "detection"
      ? detectionTooltip(properties!)
      : otherCountry
        ? `<b>${escapeHtml(properties!.name)}</b>${properties!.has_data ? " · has data" : ""}` +
          "<br>Click to see its fires"
        : null;
  if (!html) {
    hideTip();
    return;
  }
  tipContent = { html, lngLat: event.lngLat };
  tip.setLngLat(event.lngLat).setHTML(html).addTo(map);
});

map.on("mouseout", () => {
  hideTip();
  setHoveredCountry(null);
});

map.on("click", (event) => {
  if (selecting) return;
  const hit = featureAt(event.point);
  if (!hit) return;
  const properties = hit.feature.properties as Properties;
  if (hit.kind === "country") {
    selectCountry(String(properties.code));
    return;
  }
  hideTip();
  const geometry = hit.feature.geometry;
  const point = geometry.type === "Point" ? geometry.coordinates : null;
  new maplibregl.Popup({ maxWidth: "320px" })
    .setLngLat(event.lngLat)
    .setHTML(hit.kind === "detection" ? detectionPopup(properties) : eventPopupHtml(properties, point))
    .addTo(map);
});

// Mayús deja fijo el tooltip bajo el cursor; Esc quita los fijados.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    pinnedTips.splice(0).forEach((popup) => popup.remove());
  } else if (event.key === "Shift" && !event.repeat && tipContent) {
    pinnedTips.push(
      new maplibregl.Popup({
        closeButton: false,
        closeOnClick: false,
        className: "globe-tip pinned",
        offset: 12,
        maxWidth: "280px",
      })
        .setLngLat(tipContent.lngLat)
        .setHTML(tipContent.html)
        .addTo(map),
    );
  }
});

// --- Selección de zona: arrastrar un recuadro y elegir qué buscar en él -------------------------
let selecting = false;
let anchor: maplibregl.LngLat | null = null;
let menuPopup: maplibregl.Popup | null = null;
let selectedBounds: Bounds | null = null; // zona dibujada (sigue en el mapa aunque el menú se cierre)

function boundsOf(a: maplibregl.LngLat, b: maplibregl.LngLat): Bounds {
  return {
    west: Math.min(a.lng, b.lng),
    east: Math.max(a.lng, b.lng),
    south: Math.min(a.lat, b.lat),
    north: Math.max(a.lat, b.lat),
  };
}

function drawSelection(bounds: Bounds | null): void {
  if (!bounds) {
    setData("selection", EMPTY);
    return;
  }
  const { west, south, east, north } = bounds;
  setData("selection", {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: {},
        geometry: {
          type: "Polygon",
          coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
        },
      },
    ],
  });
}

function closeMenu(): void {
  const popup = menuPopup;
  menuPopup = null; // antes de remove(): su evento "close" no debe borrar la zona
  popup?.remove();
}

function clearSelection(): void {
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
  modal.body.innerHTML = historyLoadingHtml();
  const query = {
    bbox: [bounds.west, bounds.south, bounds.east, bounds.north].map((v) => v.toFixed(5)).join(","),
    start: rangeStart(),
    end: rangeEnd(),
    years: String(HISTORY_YEARS),
  };
  try {
    const data = await fetchJson<HistoryResponse>("/history", { ...query, limit: String(MAX_CARDS) });
    renderHistory(modal.body, data, {
      ...query,
      years: HISTORY_YEARS,
      tabUrl: firesPageUrl({ mode: "history", ...query }),
    });
  } catch (error) {
    modal.body.innerHTML = `<p class="history-empty">${escapeHtml((error as Error).message)}</p>`;
  }
}

function openSelectionMenu(bounds: Bounds): void {
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
  // La ✕ del menú quita también el recuadro (el menú es la zona seleccionada).
  popup.on("close", () => {
    if (menuPopup === popup) clearSelection();
  });
  menuPopup = popup;
}

// Tocar el recuadro de una zona sin menú (p. ej. tras cerrar el histórico) vuelve a abrirlo.
map.on("click", "selection-fill", () => {
  if (!selecting && !menuPopup && selectedBounds) openSelectionMenu(selectedBounds);
});

function setSelecting(active: boolean): void {
  selecting = active;
  anchor = null;
  selectButton.classList.toggle("active", active);
  selectButton.innerHTML = `${SELECT_ICON}<span>${
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

/** Aviso flotante en móvil mientras se dibuja la zona (el panel de filtros está oculto). */
let selectHint: HTMLElement | null = null;
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

setSelecting(false); // pinta el botón (icono + texto)
// Al pasar de móvil a escritorio (o al revés) cambia el distintivo del botón (dedo / Ctrl).
mobileQuery.addEventListener("change", () => {
  if (!selecting) setSelecting(false);
});
selectButton.addEventListener("click", () => {
  if (selecting && anchor) clearSelection(); // arrastre a medias: se descarta
  setSelecting(!selecting);
  if (selecting && isMobile()) setPanelOpen(false); // móvil: deja ver el mapa para dibujar la zona
});

// Con Ctrl pulsado el mapa selecciona zona sin pulsar el botón (al soltarlo, vuelve a arrastrar).
let ctrlSelecting = false;
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    // Esc cancela el modo selección y quita la zona seleccionada (recuadro y menú).
    if (selecting) setSelecting(false);
    clearSelection();
  }
  if (event.key === "Control" && !selecting) {
    ctrlSelecting = true;
    setSelecting(true);
  }
});
document.addEventListener("keyup", (event) => {
  if (event.key === "Control" && ctrlSelecting && !anchor) setSelecting(false);
  if (event.key === "Control") ctrlSelecting = false;
});
window.addEventListener("blur", () => {
  if (ctrlSelecting && !anchor) setSelecting(false);
  ctrlSelecting = false;
});

/** Posición en el mapa de un evento del ratón o del dedo de `document` (también fuera del lienzo). */
function lngLatOf(event: { clientX: number; clientY: number }): maplibregl.LngLat {
  const rect = map.getCanvasContainer().getBoundingClientRect();
  return map.unproject([event.clientX - rect.left, event.clientY - rect.top]);
}

map.on("mousedown", (event) => {
  if (!selecting) return;
  event.preventDefault();
  closeMenu(); // el menú de una selección anterior no debe quedar debajo del arrastre
  anchor = event.lngLat;
  drawSelection(boundsOf(anchor, anchor));
});
// El arrastre se sigue en `document`, no en el mapa: si el cursor pasa por un panel o se suelta
// fuera del mapa, el mapa no recibe el `mouseup` y la selección quedaría a medias.
document.addEventListener("mousemove", (event) => {
  if (!selecting || !anchor) return;
  drawSelection(boundsOf(anchor, lngLatOf(event)));
});
// Al soltar sobre una detección o un país, el navegador lanza además un `click` que abriría su
// popup o cambiaría de país. Ese clic se descarta.
let swallowClick = false;
map.getContainer().addEventListener(
  "click",
  (event) => {
    if (swallowClick) event.stopPropagation(); // en captura: MapLibre no llega a verlo
  },
  true,
);
/** Termina el arrastre en `end`: abre el menú de la zona o descarta un toque sin arrastre. */
function finishSelection(end: maplibregl.LngLat): void {
  if (!selecting || !anchor) return;
  swallowClick = true;
  window.setTimeout(() => (swallowClick = false), 100);
  const bounds = boundsOf(anchor, end);
  setSelecting(false);
  if (bounds.west === bounds.east || bounds.south === bounds.north) {
    clearSelection(); // un clic sin arrastrar no es una zona
    return;
  }
  drawSelection(bounds);
  openSelectionMenu(bounds);
}
document.addEventListener("mouseup", (event) => finishSelection(lngLatOf(event)));

// Táctil: mismo arrastre con un dedo. `touchmove` no es pasivo para que la página no se desplace.
// Pulsación larga (móvil): mantener el dedo 0,5 s sobre el mapa equivale a Ctrl en escritorio. Un
// círculo de progreso marca la espera; al cumplirse (vibración) el mapa deja de moverse y se sigue
// arrastrando para dibujar el recuadro. El pequeño temblor del dedo durante la espera no la
// cancela (solo un desplazamiento claro), y el recuadro empieza donde está el dedo en ese momento.
const LONG_PRESS_MS = 500;
const LONG_PRESS_MOVE_PX = 24; // más que esto es un desplazamiento normal del mapa
let pressTimer: number | undefined;
let pressOrigin: { x: number; y: number } | null = null;
let pressLast: { x: number; y: number } | null = null;
let pressRing: HTMLElement | null = null;
function cancelLongPress(): void {
  window.clearTimeout(pressTimer);
  pressTimer = undefined;
  pressOrigin = null;
  pressLast = null;
  pressRing?.remove();
  pressRing = null;
}
function showPressRing(clientX: number, clientY: number): void {
  pressRing = document.createElement("div");
  pressRing.className = "hold-ring";
  pressRing.style.left = `${clientX}px`;
  pressRing.style.top = `${clientY}px`;
  pressRing.style.setProperty("--hold-ms", `${LONG_PRESS_MS}ms`);
  document.body.append(pressRing);
}

map.on("touchstart", (event) => {
  if (selecting) {
    if (event.points.length !== 1) return;
    event.preventDefault();
    closeMenu();
    anchor = event.lngLat;
    drawSelection(boundsOf(anchor, anchor));
    return;
  }
  cancelLongPress();
  if (!isMobile() || event.points.length !== 1) return;
  pressOrigin = { x: event.point.x, y: event.point.y };
  pressLast = pressOrigin;
  const touch = (event.originalEvent as TouchEvent).touches[0];
  showPressRing(touch.clientX, touch.clientY);
  pressTimer = window.setTimeout(() => {
    const finger = pressLast ?? pressOrigin;
    cancelLongPress();
    if (!finger) return;
    navigator.vibrate?.(30); // aviso táctil de que ya se puede arrastrar
    closeMenu();
    setPanelOpen(false);
    setSelecting(true); // desactiva el arrastre del mapa: ya no se mueve
    anchor = map.unproject([finger.x, finger.y]); // donde está el dedo ahora, no donde empezó
    drawSelection(boundsOf(anchor, anchor));
  }, LONG_PRESS_MS);
});
map.on("touchmove", (event) => {
  if (!pressOrigin) return;
  pressLast = { x: event.point.x, y: event.point.y };
  const moved = Math.hypot(event.point.x - pressOrigin.x, event.point.y - pressOrigin.y);
  if (moved > LONG_PRESS_MOVE_PX || event.points.length !== 1) cancelLongPress();
});
for (const type of ["touchend", "touchcancel"] as const) map.on(type, cancelLongPress);
// El menú contextual del navegador al mantener pulsado taparía el gesto.
map.getContainer().addEventListener("contextmenu", (event) => {
  if (isMobile()) event.preventDefault();
});
// El arrastre del recuadro se atiende en captura sobre el contenedor del mapa y NO se deja llegar a
// MapLibre: así el mapa no se mueve aunque el dedo ya estuviera apoyado (pulsación larga) cuando
// empezó la selección. `touchmove` no es pasivo para poder cancelarlo.
map.getContainer().addEventListener(
  "touchmove",
  (event) => {
    if (!selecting || !anchor) return;
    event.preventDefault();
    event.stopPropagation();
    drawSelection(boundsOf(anchor, lngLatOf(event.touches[0])));
  },
  { capture: true, passive: false },
);
document.addEventListener("touchend", (event) => {
  if (selecting && anchor) finishSelection(lngLatOf(event.changedTouches[0]));
});
document.addEventListener("touchcancel", () => {
  if (selecting && anchor) {
    clearSelection();
    setSelecting(false);
  }
});

// --- Un incendio en el globo ("Ver en el globo" del histórico) -----------------------------------
const focusBar = $<HTMLElement>("#focus-bar");
const focusTitle = $<HTMLElement>("#focus-title");
const focusSub = $<HTMLElement>("#focus-sub");
const focusBack = $<HTMLButtonElement>("#focus-back");
const focusClose = $<HTMLButtonElement>("#focus-close");
const alertsControl = $<HTMLElement>(".alerts-control");
const FOCUS_DETECTIONS = 5000;
const FOCUS_MAX_ZOOM = 14;
// Qué hacer al volver atrás en el navegador (o con "Volver" / "Quitar", que también van atrás).
let pendingExit: "back" | "remove" = "back";

/** Centro de un píxel (polígono ancho × alto): media de sus esquinas. */
function pixelCenter(geometry: GeoJSON.Geometry): GeoJSON.Point | null {
  if (geometry.type === "Point") return geometry;
  if (geometry.type !== "Polygon") return null;
  const corners = geometry.coordinates[0].slice(0, -1);
  if (!corners.length) return null;
  const sum = corners.reduce(([x, y], [cx, cy]) => [x + cx, y + cy], [0, 0]);
  return { type: "Point", coordinates: [sum[0] / corners.length, sum[1] / corners.length] };
}

function boundsOfGeometry(geometry: GeoJSON.Geometry, bounds: maplibregl.LngLatBounds): void {
  const visit = (value: unknown): void => {
    if (Array.isArray(value) && typeof value[0] === "number") {
      bounds.extend([value[0] as number, value[1] as number]);
    } else if (Array.isArray(value)) value.forEach(visit);
  };
  if ("coordinates" in geometry) visit(geometry.coordinates);
}

async function focusFire(eventId: string, fromUrl = false): Promise<void> {
  const modals = fromUrl ? null : suspendModals();
  const token = setBusy(true, "Loading the fire…");
  let detail: EventDetail;
  try {
    detail = await fetchJson<EventDetail>(`/events/${encodeURIComponent(eventId)}`, {
      limit: String(FOCUS_DETECTIONS),
    });
  } catch (error) {
    setBusy(false, `Could not load the fire: ${(error as Error).message}`);
    modals?.resume();
    return;
  }

  stopPlayback();
  if (focus) {
    focus.modals?.discard(); // ya había otro enfocado: se sustituye
    focus.modals = modals;
  } else {
    focus = {
      previousView: {
        center: map.getCenter(),
        zoom: map.getZoom(),
        bearing: map.getBearing(),
        pitch: map.getPitch(),
      },
      modals,
      fromUrl,
      timeline: [],
    };
    if (!fromUrl) history.pushState({ siachiFocus: eventId }, "");
  }
  hideTip();
  clearSelection();

  // Píxeles (zoom alto) y puntos (zoom bajo) del incendio, con el mismo estilo que el rango.
  const sorted = sortByConfidence(detail.detections).features;
  // Pasadas (horas de adquisición distintas) y detecciones acumuladas hasta cada una.
  const perPass = new Map<string, number>();
  for (const feature of sorted) {
    const time = String(feature.properties.acq_datetime ?? "");
    if (time) perPass.set(time, (perPass.get(time) ?? 0) + 1);
  }
  let accumulated = 0;
  focus.timeline = [...perPass.keys()].sort().map((time) => {
    accumulated += perPass.get(time) ?? 0;
    return { time, total: accumulated };
  });
  resetPlayButton();
  const pixels: GeoJSON.Feature[] = [];
  const points: GeoJSON.Feature[] = [];
  sorted.forEach((feature, order) => {
    const properties = withHelpers(feature.properties, "acq_datetime", order);
    pixels.push({ type: "Feature", geometry: feature.geometry, properties });
    const center = pixelCenter(feature.geometry);
    if (center) points.push({ type: "Feature", geometry: center, properties });
  });
  setData("pixels", { type: "FeatureCollection", features: pixels });
  setData("detections", { type: "FeatureCollection", features: points });
  setData("events", EMPTY);
  setData("focus", { type: "FeatureCollection", features: [detail.event] });
  applyYearOutlines();
  applyFilters();

  // Barra superior con el incendio y cómo salir.
  const p = detail.event.properties;
  focusTitle.textContent = `Fire of ${dateRangeLabel(p.first_detection, p.last_detection)}`;
  focusSub.textContent =
    `${formatNumber(Number(p.n_detections ?? 0))} detections · max FRP ${p.frp_max_mw ?? "—"} MW` +
    (detail.detections.truncated ? ` · showing ${formatNumber(detail.detections.count)}` : "");
  focusBack.innerHTML = `<span aria-hidden="true">←</span> ${fromUrl ? "Back" : "Back to the history"}`;
  focusBar.hidden = false;
  alertsControl.hidden = true;
  statusElement.textContent = FOCUS_STATUS;

  const bounds = new maplibregl.LngLatBounds();
  boundsOfGeometry(detail.event.geometry, bounds);
  for (const feature of pixels) boundsOfGeometry(feature.geometry, bounds);
  if (!bounds.isEmpty()) {
    map.fitBounds(bounds, { padding: viewPadding(), maxZoom: FOCUS_MAX_ZOOM, duration: 2200 });
  }
  clearBusyWhenDrawn(token);
}

/**
 * Sale del incendio enfocado y vuelve a pintar el rango de fechas que ya estaba en memoria (sin
 * descargarlo otra vez). `back` reabre el histórico tal como estaba; `remove` lo cierra.
 */
function exitFocus(mode: "back" | "remove", restoreView = true): void {
  if (!focus) return;
  const previous = focus;
  window.clearTimeout(playTimer);
  playTimer = undefined;
  playTime = null;
  focus = null;
  resetPlayButton();
  setData("focus", EMPTY);
  focusBar.hidden = true;
  alertsControl.hidden = false;
  pushData();
  if (playDay === null) updateStatus();
  if (restoreView) map.easeTo({ ...previous.previousView, duration: 1400 });
  if (mode === "back") previous.modals?.resume();
  else previous.modals?.discard();
}

// "Volver" y "Quitar" van atrás en el historial del navegador (se entró con pushState), así el
// botón Atrás del navegador hace lo mismo que "Volver".
focusBack.addEventListener("click", () => {
  if (!focus) return;
  if (focus.fromUrl) {
    history.back(); // regresa a la pestaña de incendios
    return;
  }
  pendingExit = "back";
  history.back();
});
focusClose.addEventListener("click", () => {
  if (!focus) return;
  if (focus.fromUrl) {
    exitFocus("remove");
    history.replaceState(null, "", `${location.pathname}#${selectedCode ?? ""}`);
    return;
  }
  pendingExit = "remove";
  history.back();
});
window.addEventListener("popstate", () => {
  if (focus && !focus.fromUrl) exitFocus(pendingExit);
  pendingExit = "back";
});

setShowOnGlobe((eventId) => void focusFire(eventId));

// --- Actividad reciente -------------------------------------------------------------------------
initActivityPanel((lat, lon, zoom) => map.flyTo({ center: [lon, lat], zoom }));

// --- Arranque -----------------------------------------------------------------------------------
async function start(): Promise<void> {
  const [summary, borders] = await Promise.all([
    fetchJson<Summary>("/summary", {}).catch(() => null),
    fetchJson<CountryCollection>("/countries", {}).catch(() => null),
  ]);
  if (summary?.first_detection && summary.last_detection) {
    // Rango inicial: el último año con datos (o el último elegido).
    const last = summary.last_detection.slice(0, 10);
    picker.setBounds(summary.first_detection.slice(0, 10), last);
    lastDataDay = last;
    picker.setRange(loadRange() ?? defaultRange());
    updateMonthsHint();
  }
  if (!borders) {
    countryName.textContent = "No connection";
    setBusy(false, "Could not load the countries. Is the API running?");
    return;
  }
  countries = borders;
  await mapLoaded;
  updateMinZoom();
  window.addEventListener("resize", () => window.requestAnimationFrame(updateMinZoom));
  addLayers();
  setData("countries", borders);
  wasPixels = map.getZoom() >= PIXEL_MIN_ZOOM;

  // Un enlace con #PRT abre directamente ese país; sin él, el país por defecto. Sin animación:
  // la página se abre ya encuadrada.
  const fromHash = decodeURIComponent(location.hash.slice(1)).toUpperCase();
  selectCountry(fromHash && countryByCode(fromHash) ? fromHash : DEFAULT_COUNTRY, false);
  // La pantalla de carga se retira en cuanto el mapa está pintado (sin esperar a los datos de
  // incendios, que siguen cargándose con la barra del panel), con un tope por si no llega a quedar
  // inactivo (por ejemplo, teselas lentas).
  map.once("idle", hideSplash);
  window.setTimeout(hideSplash, SPLASH_MAX_WAIT_MS);

  // ?fire=ID (desde la pestaña de incendios): se abre ya enfocado en ese incendio.
  const fireId = new URLSearchParams(location.search).get("fire");
  if (fireId) void focusFire(fireId, true);
}

void start();
