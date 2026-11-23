// Fuentes y capas propias del globo (países, incendios, incendio enfocado y zona seleccionada) y
// su estilo. Los datos entran con `setData`; lo que se oculta se aplica con filtros (filters.ts).
import type { ExpressionSpecification, GeoJSONSource } from "maplibre-gl";

import {
  EVENT_COLORS,
  FRP_COLORS,
  MODIS_PIXEL_BORDER_OPACITY,
  MODIS_PIXEL_FILL_OPACITY,
  NO_CATEGORY_COLOR,
  PIXEL_BORDER_COLOR,
  PIXEL_FILL_OPACITY,
} from "../../domain/colors.js";
import { PIXEL_MIN_ZOOM } from "../config.js";
import { byYear } from "../state.js";
import { EMPTY } from "./geojson.js";
import { map } from "./map.js";

export type SourceId = "countries" | "detections" | "pixels" | "events" | "focus" | "selection";

export const LAYERS = {
  countries: "countries-fill",
  countryLines: "countries-line",
  /** Eventos cerrados (los activos van como marcador HTML, ver activeMarkers.ts). */
  events: "events-closed",
  pixels: "detection-pixels",
  pixelBorders: "detection-pixel-borders",
  points: "detection-points",
  focusGlow: "focus-glow",
  focusHull: "focus-hull",
  selectionFill: "selection-fill",
  selectionLine: "selection-line",
} as const;

/** Capas en las que se puede pulsar una detección. */
export const DETECTION_LAYERS: string[] = [LAYERS.pixels, LAYERS.points];

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

/** Crea las fuentes (vacías) y las capas, de abajo arriba. Se llama una vez, con el mapa cargado. */
export function addLayers(): void {
  map.addSource("countries", { type: "geojson", data: EMPTY, promoteId: "code" });
  map.addSource("detections", { type: "geojson", data: EMPTY });
  map.addSource("pixels", { type: "geojson", data: EMPTY });
  map.addSource("events", { type: "geojson", data: EMPTY });
  map.addSource("selection", { type: "geojson", data: EMPTY });

  map.addLayer({
    id: LAYERS.countries,
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
    id: LAYERS.countryLines,
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
    id: LAYERS.events,
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
    id: LAYERS.pixels,
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
    id: LAYERS.pixelBorders,
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
    id: LAYERS.points,
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
    id: LAYERS.focusGlow,
    type: "line",
    source: "focus",
    paint: { "line-color": "#e2603a", "line-width": 7, "line-blur": 4, "line-opacity": 0.55 },
  });
  map.addLayer({
    id: LAYERS.focusHull,
    type: "line",
    source: "focus",
    paint: { "line-color": "#ffffff", "line-width": 2.2, "line-dasharray": [2, 1.5] },
  });

  // Recuadro de la selección de zona.
  map.addLayer({
    id: LAYERS.selectionFill,
    type: "fill",
    source: "selection",
    paint: { "fill-color": "#e2603a", "fill-opacity": 0.3 },
  });
  map.addLayer({
    id: LAYERS.selectionLine,
    type: "line",
    source: "selection",
    paint: { "line-color": "#e2603a", "line-width": 2 },
  });
}

/** ¿Están ya creadas las capas? (antes de cargar el mapa no hay nada que filtrar ni pintar). */
export const layersReady = (): boolean => Boolean(map.getLayer(LAYERS.points));

export function setData(source: SourceId, data: GeoJSON.FeatureCollection): void {
  map.getSource<GeoJSONSource>(source)?.setData(data);
}

/** Colores de contorno: el del año en "comparar con todos los años"; si no, los normales. */
export function applyYearOutlines(): void {
  if (!layersReady()) return;
  const yearOutline: ExpressionSpecification = ["get", "_yc"];
  const allYears = byYear(); // en el incendio enfocado, colores normales
  map.setPaintProperty(LAYERS.points, "circle-stroke-color", allYears ? yearOutline : "#ffffff");
  map.setPaintProperty(LAYERS.points, "circle-stroke-width", allYears ? 1.5 : pointStroke);
  map.setPaintProperty(LAYERS.pixelBorders, "line-color", allYears ? yearOutline : PIXEL_BORDER_COLOR);
  map.setPaintProperty(LAYERS.pixelBorders, "line-width", allYears ? 2 : ["case", isModis, 1, 1.5]);
  map.setPaintProperty(
    LAYERS.pixelBorders,
    "line-opacity",
    allYears ? 1 : ["case", isModis, MODIS_PIXEL_BORDER_OPACITY, 1],
  );
  map.setPaintProperty(LAYERS.events, "circle-stroke-color", allYears ? yearOutline : EVENT_COLORS.closed);
  map.setPaintProperty(LAYERS.events, "circle-stroke-width", allYears ? 3 : 2);
}
