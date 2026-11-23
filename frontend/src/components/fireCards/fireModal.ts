// Ventana grande con el mapa de un incendio (zoom con la rueda, popups) y todos sus datos.
import type { FireFeature } from "../../api/types.js";
import { sensorsLabel } from "../../domain/detections.js";
import { durationLabel } from "../../domain/fireMetrics.js";
import { BIOMASS_HELP, FOOTPRINT_NOTE } from "../../domain/help.js";
import { dateRangeLabel } from "../../lib/dates.js";
import { escapeHtml } from "../../lib/html.js";
import { openModal } from "../modal.js";
import { pillHtml, statsHtml } from "./fireDetails.js";
import { drawFireMap } from "./fireMap.js";
import { bindGlobeButtons, globeButtonHtml } from "./showOnGlobe.js";

export function openFireModal(feature: FireFeature, period: string | null): void {
  const p = feature.properties;
  const modal = openModal(
    dateRangeLabel(p.first_detection, p.last_detection),
    "lightbox",
    period ? `Fire of ${period}` : "Fire",
  );
  modal.body.innerHTML = `
    <div class="lightbox-top">${pillHtml(p, period)}
      <span class="card-sensors">${sensorsLabel(p.sensors)} ·
        ${durationLabel(p.first_detection, p.last_detection, p.n_passes)}</span>
      ${globeButtonHtml(p.event_id)}</div>
    <div class="big-map"><div class="loading"><span class="spinner"></span>Loading map…</div></div>
    ${statsHtml(p)}
    <p class="footnote">${FOOTPRINT_NOTE}
      **${escapeHtml(BIOMASS_HELP)}</p>`;
  bindGlobeButtons(modal.body);
  void drawFireMap(modal.body.querySelector<HTMLElement>(".big-map")!, String(p.event_id));
}
