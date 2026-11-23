// Ratón sobre el globo: coordenadas del cursor, tooltip, cursor de mano y clics (elegir país o
// abrir el popup de un incendio o una detección).
import * as maplibregl from "maplibre-gl";
import type { MapGeoJSONFeature } from "maplibre-gl";

import type { Properties } from "../../api/types.js";
import { detectionPopup, detectionTooltip, eventPopupWithReport } from "../../domain/popups.js";
import { escapeHtml } from "../../lib/html.js";
import { ui } from "../dom.js";
import { DETECTION_LAYERS, LAYERS } from "../map/layers.js";
import { map } from "../map/map.js";
import { hideTip, initTooltipPinning, showTip } from "../map/tooltip.js";
import { state } from "../state.js";
import { selectCountry, setHoveredCountry } from "./countries.js";

/** Lo que hay bajo el cursor, por prioridad: evento, detección y país. */
function featureAt(point: maplibregl.PointLike): {
  kind: "event" | "detection" | "country";
  feature: MapGeoJSONFeature;
} | null {
  const layers = [LAYERS.events, ...DETECTION_LAYERS, LAYERS.countries].filter((id) => map.getLayer(id));
  const [feature] = map.queryRenderedFeatures(point, { layers });
  if (!feature) return null;
  const layer = feature.layer.id;
  const kind = layer === LAYERS.events ? "event" : DETECTION_LAYERS.includes(layer) ? "detection" : "country";
  return { kind, feature };
}

export function initPointer(): void {
  map.on("mousemove", (event) => {
    ui.coordinates.innerHTML =
      `Latitude: <b>${event.lngLat.lat.toFixed(5)}°</b><br>` +
      `Longitude: <b>${event.lngLat.lng.toFixed(5)}°</b>`;
    if (state.selecting) return;
    const hit = featureAt(event.point);
    const code = hit?.kind === "country" ? String(hit.feature.properties.code) : null;
    const otherCountry = code !== null && code !== state.selectedCode;
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
    if (html) showTip(html, event.lngLat);
    else hideTip();
  });

  map.on("mouseout", () => {
    hideTip();
    setHoveredCountry(null);
  });

  map.on("click", (event) => {
    if (state.selecting) return;
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
      .setHTML(hit.kind === "detection" ? detectionPopup(properties) : eventPopupWithReport(properties, point))
      .addTo(map);
  });

  initTooltipPinning();
}
