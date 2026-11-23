// Estilo del mapa base, de abajo arriba:
//
// 1. Color del océano: rellena lo que no cubre ninguna imagen (los polos, fuera de Web Mercator).
// 2. El mundo entero en una sola imagen incluida en la web (Blue Marble a zoom 3, 1024 × 1024 px,
//    ~150 KB, en Web Mercator). Llega con la página y no depende de nadie, así que al girar el
//    globo nunca quedan huecos: mientras bajan las teselas con detalle se ve esta versión algo más
//    borrosa. Para regenerarla: unir las teselas de GIBS de zoom 3 (8 × 8) y reducirlas a 1024 px.
// 3. NASA Blue Marble con relieve y batimetría (GIBS, público y sin clave): la Tierra vista desde
//    el espacio. Solo llega a zoom 8.
// 4. Al acercarse, entre DETAIL_FADE_START y DETAIL_FADE_END, se funde encima un mapa vectorial de
//    OpenFreeMap (datos de OpenStreetMap, sin límite de uso ni clave): agua, bosques, carreteras y
//    nombres de pueblos para situar los incendios. Al ser vectorial se dibuja nítido a cualquier
//    zoom y sin cuadros que cargan. El estilo es propio y oscuro (el de OpenFreeMap tiene ~110
//    capas y fondo blanco): menos capas que dibujar y los incendios destacan.
import type {
  ExpressionSpecification,
  LayerSpecification,
  StyleSpecification,
} from "maplibre-gl";

import BLUE_MARBLE_WORLD from "../assets/blueMarbleWorld.jpg";

const GIBS_BLUE_MARBLE =
  "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/" +
  "default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg";
const OPENFREEMAP_TILES = "https://tiles.openfreemap.org/planet";
const OPENFREEMAP_GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const DETAIL_FADE_START = 8;
const DETAIL_FADE_END = 9.5;
/** Latitud límite de Web Mercator: la imagen del mundo cubre de -MERCATOR_MAX_LAT a +MERCATOR_MAX_LAT. */
const MERCATOR_MAX_LAT = 85.051129;
/** Color del océano de Blue Marble: rellena los casquetes polares, que Web Mercator no cubre. */
const OCEAN_COLOR = "#0d244c";

/** Colores del mapa de detalle. */
const DETAIL = {
  land: "#25302a",
  wood: "#2b4231",
  grass: "#2f3a2c",
  farmland: "#303629",
  urban: "#383b3d",
  water: "#12304f",
  waterway: "#1f4f7a",
  boundary: "#a9b3ad",
  motorway: "#c9a66b",
  primary: "#9c927c",
  minor: "#5f655f",
  text: "#e8ece6",
  textHalo: "#121a15",
  waterText: "#8fb4d9",
};
const FONT = { regular: ["Noto Sans Regular"], bold: ["Noto Sans Bold"], italic: ["Noto Sans Italic"] };

/** Opacidad que sube de 0 a `to` durante el fundido con Blue Marble. */
const fadeIn = (to = 1): ExpressionSpecification => [
  "interpolate",
  ["linear"],
  ["zoom"],
  DETAIL_FADE_START,
  0,
  DETAIL_FADE_END,
  to,
];
const byClass = (...classes: string[]): ExpressionSpecification => [
  "match",
  ["get", "class"],
  classes,
  true,
  false,
];
const isLine: ExpressionSpecification = ["==", ["geometry-type"], "LineString"];
const name: ExpressionSpecification = ["coalesce", ["get", "name"], ["get", "name:latin"], ""];
const textPaint = {
  "text-color": DETAIL.text,
  "text-halo-color": DETAIL.textHalo,
  "text-halo-width": 1.4,
  "text-opacity": fadeIn(),
};

/** Capas del mapa de detalle (OpenMapTiles), todas con el fundido de entrada. */
const DETAIL_LAYERS: LayerSpecification[] = [
  // Fondo de tierra: una capa `background` en medio de la pila tapa Blue Marble al acercarse.
  {
    id: "detail-land",
    type: "background",
    minzoom: DETAIL_FADE_START,
    paint: { "background-color": DETAIL.land, "background-opacity": fadeIn() },
  },
  {
    id: "detail-landcover",
    type: "fill",
    source: "detail",
    "source-layer": "landcover",
    minzoom: DETAIL_FADE_START,
    filter: byClass("wood", "grass", "farmland"),
    paint: {
      "fill-color": [
        "match",
        ["get", "class"],
        "wood",
        DETAIL.wood,
        "farmland",
        DETAIL.farmland,
        DETAIL.grass,
      ],
      "fill-opacity": fadeIn(0.9),
    },
  },
  {
    id: "detail-urban",
    type: "fill",
    source: "detail",
    "source-layer": "landuse",
    minzoom: DETAIL_FADE_START,
    filter: byClass("residential", "industrial", "commercial", "retail"),
    paint: { "fill-color": DETAIL.urban, "fill-opacity": fadeIn(0.8) },
  },
  {
    id: "detail-water",
    type: "fill",
    source: "detail",
    "source-layer": "water",
    minzoom: DETAIL_FADE_START,
    paint: { "fill-color": DETAIL.water, "fill-opacity": fadeIn() },
  },
  {
    id: "detail-waterway",
    type: "line",
    source: "detail",
    "source-layer": "waterway",
    minzoom: DETAIL_FADE_START,
    paint: {
      "line-color": DETAIL.waterway,
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.6, 14, 1.8],
      "line-opacity": fadeIn(),
    },
  },
  {
    id: "detail-boundary",
    type: "line",
    source: "detail",
    "source-layer": "boundary",
    minzoom: DETAIL_FADE_START,
    filter: ["all", ["<=", ["get", "admin_level"], 6], ["!=", ["get", "maritime"], 1]],
    paint: {
      "line-color": DETAIL.boundary,
      "line-width": ["case", ["<=", ["get", "admin_level"], 4], 1.1, 0.7],
      "line-dasharray": [3, 2],
      "line-opacity": fadeIn(0.55),
    },
  },
  {
    id: "detail-roads-minor",
    type: "line",
    source: "detail",
    "source-layer": "transportation",
    minzoom: 11,
    filter: ["all", isLine, byClass("tertiary", "minor")],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": DETAIL.minor,
      "line-width": ["interpolate", ["linear"], ["zoom"], 11, 0.5, 15, 2.5],
    },
  },
  {
    id: "detail-roads-primary",
    type: "line",
    source: "detail",
    "source-layer": "transportation",
    minzoom: DETAIL_FADE_START,
    filter: ["all", isLine, byClass("primary", "secondary")],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": DETAIL.primary,
      "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 15, 4],
      "line-opacity": fadeIn(),
    },
  },
  {
    id: "detail-roads-motorway",
    type: "line",
    source: "detail",
    "source-layer": "transportation",
    minzoom: DETAIL_FADE_START,
    filter: ["all", isLine, byClass("motorway", "trunk")],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": DETAIL.motorway,
      "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.8, 15, 5],
      "line-opacity": fadeIn(),
    },
  },
  {
    id: "detail-water-names",
    type: "symbol",
    source: "detail",
    "source-layer": "water_name",
    minzoom: 10,
    layout: { "text-field": name, "text-font": FONT.italic, "text-size": 11 },
    paint: { ...textPaint, "text-color": DETAIL.waterText },
  },
  {
    id: "detail-road-names",
    type: "symbol",
    source: "detail",
    "source-layer": "transportation_name",
    minzoom: 13,
    filter: byClass("motorway", "trunk", "primary", "secondary"),
    layout: {
      "symbol-placement": "line",
      "text-field": ["coalesce", ["get", "ref"], name],
      "text-font": FONT.regular,
      "text-size": 10.5,
    },
    paint: textPaint,
  },
  {
    id: "detail-places",
    type: "symbol",
    source: "detail",
    "source-layer": "place",
    minzoom: DETAIL_FADE_START,
    filter: [
      "any",
      byClass("city", "town"),
      ["all", byClass("village"), [">=", ["zoom"], 10]],
      ["all", byClass("hamlet"), [">=", ["zoom"], 12]],
    ],
    layout: {
      "text-field": name,
      "text-font": [
        "match",
        ["get", "class"],
        ["city", "town"],
        ["literal", FONT.bold],
        ["literal", FONT.regular],
      ],
      "text-size": ["match", ["get", "class"], "city", 15, "town", 13, "village", 11.5, 10.5],
      "symbol-sort-key": ["match", ["get", "class"], "city", 0, "town", 1, "village", 2, 3],
      "text-max-width": 8,
    },
    paint: textPaint,
  },
];

export const BASE_STYLE: StyleSpecification = {
  version: 8,
  glyphs: OPENFREEMAP_GLYPHS,
  sources: {
    world: {
      type: "image",
      url: BLUE_MARBLE_WORLD,
      coordinates: [
        [-180, MERCATOR_MAX_LAT],
        [180, MERCATOR_MAX_LAT],
        [180, -MERCATOR_MAX_LAT],
        [-180, -MERCATOR_MAX_LAT],
      ],
    },
    earth: {
      type: "raster",
      tiles: [GIBS_BLUE_MARBLE],
      tileSize: 256,
      maxzoom: 8,
      attribution: "Imagery: NASA Blue Marble (GIBS)",
    },
    detail: {
      type: "vector",
      url: OPENFREEMAP_TILES,
      attribution:
        '<a href="https://openfreemap.org">OpenFreeMap</a> &copy; OpenMapTiles ' +
        "&copy; OpenStreetMap contributors · Borders: Natural Earth",
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
    { id: "ocean", type: "background", paint: { "background-color": OCEAN_COLOR } },
    {
      id: "world",
      type: "raster",
      source: "world",
      maxzoom: DETAIL_FADE_END + 0.5,
      paint: { "raster-fade-duration": 0 },
    },
    { id: "earth", type: "raster", source: "earth", maxzoom: DETAIL_FADE_END + 0.5 },
    ...DETAIL_LAYERS,
  ],
};
