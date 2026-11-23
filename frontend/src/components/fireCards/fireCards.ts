// Rejilla de tarjetas de incendio, cada una con su mapa en miniatura (un clic lo amplía).
import type { FireFeature } from "../../api/types.js";
import { MAX_CARDS } from "../../domain/config.js";
import { sensorsLabel } from "../../domain/detections.js";
import { biomassLabel, durationLabel } from "../../domain/fireMetrics.js";
import { BIOMASS_HELP } from "../../domain/help.js";
import { landCoverLabel } from "../../domain/landCover.js";
import { dateRangeLabel } from "../../lib/dates.js";
import { formatNumber } from "../../lib/format.js";
import { escapeHtml } from "../../lib/html.js";
import { pillHtml, severityPillHtml } from "./fireDetails.js";
import { drawFireMap } from "./fireMap.js";
import { openFireModal } from "./fireModal.js";
import { bindGlobeButtons, globeButtonHtml } from "./showOnGlobe.js";

function card(feature: FireFeature, period: string | null): HTMLElement {
  const p = feature.properties;
  const element = document.createElement("article");
  element.className = "card";
  element.dataset.period = period ?? "";
  element.dataset.severity = String(p.severity ?? "none");
  const zoneDetections = p.zone_detections ?? p.n_detections;
  element.innerHTML = `
    <div class="fire-map enlargeable" data-event-id="${escapeHtml(p.event_id)}">
      <div class="loading"><span class="spinner"></span>Loading map…</div></div>
    <div class="card-body">
      <div class="card-top">${pillHtml(p, period)}
        ${severityPillHtml(p)}
        <span class="card-sensors">${sensorsLabel(p.sensors)}</span></div>
      <h3 class="card-title">${dateRangeLabel(p.first_detection, p.last_detection)}</h3>
      <p class="card-zone">${escapeHtml(landCoverLabel(p.land_cover))}</p>
      <p class="card-sub">${durationLabel(p.first_detection, p.last_detection, p.n_passes)}</p>
      <div class="card-metrics">
        <div><b>${formatNumber(Number(zoneDetections ?? 0))}</b>
          <span>${p.zone_detections == null ? "detections" : "detections in the zone"}</span></div>
        <div><b>${escapeHtml(p.frp_max_mw)}<small> MW</small></b><span>max FRP</span></div>
        <div title="${escapeHtml(BIOMASS_HELP)}"><b>${biomassLabel(p.biomass_burned_kg)}</b>
          <span>burned biomass</span></div>
      </div>
      <div class="card-actions">
        <button type="button" class="card-more">See all data <span aria-hidden="true">→</span></button>
        ${globeButtonHtml(p.event_id)}
      </div>
    </div>`;
  element.querySelector(".card-more")!.addEventListener("click", () => openFireModal(feature, period));
  bindGlobeButtons(element);
  return element;
}

// Los mapas se crean solo cuando su tarjeta entra en pantalla (puede haber decenas).
const pending = new Map<Element, () => void>();
const observer = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      pending.get(entry.target)?.();
      pending.delete(entry.target);
    }
  },
  { rootMargin: "200px" },
);

/**
 * Rellena `grid` con una tarjeta por incendio (como mucho `MAX_CARDS`). Con `withPeriod`, cada
 * tarjeta lleva el año de su periodo (histórico) en lugar del estado.
 */
export function renderCards(grid: HTMLElement, features: FireFeature[], withPeriod: boolean): void {
  grid.innerHTML = "";
  for (const feature of features.slice(0, MAX_CARDS)) {
    const period = withPeriod ? String(feature.properties.period) : null;
    const element = card(feature, period);
    grid.append(element);
    const container = element.querySelector<HTMLElement>(".fire-map")!;
    pending.set(container, () =>
      void drawFireMap(container, String(feature.properties.event_id), () =>
        openFireModal(feature, period),
      ),
    );
    observer.observe(container);
  }
}
