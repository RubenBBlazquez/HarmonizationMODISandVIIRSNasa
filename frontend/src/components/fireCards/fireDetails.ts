// Piezas de HTML con los datos de un incendio: pastillas (año / estado) y la ficha completa.
import type { FireFeature } from "../../api/types.js";
import { yearColor } from "../../domain/colors.js";
import { confidenceLabel, detectionsLabel, sensorsLabel, zoneDetectionsLabel } from "../../domain/detections.js";
import { biomassLabel, durationLabel, passesLabel } from "../../domain/fireMetrics.js";
import { BIOMASS_HELP, DURATION_HELP } from "../../domain/help.js";
import { landCoverLabel } from "../../domain/landCover.js";
import { SEVERITY_HELP, severityClass, severityLabel } from "../../domain/severity.js";
import { escapeHtml } from "../../lib/html.js";

type FireProperties = FireFeature["properties"];

/** Etiqueta del año (con su color, el mismo que en el globo) o del estado del incendio. */
export function pillHtml(p: FireProperties, period: string | null): string {
  if (period) {
    const year = Number(period.slice(0, 4));
    return `<span class="year-pill" style="--c:${year ? yearColor(year) : "#5b6b62"}">${escapeHtml(period)}</span>`;
  }
  const active = p.status === "active";
  return `<span class="status-pill ${active ? "active" : "closed"}">${active ? "Active" : "Closed"}</span>`;
}

/** Pastilla de color con la severidad. */
export function severityPillHtml(p: FireProperties): string {
  return `<span class="severity-pill ${severityClass(p.severity)}" title="${escapeHtml(SEVERITY_HELP)}">${severityLabel(p.severity)}</span>`;
}

/** Todos los datos de un incendio (ventana ampliada). */
export function statsHtml(p: FireProperties): string {
  return `<dl class="stats">
    <div><dt>Detections</dt><dd>${detectionsLabel(p.n_detections)}</dd></div>
    ${
      p.zone_detections == null
        ? ""
        : `<div><dt>In the zone</dt><dd>${zoneDetectionsLabel(p)}</dd></div>`
    }
    <div><dt>Max FRP</dt><dd>${escapeHtml(p.frp_max_mw)} MW</dd></div>
    <div title="${escapeHtml(BIOMASS_HELP)}"><dt>Burned biomass**</dt>
      <dd>${biomassLabel(p.biomass_burned_kg)}</dd></div>
    <div title="${escapeHtml(DURATION_HELP)}"><dt>Duration</dt>
      <dd>${durationLabel(p.first_detection, p.last_detection, p.n_passes)}</dd></div>
    <div title="Times a satellite saw the fire and the longest time without seeing it">
      <dt>Passes</dt><dd>${passesLabel(p)}</dd></div>
    <div title="${escapeHtml(SEVERITY_HELP)}"><dt>Severity</dt>
      <dd><span class="severity-pill ${severityClass(p.severity)}">${severityLabel(p.severity)}</span></dd></div>
    <div title="Most frequent land cover (SIGPAC) among the detections"><dt>Zone type</dt>
      <dd>${escapeHtml(landCoverLabel(p.land_cover))}</dd></div>
    <div><dt>Footprint*</dt><dd>${escapeHtml(p.footprint_area_km2)} km²</dd></div>
    <div><dt>Sensors</dt><dd>${sensorsLabel(p.sensors)}</dd></div>
    <div><dt>Confidence</dt><dd>${confidenceLabel(p.confidence)}</dd></div>
  </dl>`;
}
