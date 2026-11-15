// Tarjetas de incendio con su mapa y ventanas emergentes, compartidas por el mapa principal
// (index.ts) y la pestaña de incendios (fires.ts).
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./fireCards.css";

import {
  BIOMASS_HELP,
  DURATION_HELP,
  biomassLabel,
  confidenceLabel,
  detectionPopup,
  detectionTooltip,
  detectionsLabel,
  durationLabel,
  escapeHtml,
  fetchJson,
  formatDay,
  formatNumber,
  passesLabel,
  pixelStyle,
  sensorsLabel,
  sortByConfidence,
  zoneDetectionsLabel,
  type EventDetail,
  type FireFeature,
  type HistoryPeriod,
} from "./shared.js";

export const MAX_CARDS = 100;
const DETECTIONS_PER_MAP = 2000;
const HULL_STYLE: L.PathOptions = { color: "#26362c", weight: 2, dashArray: "5 4", fill: false };

// --- Ventanas emergentes ---------------------------------------------------------------------
export interface Modal {
  body: HTMLElement;
  close: () => void;
}
const openModals: Modal[] = [];

/** Abre una ventana grande sobre el mapa. Esc o un clic fuera cierran la de encima. */
export function openModal(title: string, className = ""): Modal {
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `
    <section class="modal ${className}" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
      <header class="modal-head"><h2>${escapeHtml(title)}</h2>
        <button type="button" class="modal-close" aria-label="Cerrar">✕</button></header>
      <div class="modal-body"></div>
    </section>`;
  const modal: Modal = {
    body: backdrop.querySelector<HTMLElement>(".modal-body")!,
    close: () => {
      backdrop.remove();
      const index = openModals.indexOf(modal);
      if (index >= 0) openModals.splice(index, 1);
    },
  };
  backdrop.addEventListener("mousedown", (event) => {
    if (event.target === backdrop) modal.close();
  });
  backdrop.querySelector(".modal-close")!.addEventListener("click", modal.close);
  document.body.append(backdrop);
  openModals.push(modal);
  return modal;
}

// En captura: con una ventana abierta, Esc solo la cierra (no llega a los demás atajos del mapa).
document.addEventListener(
  "keydown",
  (event) => {
    if (event.key !== "Escape" || openModals.length === 0) return;
    event.stopPropagation();
    openModals[openModals.length - 1].close();
  },
  true,
);

// --- Tarjetas --------------------------------------------------------------------------------
export function statsHtml(p: FireFeature["properties"]): string {
  return `<dl class="stats">
    <div><dt>Detecciones</dt><dd>${detectionsLabel(p.n_detections)}</dd></div>
    ${
      p.zone_detections == null
        ? ""
        : `<div><dt>En la zona</dt><dd>${zoneDetectionsLabel(p)}</dd></div>`
    }
    <div><dt>FRP máximo</dt><dd>${escapeHtml(p.frp_max_mw)} MW</dd></div>
    <div title="${escapeHtml(BIOMASS_HELP)}"><dt>Biomasa quemada**</dt>
      <dd>${biomassLabel(p.biomass_burned_kg)}</dd></div>
    <div title="${escapeHtml(DURATION_HELP)}"><dt>Duración</dt>
      <dd>${durationLabel(p.first_detection, p.last_detection, p.n_passes)}</dd></div>
    <div title="Veces que un satélite vio el fuego y mayor tiempo sin verlo">
      <dt>Pasadas</dt><dd>${passesLabel(p)}</dd></div>
    <div><dt>Huella*</dt><dd>${escapeHtml(p.footprint_area_km2)} km²</dd></div>
    <div><dt>Sensores</dt><dd>${sensorsLabel(p.sensors)}</dd></div>
    <div><dt>Confianza</dt><dd>${confidenceLabel(p.confidence)}</dd></div>
  </dl>`;
}

function badgeHtml(p: FireFeature["properties"], period: string | null): string {
  return period
    ? `<span class="badge">${escapeHtml(period)}</span>`
    : `<span class="badge ${escapeHtml(p.status)}">${p.status === "active" ? "activo" : "cerrado"}</span>`;
}

function card(feature: FireFeature, period: string | null): HTMLElement {
  const p = feature.properties;
  const element = document.createElement("article");
  element.className = "card";
  element.innerHTML = `
    <header>${badgeHtml(p, period)}<h2>${formatDay(p.first_detection)} → ${formatDay(p.last_detection)}</h2></header>
    <div class="fire-map enlargeable" data-event-id="${escapeHtml(p.event_id)}">
      <div class="loading">Cargando mapa…</div></div>
    ${statsHtml(p)}`;
  return element;
}

const detailCache = new Map<string, Promise<EventDetail>>();
function loadDetail(eventId: string): Promise<EventDetail> {
  let detail = detailCache.get(eventId);
  if (!detail) {
    detail = fetchJson<EventDetail>(`/events/${encodeURIComponent(eventId)}`, {
      limit: String(DETECTIONS_PER_MAP),
    });
    detailCache.set(eventId, detail);
    detail.catch(() => detailCache.delete(eventId)); // un fallo no se queda en caché
  }
  return detail;
}

/**
 * Mapa de un incendio: su envolvente (discontinua) y el píxel de cada detección, coloreado por
 * FRP. Con `onClick` es una vista previa (un clic la amplía); sin él, el mapa es interactivo.
 */
async function drawFireMap(
  container: HTMLElement,
  eventId: string,
  onClick?: () => void,
): Promise<void> {
  try {
    const detail = await loadDetail(eventId);
    container.innerHTML = "";
    const map = L.map(container, { preferCanvas: true, scrollWheelZoom: !onClick });
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
    map.fitBounds(hull.getBounds().extend(pixels.getBounds()), { padding: [12, 12] });

    if (onClick) {
      map.on("click", onClick);
      container.insertAdjacentHTML(
        "beforeend",
        '<span class="enlarge-hint">Pulsa para ampliar</span>',
      );
    }
    if (detail.detections.truncated) {
      const note = document.createElement("p");
      note.className = "note";
      note.textContent = `Mapa con ${formatNumber(detail.detections.count)} de ${detectionsLabel(
        detail.detections.total,
      )}.`;
      container.after(note);
    }
  } catch (error) {
    container.innerHTML = `<div class="loading">No se pudo cargar el mapa: ${escapeHtml(
      (error as Error).message,
    )}</div>`;
  }
}

/** Ventana grande con el mapa del incendio (zoom con la rueda, popups) y todos sus datos. */
export function openFireModal(feature: FireFeature, period: string | null): void {
  const p = feature.properties;
  const modal = openModal(`${formatDay(p.first_detection)} → ${formatDay(p.last_detection)}`, "lightbox");
  modal.body.innerHTML = `<p>${badgeHtml(p, period)}</p>
    <div class="big-map"><div class="loading">Cargando mapa…</div></div>${statsHtml(p)}`;
  void drawFireMap(modal.body.querySelector<HTMLElement>(".big-map")!, String(p.event_id));
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

/** Rellena `grid` con una tarjeta por incendio (como mucho `MAX_CARDS`); un clic en el mapa lo amplía. */
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

export function summaryTableHtml(periods: HistoryPeriod[]): string {
  const rows = periods.map(
    (period) => `<tr class="${period.is_reference ? "reference" : ""}">
      <td>${escapeHtml(period.label)}${period.is_reference ? " (elegido)" : ""}</td>
      <td>${formatNumber(period.events)}</td><td>${formatNumber(period.detections)}</td>
      <td>${period.frp_max_mw ?? "—"}</td></tr>`,
  );
  return `<thead><tr><th>Periodo</th><th>Incendios</th><th>Detecciones en la zona</th>
    <th>FRP máx. en la zona (MW)</th></tr></thead><tbody>${rows.join("")}</tbody>`;
}
