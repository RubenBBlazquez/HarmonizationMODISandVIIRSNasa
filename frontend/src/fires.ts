// Pestaña con todos los incendios de una búsqueda del mapa: una tarjeta con su mapa por incendio.
// Parámetros de la URL: mode=zone|history, bbox, start, end y (en history) years.
import L from "leaflet";
import "leaflet/dist/leaflet.css";

import {
  DETECTION_HELP,
  FRP_COLORS,
  HISTORY_YEARS,
  NO_CATEGORY_COLOR,
  PIXEL_BORDER_COLOR,
  PIXEL_FILL_OPACITY,
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
  formatDayMonth,
  formatNumber,
  passesLabel,
  sensorsLabel,
  zoneDetectionsLabel,
  type EventDetail,
  type FireCollection,
  type FireFeature,
  type HistoryResponse,
} from "./shared.js";

const MAX_CARDS = 100;
const ZONE_QUERY_LIMIT = 5000;
const DETECTIONS_PER_MAP = 2000;
const HULL_STYLE: L.PathOptions = { color: "#26362c", weight: 2, dashArray: "5 4", fill: false };

const params = new URLSearchParams(window.location.search);
const mode = params.get("mode") === "history" ? "history" : "zone";
const bbox = params.get("bbox") ?? "";
const start = params.get("start") ?? "";
const end = params.get("end") ?? "";
const years = params.get("years") ?? String(HISTORY_YEARS);

const titleElement = document.querySelector<HTMLElement>("#page-title")!;
const criteriaElement = document.querySelector<HTMLElement>("#criteria")!;
const statusElement = document.querySelector<HTMLElement>("#status")!;
const summaryElement = document.querySelector<HTMLTableElement>("#summary")!;
const gridElement = document.querySelector<HTMLElement>("#grid")!;
document.querySelector<HTMLElement>("#help")!.innerHTML = [
  DETECTION_HELP,
  DURATION_HELP,
  `**${BIOMASS_HELP}`,
  "*La huella (área de los píxeles detectados) no es superficie quemada.",
]
  .map((text) => escapeHtml(text))
  .join("<br>");

function zoneText(): string {
  const [west, south, east, north] = bbox.split(",").map(Number);
  return `Zona: lat ${south.toFixed(3)} a ${north.toFixed(3)} · lon ${west.toFixed(3)} a ${east.toFixed(3)}`;
}

function card(feature: FireFeature, period: string | null): HTMLElement {
  const p = feature.properties;
  const element = document.createElement("article");
  element.className = "card";
  const badge = period
    ? `<span class="badge">${escapeHtml(period)}</span>`
    : `<span class="badge ${escapeHtml(p.status)}">${p.status === "active" ? "activo" : "cerrado"}</span>`;
  element.innerHTML = `
    <header>${badge}<h2>${formatDay(p.first_detection)} → ${formatDay(p.last_detection)}</h2></header>
    <div class="fire-map" data-event-id="${escapeHtml(p.event_id)}">
      <div class="loading">Cargando mapa…</div></div>
    <dl class="stats">
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
  return element;
}

// Mapa de un incendio: su envolvente (discontinua) y el píxel de cada detección, coloreado por FRP.
async function drawCardMap(container: HTMLElement): Promise<void> {
  const eventId = container.dataset.eventId ?? "";
  try {
    const detail = await fetchJson<EventDetail>(`/events/${encodeURIComponent(eventId)}`, {
      limit: String(DETECTIONS_PER_MAP),
    });
    container.innerHTML = "";
    const map = L.map(container, { preferCanvas: true, scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
    }).addTo(map);

    const hull = L.geoJSON(detail.event as GeoJSON.Feature, { style: HULL_STYLE }).addTo(map);
    const pixels = L.geoJSON(detail.detections as GeoJSON.FeatureCollection, {
      style: (feature) => ({
        color: PIXEL_BORDER_COLOR,
        weight: 1,
        fillColor: FRP_COLORS[String(feature?.properties?.frp_category)] ?? NO_CATEGORY_COLOR,
        fillOpacity: PIXEL_FILL_OPACITY,
      }),
      onEachFeature: (feature, layer) => {
        layer.bindPopup(detectionPopup(feature.properties));
        layer.bindTooltip(detectionTooltip(feature.properties), {
          sticky: true,
          direction: "top",
        });
      },
    }).addTo(map);
    map.fitBounds(hull.getBounds().extend(pixels.getBounds()), { padding: [12, 12] });

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

// Los mapas se crean solo cuando su tarjeta entra en pantalla (puede haber decenas).
const observer = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      void drawCardMap(entry.target as HTMLElement);
    }
  },
  { rootMargin: "200px" },
);

function renderCards(features: FireFeature[], withPeriod: boolean): void {
  gridElement.innerHTML = "";
  for (const feature of features.slice(0, MAX_CARDS)) {
    const element = card(feature, withPeriod ? String(feature.properties.period) : null);
    gridElement.append(element);
    observer.observe(element.querySelector(".fire-map")!);
  }
}

function renderSummary(data: HistoryResponse): void {
  const rows = data.periods.map(
    (period) => `<tr class="${period.is_reference ? "reference" : ""}">
      <td>${escapeHtml(period.label)}${period.is_reference ? " (elegido)" : ""}</td>
      <td>${formatNumber(period.events)}</td><td>${formatNumber(period.detections)}</td>
      <td>${period.frp_max_mw ?? "—"}</td></tr>`,
  );
  summaryElement.innerHTML = `<thead><tr><th>Periodo</th><th>Incendios</th><th>Detecciones en la zona</th>
    <th>FRP máx. en la zona (MW)</th></tr></thead><tbody>${rows.join("")}</tbody>`;
  summaryElement.hidden = false;
}

async function load(): Promise<void> {
  if (!bbox) {
    statusElement.textContent = "Falta la zona. Abre esta página desde el mapa (Seleccionar zona).";
    return;
  }

  try {
    if (mode === "history") {
      titleElement.textContent = "Incendios similares en años anteriores";
      criteriaElement.textContent = `${zoneText()} · mismas fechas (${formatDayMonth(start)} → ${formatDayMonth(end)}) en los ${years} años anteriores a ${start.slice(0, 4)}.`;
      const data = await fetchJson<HistoryResponse>("/history", {
        bbox,
        start,
        end,
        years,
        limit: String(MAX_CARDS),
      });
      renderSummary(data);
      const shown = Math.min(data.events.count, MAX_CARDS);
      statusElement.textContent = `${formatNumber(shown)} incendios de años anteriores, los mayores primero. *La huella no es superficie quemada.`;
      renderCards(data.events.features, true);
    } else {
      titleElement.textContent = "Incendios en la zona";
      const dates = start && end ? ` · del ${formatDay(start)} al ${formatDay(end)}` : "";
      criteriaElement.textContent = `${zoneText()}${dates}.`;
      const query: Record<string, string> = {
        bbox,
        limit: String(ZONE_QUERY_LIMIT),
        match: "inside", // solo incendios con detecciones dentro de la zona
      };
      if (start) query.start = start;
      if (end) query.end = end;
      const data = await fetchJson<FireCollection>("/events", query);
      const inZone = (feature: FireFeature): number =>
        Number(feature.properties.zone_detections ?? feature.properties.n_detections);
      const largest = [...data.features].sort((a, b) => inZone(b) - inZone(a));
      const shown = Math.min(largest.length, MAX_CARDS);
      statusElement.textContent = `${formatNumber(data.total)} incendios; se muestran los ${formatNumber(shown)} mayores. *La huella no es superficie quemada.`;
      renderCards(largest, false);
    }
  } catch (error) {
    statusElement.textContent = `No se pudieron cargar los incendios: ${(error as Error).message}`;
  }
}

void load();
