// Países: elegir uno (clic en el globo o #CÓDIGO en la URL), encuadrarlo y resaltarlo.
import * as maplibregl from "maplibre-gl";

import type { CountryFeature } from "../../api/types.js";
import { COUNTRY_MAX_ZOOM } from "../config.js";
import { ui } from "../dom.js";
import { map } from "../map/map.js";
import { state } from "../state.js";
import { viewPadding } from "../ui/layout.js";
import { scheduleRefresh } from "./dataLoader.js";
import { stopPlayback } from "./playback.js";

// Países que descarga el ETL (download.country en backend/etls/config/default.yaml). En el resto
// solo hay, como mucho, detecciones sueltas de la frontera.
const COVERED_COUNTRIES = new Set(["ESP"]);
/** País con el que se abre la página (sin #CÓDIGO en la URL): ya encuadrado y con sus incendios. */
export const DEFAULT_COUNTRY = "ESP";
const WORLD_VIEW = { center: [-3.7, 30] as [number, number] }; // zoom: el mínimo (globo entero)

let hoveredCode: string | null = null;

export function countryByCode(code: string): CountryFeature | null {
  return state.countries?.features.find((feature) => feature.properties.code === code) ?? null;
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

/** Resalta el país bajo el cursor (null: ninguno). */
export function setHoveredCountry(code: string | null): void {
  if (code === hoveredCode) return;
  if (hoveredCode) map.setFeatureState({ source: "countries", id: hoveredCode }, { hover: false });
  hoveredCode = code;
  if (code) map.setFeatureState({ source: "countries", id: code }, { hover: true });
}

/** Elige un país: lo encuadra, lo pone en la URL y carga sus incendios. */
export function selectCountry(code: string, animate = true): void {
  const country = countryByCode(code);
  if (!country) return;
  if (code === state.selectedCode) {
    // El mismo país: solo se vuelve a encuadrar si se está viendo el mundo entero.
    if (map.getZoom() < COUNTRY_MAX_ZOOM - 2) fitCountry(code, animate);
    return;
  }

  if (state.selectedCode) {
    map.setFeatureState({ source: "countries", id: state.selectedCode }, { selected: false });
  }
  state.selectedCode = code;
  map.setFeatureState({ source: "countries", id: code }, { selected: true });
  history.replaceState(null, "", `#${code}`);

  const { name, has_data } = country.properties;
  ui.countryName.textContent = name;
  const covered = COVERED_COUNTRIES.has(code);
  ui.countryNote.classList.toggle("warning", !covered);
  ui.countryNote.textContent = covered
    ? "Fires detected by the MODIS and VIIRS satellites (NASA FIRMS)."
    : has_data
      ? "For now only fires in Spain are loaded: only stray detections near the " +
        "border appear here."
      : "For now only fires in Spain are loaded: this country has no data.";

  stopPlayback();
  state.yearlyKey = "";
  fitCountry(code, animate);
  scheduleRefresh();
}

/** País de la URL (#PRT) si existe; si no, el país por defecto. */
export function initialCountry(): string {
  const fromHash = decodeURIComponent(location.hash.slice(1)).toUpperCase();
  return fromHash && countryByCode(fromHash) ? fromHash : DEFAULT_COUNTRY;
}

export function initCountries(): void {
  ui.worldButton.addEventListener("click", () => {
    map.flyTo({ center: WORLD_VIEW.center, zoom: map.getMinZoom(), duration: 1600 });
  });
}
