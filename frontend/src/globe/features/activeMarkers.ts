// Incendios activos: marcador HTML con pulso sobre el mapa (los cerrados son una capa de círculos).
// También alimenta la lista de alertas con los activos visibles.
import * as maplibregl from "maplibre-gl";

import { confidenceColor } from "../../domain/colors.js";
import { landCoverKeys } from "../../domain/landCover.js";
import { eventPopupWithReport } from "../../domain/popups.js";
import { map } from "../map/map.js";
import { isVisibleByDate, isVisibleYear, state } from "../state.js";
import { updateAlerts } from "./alerts.js";

let activeMarkers: maplibregl.Marker[] = [];

export function drawActiveMarkers(): void {
  for (const marker of activeMarkers) marker.remove();
  activeMarkers = [];
  if (state.focus) return; // con un incendio enfocado no se muestran los del rango
  const active = (state.events?.features ?? []).filter(
    (feature) =>
      feature.properties.status === "active" &&
      isVisibleByDate(feature, "first_detection") &&
      landCoverKeys(feature.properties.land_cover).some((key) => !state.hiddenKeys.has(`lc:${key}`)),
  );
  updateAlerts(active);
  if (state.hiddenKeys.has("status:active")) return;
  for (const feature of active) {
    if (feature.geometry.type !== "Point" || !isVisibleYear(feature, "first_detection")) continue;
    const point = feature.geometry.coordinates;
    const color = confidenceColor(feature.properties.confidence);
    const element = document.createElement("div");
    element.className = "active-zone-marker";
    element.innerHTML = `<span class="active-zone-pulse" style="--alert-color:${color}"></span>
      <span class="active-zone-icon" style="--alert-color:${color}" aria-label="Active zone">!</span>`;
    const popup = new maplibregl.Popup({ maxWidth: "320px", offset: 20 }).setHTML(
      eventPopupWithReport(feature.properties, point),
    );
    activeMarkers.push(
      new maplibregl.Marker({ element }).setLngLat([point[0], point[1]]).setPopup(popup).addTo(map),
    );
  }
}
