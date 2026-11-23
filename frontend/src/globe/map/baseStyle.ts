// Estilo del mapa base. Desde el espacio, la Tierra real: NASA Blue Marble con relieve y
// batimetría (GIBS, público y sin clave). Esas imágenes solo llegan a zoom 8, así que al acercarse
// se funden con OpenStreetMap (pueblos y carreteras para situar los incendios) entre
// OSM_FADE_START y OSM_FADE_END.
import type { StyleSpecification } from "maplibre-gl";

const GIBS_BLUE_MARBLE =
  "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/" +
  "default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg";
const OSM_FADE_START = 5.5;
const OSM_FADE_END = 7;

export const BASE_STYLE: StyleSpecification = {
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
