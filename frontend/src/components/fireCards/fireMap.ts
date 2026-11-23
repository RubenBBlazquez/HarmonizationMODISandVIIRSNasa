// Mapa (Leaflet) de un incendio: su envolvente y el píxel de cada detección coloreado por FRP.
import L from "leaflet";
import "leaflet/dist/leaflet.css";

import { getEventDetail } from "../../api/endpoints.js";
import type { EventDetail } from "../../api/types.js";
import {
  FRP_COLORS,
  MODIS_PIXEL_BORDER_OPACITY,
  MODIS_PIXEL_FILL_OPACITY,
  NO_CATEGORY_COLOR,
  PIXEL_BORDER_COLOR,
  PIXEL_FILL_OPACITY,
} from "../../domain/colors.js";
import { detectionsLabel, sortByConfidence } from "../../domain/detections.js";
import { detectionPopup, detectionTooltip } from "../../domain/popups.js";
import { formatNumber } from "../../lib/format.js";
import { escapeHtml } from "../../lib/html.js";

const DETECTIONS_PER_MAP = 2000;
const HULL_STYLE: L.PathOptions = { color: "#1b2a3a", weight: 2, dashArray: "5 4", fill: false };

/** Estilo de un píxel: VIIRS opaco y con borde marcado; MODIS muy transparente y de fondo. */
function pixelStyle(feature: GeoJSON.Feature | undefined): L.PathOptions {
  const isModis = feature?.properties?.source === "MODIS";
  return {
    color: PIXEL_BORDER_COLOR,
    weight: isModis ? 1 : 1.5,
    opacity: isModis ? MODIS_PIXEL_BORDER_OPACITY : 1,
    fillColor: FRP_COLORS[String(feature?.properties?.frp_category)] ?? NO_CATEGORY_COLOR,
    fillOpacity: isModis ? MODIS_PIXEL_FILL_OPACITY : PIXEL_FILL_OPACITY,
  };
}

// Cada incendio se pide una vez aunque salga en varias tarjetas o se amplíe.
const detailCache = new Map<string, Promise<EventDetail>>();
function loadDetail(eventId: string): Promise<EventDetail> {
  let detail = detailCache.get(eventId);
  if (!detail) {
    detail = getEventDetail(eventId, DETECTIONS_PER_MAP);
    detailCache.set(eventId, detail);
    detail.catch(() => detailCache.delete(eventId)); // un fallo no se queda en caché
  }
  return detail;
}

/**
 * Dibuja en `container` el mapa de un incendio. Con `onClick` es una vista previa (sin controles;
 * un clic la amplía); sin él, el mapa es interactivo (zoom con la rueda, popups).
 */
export async function drawFireMap(
  container: HTMLElement,
  eventId: string,
  onClick?: () => void,
): Promise<void> {
  try {
    const detail = await loadDetail(eventId);
    container.innerHTML = "";
    const map = L.map(container, {
      preferCanvas: true,
      scrollWheelZoom: !onClick,
      zoomControl: !onClick,
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
    }).addTo(map);

    const hull = L.geoJSON(detail.event as GeoJSON.Feature, {
      style: HULL_STYLE,
      interactive: false,
    }).addTo(map);
    const pixels = L.geoJSON(sortByConfidence(detail.detections as GeoJSON.FeatureCollection), {
      style: (feature) => pixelStyle(feature),
      onEachFeature: (feature, layer) => {
        if (!onClick) layer.bindPopup(detectionPopup(feature.properties));
        layer.bindTooltip(detectionTooltip(feature.properties), { sticky: true, direction: "top" });
      },
    }).addTo(map);
    map.fitBounds(hull.getBounds().extend(pixels.getBounds()), { padding: [18, 18] });

    if (onClick) {
      map.on("click", onClick);
      container.insertAdjacentHTML(
        "beforeend",
        '<span class="enlarge-hint"><span aria-hidden="true">⤢</span> Enlarge</span>',
      );
    }
    if (detail.detections.truncated) {
      const note = document.createElement("p");
      note.className = "note";
      note.textContent = `Map with ${formatNumber(detail.detections.count)} of ${detectionsLabel(
        detail.detections.total,
      )}.`;
      container.after(note);
    }
  } catch (error) {
    container.innerHTML = `<div class="loading">Could not load the map: ${escapeHtml(
      (error as Error).message,
    )}</div>`;
  }
}
