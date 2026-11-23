// Filtros de las capas: lo que oculta la leyenda, los años y la reproducción se aplica como filtro
// de MapLibre, sin volver a enviar los datos al mapa.
import type { FilterSpecification } from "maplibre-gl";

import { LAND_COVER_KEYS } from "../../domain/landCover.js";
import { byYear, hiddenOf, state } from "../state.js";
import { LAYERS, layersReady } from "./layers.js";
import { map } from "./map.js";

/** Condiciones comunes (años, tipo de suelo, reproducción) más las de `extra`. */
function visibilityFilter(extra: FilterSpecification[]): FilterSpecification {
  const conditions: FilterSpecification[] = [...extra];
  const hiddenYears = hiddenOf("year:");
  if (byYear() && hiddenYears.length) {
    conditions.push(["!", ["in", ["get", "_year"], ["literal", hiddenYears]]]);
  }
  // Una parcela con varias clases se oculta solo cuando todas sus clases están ocultas.
  const hiddenCovers = hiddenOf("lc:");
  if (hiddenCovers.length && !state.focus) {
    const visible = LAND_COVER_KEYS.filter((item) => !hiddenCovers.includes(item.key));
    conditions.push([
      "any",
      ...visible.map((item) => ["in", `|${item.key}|`, ["get", "_lcs"]]),
    ] as FilterSpecification);
  }
  if (state.playDay && !state.focus) conditions.push(["<=", ["get", "_day"], state.playDay]);
  if (state.playTime && state.focus) conditions.push(["<=", ["get", "_t"], state.playTime]);
  return ["all", ...conditions] as FilterSpecification;
}

/** Aplica el estado actual (`state.hiddenKeys`, reproducción, foco) a las capas del mapa. */
export function applyLayerFilters(): void {
  if (!layersReady()) return;
  const detections = visibilityFilter([
    ["!", ["in", ["coalesce", ["get", "frp_category"], ""], ["literal", hiddenOf("frp:")]]],
  ]);
  for (const layer of [LAYERS.points, LAYERS.pixels, LAYERS.pixelBorders]) {
    map.setFilter(layer, detections);
  }
  // Los activos van como marcadores; la capa solo dibuja los cerrados (si no están ocultos).
  map.setFilter(
    LAYERS.events,
    state.hiddenKeys.has("status:closed")
      ? ["==", ["get", "status"], "__none__"]
      : visibilityFilter([["!=", ["get", "status"], "active"]]),
  );
}
