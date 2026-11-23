// HTML de los popups y tooltips de detecciones e incendios (globo y mapas de las tarjetas).
import type { Properties } from "../api/types.js";
import { formatDate, formatDay } from "../lib/dates.js";
import { escapeHtml } from "../lib/html.js";
import { confidenceLabel, detectionsLabel, sensorLabel, sensorsLabel } from "./detections.js";
import { biomassLabel, durationLabel, passesLabel } from "./fireMetrics.js";
import { BIOMASS_HELP } from "./help.js";
import { landCoverLabel } from "./landCover.js";
import { SEVERITY_HELP, severityLabel } from "./severity.js";

/** Texto al pasar el ratón por una detección: sensor, satélite y día-mes-año. */
export function detectionTooltip(properties: Properties): string {
  return `${sensorLabel(properties)} · ${formatDay(properties.acq_datetime)}`;
}

export function detectionPopup(properties: Properties): string {
  return `<b>Detection ${sensorLabel(properties)}</b><br>
    ${formatDate(properties.acq_datetime)}<br>
    FRP: <b>${escapeHtml(properties.frp_mw)} MW</b> (${escapeHtml(properties.frp_category)})<br>
    Confidence: ${confidenceLabel(properties.confidence)} ·
    ${properties.daynight === "D" ? "day" : "night"}<br>
    Biomass: ${escapeHtml(properties.biomass_kg_s)} kg/s<br>
    Area: ${escapeHtml(properties.area_width_km)} × ${escapeHtml(properties.area_height_km)} km<br>
    Land cover: ${escapeHtml(landCoverLabel(properties.land_cover))}`;
}

export function eventPopup(properties: Properties): string {
  const zone =
    properties.zone_detections == null
      ? ""
      : `In the zone: <b>${detectionsLabel(properties.zone_detections)}</b>
        (max FRP ${escapeHtml(properties.zone_frp_max_mw)} MW)<br>`;
  return `<b>Fire ${properties.status === "active" ? "active" : "closed"}</b>
    (${sensorsLabel(properties.sensors)})<br>
    ${formatDate(properties.first_detection)} → ${formatDate(properties.last_detection)}<br>
    ${zone}Whole fire: ${detectionsLabel(properties.n_detections)} · max FRP:
    <b>${escapeHtml(properties.frp_max_mw)} MW</b> · burned biomass
    <b title="${escapeHtml(BIOMASS_HELP)}">${biomassLabel(properties.biomass_burned_kg)}</b><br>
    Duration: ${durationLabel(properties.first_detection, properties.last_detection, properties.n_passes)}
    (${passesLabel(properties)})<br>
    Severity: <b title="${escapeHtml(SEVERITY_HELP)}">${severityLabel(properties.severity)}</b> ·
    Zone type: <b>${escapeHtml(landCoverLabel(properties.land_cover))}</b><br>
    Confidence: ${confidenceLabel(properties.confidence)}<br>
    Footprint: ${escapeHtml(properties.footprint_area_km2)} km² (not burned area)`;
}

/** Popup de un incendio con, si está activo, el enlace para avisar en Google Maps. */
export function eventPopupWithReport(properties: Properties, point: GeoJSON.Position | null): string {
  const report =
    properties.status === "active" && point
      ? `<br><a class="google-maps-report" target="_blank" rel="noopener noreferrer"
          href="https://www.google.com/maps/search/?api=1&query=${point[1]},${point[0]}">
          Report on Google Maps</a>`
      : "";
  return `${eventPopup(properties)}${report}`;
}
