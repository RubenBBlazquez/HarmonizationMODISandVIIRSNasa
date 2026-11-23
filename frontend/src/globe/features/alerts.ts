// Lista de alertas (arriba, en el centro): los incendios activos del rango, ordenables por los más
// recientes o los más graves. Un clic lleva el mapa al incendio.
import type { FireFeature } from "../../api/types.js";
import { freshnessClass } from "../../domain/fireMetrics.js";
import { landCoverLabel } from "../../domain/landCover.js";
import { detectionsLabel } from "../../domain/detections.js";
import { SEVERITY_HELP, severityLabel, severityRank } from "../../domain/severity.js";
import { formatDate, formatDay, timeAgoLabel } from "../../lib/dates.js";
import { coordinatesLabel } from "../../lib/format.js";
import { escapeHtml } from "../../lib/html.js";
import { isMobile } from "../../lib/responsive.js";
import { ui } from "../dom.js";
import { map } from "../map/map.js";
import { setAlertsOpen } from "../ui/layout.js";

const ALERT_ZOOM = 10;

type AlertSort = "recent" | "severity";
let alertsSort: AlertSort = "recent"; // lo urgente es lo último que se ha visto
let lastAlerts: FireFeature[] = [];

/** "recent": última detección primero; "severity": la más grave primero. En ambos, el otro criterio desempata. */
function sortAlerts(features: FireFeature[], mode: AlertSort): FireFeature[] {
  const rank = (feature: FireFeature): number => severityRank(feature.properties.severity);
  const recency = (feature: FireFeature): string => String(feature.properties.last_detection ?? "");
  const byRecency = (a: FireFeature, b: FireFeature): number => recency(b).localeCompare(recency(a));
  const bySeverity = (a: FireFeature, b: FireFeature): number => rank(b) - rank(a);
  return [...features].sort(
    mode === "recent"
      ? (a, b) => byRecency(a, b) || bySeverity(a, b)
      : (a, b) => bySeverity(a, b) || byRecency(a, b),
  );
}

function alertItemHtml(feature: FireFeature, index: number): string {
  const p = feature.properties;
  const place = feature.geometry.type === "Point" ? coordinatesLabel(feature.geometry.coordinates) : "";
  const severity = String(p.severity ?? "none");
  return `<button type="button" class="alert-item" data-index="${index}">
    <span class="severity-pill sev-${escapeHtml(severity)}" title="${escapeHtml(SEVERITY_HELP)}">${escapeHtml(severityLabel(p.severity))}</span>
    <span class="alert-body"><b>${escapeHtml(landCoverLabel(p.land_cover))}</b>
      <small>${detectionsLabel(p.n_detections)} · max FRP ${escapeHtml(p.frp_max_mw)} MW</small>
      <small>${place} · started ${formatDay(p.first_detection)}</small></span>
    <span class="alert-time ${freshnessClass(p.last_detection)}" title="Last seen ${escapeHtml(formatDate(p.last_detection))}">
      <b>${escapeHtml(timeAgoLabel(p.last_detection))}</b><small>last seen</small></span>
  </button>`;
}

function renderAlerts(): void {
  const active = sortAlerts(lastAlerts, alertsSort);
  ui.alertsToggle.querySelector<HTMLElement>(".alerts-count")!.textContent = String(active.length);
  ui.alertsToggle.classList.toggle("has-alerts", active.length > 0);
  const tab = (mode: AlertSort, label: string): string =>
    `<button type="button" class="alerts-tab" data-sort="${mode}" aria-pressed="${alertsSort === mode}">${label}</button>`;
  ui.alertsList.innerHTML = active.length
    ? `<div class="alerts-tabs" role="group" aria-label="Sort alerts">${tab("recent", "Most recent")}${tab("severity", "Most severe")}</div>` +
      active.map(alertItemHtml).join("")
    : `<div class="alerts-empty">No active zones</div>`;
  ui.alertsList.querySelectorAll<HTMLButtonElement>(".alerts-tab").forEach((button) =>
    button.addEventListener("click", () => {
      alertsSort = button.dataset.sort as AlertSort;
      renderAlerts();
    }),
  );
  ui.alertsList.querySelectorAll<HTMLButtonElement>(".alert-item").forEach((button) => {
    button.addEventListener("click", () => {
      const geometry = active[Number(button.dataset.index)].geometry;
      if (geometry.type === "Point") {
        map.flyTo({ center: [geometry.coordinates[0], geometry.coordinates[1]], zoom: ALERT_ZOOM });
        if (isMobile()) setAlertsOpen(false); // móvil: deja ver el mapa tras elegir
      }
    });
  });
}

/** Cambia los incendios de la lista. */
export function updateAlerts(features: FireFeature[]): void {
  lastAlerts = features;
  renderAlerts();
}
