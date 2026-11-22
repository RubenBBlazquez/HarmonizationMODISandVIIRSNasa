// Tarjetas de incendio con su mapa, el histórico de una zona y las ventanas emergentes,
// compartidos por el mapa principal (globe.ts) y la pestaña de incendios (fires.ts).
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./fireCards.css";

import {
  BIOMASS_HELP,
  DETECTION_HELP,
  DURATION_HELP,
  biomassLabel,
  confidenceLabel,
  detectionPopup,
  detectionTooltip,
  detectionsLabel,
  durationLabel,
  escapeHtml,
  fetchJson,
  formatDayMonth,
  formatNumber,
  passesLabel,
  pixelStyle,
  sensorsLabel,
  sortByConfidence,
  yearColor,
  zoneDetectionsLabel,
  type EventDetail,
  type FireFeature,
  type HistoryPeriod,
  type HistoryResponse,
} from "./shared.js";

export const MAX_CARDS = 100;
const DETECTIONS_PER_MAP = 2000;
const HULL_STYLE: L.PathOptions = { color: "#1b2a3a", weight: 2, dashArray: "5 4", fill: false };

// --- Fechas legibles ("14 ago 2024") --------------------------------------------------------------
const SHORT_DATE = new Intl.DateTimeFormat("es-ES", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const DAY_MONTH = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short", timeZone: "UTC" });

function parseDate(value: string | number | null | undefined): Date | null {
  if (value == null || value === "") return null;
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "14 ago 2024", "12 → 14 ago 2024" o "30 jul → 2 ago 2024". */
export function dateRangeLabel(
  first: string | number | null | undefined,
  last: string | number | null | undefined,
): string {
  const [a, b] = [parseDate(first), parseDate(last)];
  if (!a) return "Sin fecha";
  const clean = (text: string): string => text.replace(/\./g, "");
  if (!b || a.toISOString().slice(0, 10) === b.toISOString().slice(0, 10)) return clean(SHORT_DATE.format(a));
  if (a.getUTCFullYear() !== b.getUTCFullYear()) {
    return `${clean(SHORT_DATE.format(a))} → ${clean(SHORT_DATE.format(b))}`;
  }
  if (a.getUTCMonth() === b.getUTCMonth()) {
    return `${a.getUTCDate()} → ${clean(SHORT_DATE.format(b))}`;
  }
  return `${clean(DAY_MONTH.format(a))} → ${clean(SHORT_DATE.format(b))}`;
}

// --- Ventanas emergentes ---------------------------------------------------------------------
export interface Modal {
  body: HTMLElement;
  close: () => void;
  /** Fondo de la ventana (su elemento raíz en la página). */
  element: HTMLElement;
}
const openModals: Modal[] = [];

/**
 * Aparta las ventanas abiertas sin cerrarlas (para ver algo en el globo): `resume` las vuelve a
 * mostrar tal como estaban (scroll, filtros) y `discard` las cierra.
 */
export function suspendModals(): { resume: () => void; discard: () => void } | null {
  if (openModals.length === 0) return null;
  const suspended = openModals.splice(0);
  for (const modal of suspended) modal.element.hidden = true;
  return {
    resume: () => {
      for (const modal of suspended) {
        modal.element.hidden = false;
        openModals.push(modal);
      }
    },
    discard: () => suspended.forEach((modal) => modal.element.remove()),
  };
}

// --- "Ver en el globo" ------------------------------------------------------------------------
// En el globo (globe.ts) se registra cómo enfocar un incendio; en la pestaña de incendios no hay
// globo, así que el botón abre la página principal ya enfocada en él (?fire=).
let showOnGlobe: ((eventId: string) => void) | null = null;
export function setShowOnGlobe(handler: (eventId: string) => void): void {
  showOnGlobe = handler;
}

const GLOBE_ICON = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>`;

function globeButtonHtml(eventId: string | number | null): string {
  return `<button type="button" class="globe-button" data-event-id="${escapeHtml(eventId)}"
    title="Ver este incendio en el globo">${GLOBE_ICON}Ver en el globo</button>`;
}

function bindGlobeButtons(root: HTMLElement): void {
  root.querySelectorAll<HTMLButtonElement>(".globe-button").forEach((button) =>
    button.addEventListener("click", () => {
      const eventId = button.dataset.eventId ?? "";
      if (showOnGlobe) showOnGlobe(eventId);
      else window.location.href = `/?fire=${encodeURIComponent(eventId)}`;
    }),
  );
}

/** Abre una ventana grande sobre el mapa. Esc o un clic fuera cierran la de encima. */
export function openModal(title: string, className = "", subtitle = ""): Modal {
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `
    <section class="modal ${className}" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
      <header class="modal-head">
        <div class="modal-heading">
          ${subtitle ? `<span class="modal-eyebrow">${escapeHtml(subtitle)}</span>` : ""}
          <h2>${escapeHtml(title)}</h2>
        </div>
        <button type="button" class="modal-close" aria-label="Cerrar">✕</button>
      </header>
      <div class="modal-body"></div>
    </section>`;
  const modal: Modal = {
    element: backdrop,
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

// --- Datos de un incendio ------------------------------------------------------------------------
/** Todos los datos de un incendio (ventana ampliada). */
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

/** Etiqueta del año (con su color, el mismo que en el globo) o del estado del incendio. */
function pillHtml(p: FireFeature["properties"], period: string | null): string {
  if (period) {
    const year = Number(period.slice(0, 4));
    return `<span class="year-pill" style="--c:${year ? yearColor(year) : "#5b6b62"}">${escapeHtml(period)}</span>`;
  }
  const active = p.status === "active";
  return `<span class="status-pill ${active ? "active" : "closed"}">${active ? "Activo" : "Cerrado"}</span>`;
}

function card(feature: FireFeature, period: string | null): HTMLElement {
  const p = feature.properties;
  const element = document.createElement("article");
  element.className = "card";
  element.dataset.period = period ?? "";
  const zoneDetections = p.zone_detections ?? p.n_detections;
  element.innerHTML = `
    <div class="fire-map enlargeable" data-event-id="${escapeHtml(p.event_id)}">
      <div class="loading"><span class="spinner"></span>Cargando mapa…</div></div>
    <div class="card-body">
      <div class="card-top">${pillHtml(p, period)}
        <span class="card-sensors">${sensorsLabel(p.sensors)}</span></div>
      <h3 class="card-title">${dateRangeLabel(p.first_detection, p.last_detection)}</h3>
      <p class="card-sub">${durationLabel(p.first_detection, p.last_detection, p.n_passes)}</p>
      <div class="card-metrics">
        <div><b>${formatNumber(Number(zoneDetections ?? 0))}</b>
          <span>${p.zone_detections == null ? "detecciones" : "detecciones en la zona"}</span></div>
        <div><b>${escapeHtml(p.frp_max_mw)}<small> MW</small></b><span>FRP máximo</span></div>
        <div title="${escapeHtml(BIOMASS_HELP)}"><b>${biomassLabel(p.biomass_burned_kg)}</b>
          <span>biomasa quemada</span></div>
      </div>
      <div class="card-actions">
        <button type="button" class="card-more">Ver todos los datos <span aria-hidden="true">→</span></button>
        ${globeButtonHtml(p.event_id)}
      </div>
    </div>`;
  element.querySelector(".card-more")!.addEventListener("click", () => openFireModal(feature, period));
  bindGlobeButtons(element);
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
 * FRP. Con `onClick` es una vista previa (sin controles; un clic la amplía); sin él, el mapa es
 * interactivo.
 */
async function drawFireMap(
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
        '<span class="enlarge-hint"><span aria-hidden="true">⤢</span> Ampliar</span>',
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
  const modal = openModal(
    dateRangeLabel(p.first_detection, p.last_detection),
    "lightbox",
    period ? `Incendio de ${period}` : "Incendio",
  );
  modal.body.innerHTML = `
    <div class="lightbox-top">${pillHtml(p, period)}
      <span class="card-sensors">${sensorsLabel(p.sensors)} ·
        ${durationLabel(p.first_detection, p.last_detection, p.n_passes)}</span>
      ${globeButtonHtml(p.event_id)}</div>
    <div class="big-map"><div class="loading"><span class="spinner"></span>Cargando mapa…</div></div>
    ${statsHtml(p)}
    <p class="footnote">*La huella (área de los píxeles detectados) no es superficie quemada.
      **${escapeHtml(BIOMASS_HELP)}</p>`;
  bindGlobeButtons(modal.body);
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

// --- Histórico de una zona: indicadores, gráfico por años y tarjetas -----------------------------
export interface HistoryContext {
  /** Recuadro ``minLon,minLat,maxLon,maxLat``. */
  bbox: string;
  start: string;
  end: string;
  years: number;
  /** Enlace para abrir el histórico en una pestaña (en la propia pestaña no se pone). */
  tabUrl?: string;
}

function zoneLabel(bbox: string): string {
  const [west, south, east, north] = bbox.split(",").map(Number);
  const lat = (south + north) / 2;
  const lon = (west + east) / 2;
  const fmt = (value: number, positive: string, negative: string): string =>
    `${Math.abs(value).toFixed(2)}° ${value >= 0 ? positive : negative}`;
  // Tamaño aproximado del recuadro en km (1° de latitud ≈ 111 km).
  const height = (north - south) * 111;
  const width = (east - west) * 111 * Math.cos((lat * Math.PI) / 180);
  return `${fmt(lat, "N", "S")} · ${fmt(lon, "E", "O")} · ${width.toFixed(0)} × ${height.toFixed(0)} km`;
}

const ICONS = {
  calendar: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>`,
  pin: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>`,
  history: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/></svg>`,
  external: `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>`,
};

function kpisHtml(periods: HistoryPeriod[]): string {
  const previous = periods.filter((period) => !period.is_reference);
  const fires = previous.reduce((sum, period) => sum + period.events, 0);
  const detections = previous.reduce((sum, period) => sum + period.detections, 0);
  const withFire = previous.filter((period) => period.events > 0);
  const busiest = [...withFire].sort((a, b) => b.detections - a.detections || b.events - a.events)[0];
  const frpValues = previous.map((period) => period.frp_max_mw).filter((v): v is number => v != null);
  const frpMax = frpValues.length ? Math.max(...frpValues) : null;
  const frpYear = previous.find((period) => period.frp_max_mw === frpMax)?.label;
  return `<div class="kpis">
    <div class="kpi"><span class="kpi-label">Incendios</span><b>${formatNumber(fires)}</b>
      <small>en ${withFire.length} de ${previous.length} años</small></div>
    <div class="kpi"><span class="kpi-label">Detecciones en la zona</span><b>${formatNumber(detections)}</b>
      <small>píxeles con fuego vistos por satélite</small></div>
    <div class="kpi"><span class="kpi-label">Año con más actividad</span>
      <b>${busiest ? escapeHtml(busiest.label) : "—"}</b>
      <small>${busiest ? `${formatNumber(busiest.events)} incendios · ${formatNumber(busiest.detections)} detecciones` : "ningún incendio en la zona"}</small></div>
    <div class="kpi fire"><span class="kpi-label">FRP máximo</span>
      <b>${frpMax == null ? "—" : `${frpMax}<small> MW</small>`}</b>
      <small>${frpYear ? `en ${escapeHtml(frpYear)} · intensidad del fuego` : "sin datos"}</small></div>
  </div>`;
}

/** Gráfico de barras por año (del más antiguo al más reciente): altura = detecciones en la zona. */
function chartHtml(periods: HistoryPeriod[]): string {
  const ordered = [...periods].reverse();
  const max = Math.max(1, ...ordered.map((period) => period.detections));
  const bars = ordered.map((period) => {
    const empty = period.events === 0;
    // Raíz cuadrada: un año con 2 detecciones sigue viéndose al lado de otro con 200.
    const height = empty ? 0 : Math.max(8, Math.round((Math.sqrt(period.detections) / Math.sqrt(max)) * 100));
    const title =
      `${period.label}: ${formatNumber(period.events)} incendios, ` +
      `${formatNumber(period.detections)} detecciones` +
      (period.frp_max_mw == null ? "" : `, FRP máx. ${period.frp_max_mw} MW`);
    return `<button type="button" class="bar ${empty ? "empty" : ""} ${period.is_reference ? "reference" : ""}"
        data-period="${escapeHtml(period.label)}" title="${escapeHtml(title)}" ${empty ? "disabled" : ""}>
      <span class="bar-track">
        ${empty ? "" : `<span class="bar-fill" style="height:${height}%"><span class="bar-value">${formatNumber(period.events)}</span></span>`}
      </span>
      <span class="bar-label">${escapeHtml(period.label)}${period.is_reference ? "<em>elegido</em>" : ""}</span>
    </button>`;
  });
  return `<section class="history-chart" aria-label="Incendios por año">
    <header><h3>Incendios por año</h3>
      <span>Número de incendios sobre cada barra · altura según las detecciones en la zona ·
        pulsa un año para ver solo sus incendios</span></header>
    <div class="bars">${bars.join("")}</div>
  </section>`;
}

/**
 * Pinta en `container` el histórico de una zona: cabecera, indicadores, gráfico por años, ayuda
 * plegable y las tarjetas de los incendios, filtrables por año.
 */
export function renderHistory(
  container: HTMLElement,
  data: HistoryResponse,
  context: HistoryContext,
): void {
  const features = data.events.features.slice(0, MAX_CARDS) as FireFeature[];
  const reference = data.periods.find((period) => period.is_reference)?.label ?? context.start.slice(0, 4);
  container.innerHTML = `
    <section class="history-hero">
      <div class="history-chips">
        <span class="chip">${ICONS.calendar}${formatDayMonth(context.start)} → ${formatDayMonth(context.end)} de cada año</span>
        <span class="chip">${ICONS.pin}${zoneLabel(context.bbox)}</span>
        <span class="chip">${ICONS.history}${context.years} años anteriores a ${escapeHtml(reference)}</span>
      </div>
      ${context.tabUrl ? `<a class="hero-action" href="${context.tabUrl}" target="_blank" rel="noopener">Abrir en una pestaña ${ICONS.external}</a>` : ""}
    </section>
    ${kpisHtml(data.periods)}
    ${chartHtml(data.periods)}
    <details class="history-help">
      <summary>¿Cómo leer estos datos?</summary>
      <p>${escapeHtml(DETECTION_HELP)}</p>
      <p>${escapeHtml(DURATION_HELP)}</p>
      <p>**${escapeHtml(BIOMASS_HELP)}</p>
      <p>*La huella (área de los píxeles detectados) no es superficie quemada.</p>
    </details>
    <section class="history-fires">
      <header><h3>Incendios de años anteriores</h3>
        <div class="filter" role="group" aria-label="Filtrar por año"></div></header>
      <div class="grid"></div>
      <p class="history-empty" hidden></p>
    </section>`;

  const grid = container.querySelector<HTMLElement>(".grid")!;
  const filter = container.querySelector<HTMLElement>(".filter")!;
  const empty = container.querySelector<HTMLElement>(".history-empty")!;
  if (features.length === 0) {
    empty.hidden = false;
    empty.textContent = "No hubo incendios en esta zona en las mismas fechas de los años anteriores.";
    return;
  }
  renderCards(grid, features, true);

  // Filtro por año: chips (y las barras del gráfico) muestran solo las tarjetas de ese año.
  const counts = new Map<string, number>();
  for (const feature of features) {
    const period = String(feature.properties.period);
    counts.set(period, (counts.get(period) ?? 0) + 1);
  }
  const chip = (value: string, label: string, count: number): string =>
    `<button type="button" class="filter-chip" data-period="${escapeHtml(value)}"
      aria-pressed="${value === ""}">${escapeHtml(label)}<span>${formatNumber(count)}</span></button>`;
  filter.innerHTML = [
    chip("", "Todos", features.length),
    ...[...counts].map(([period, count]) => chip(period, period, count)),
  ].join("");

  function show(period: string): void {
    filter.querySelectorAll<HTMLButtonElement>(".filter-chip").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.period === period));
    });
    container.querySelectorAll<HTMLButtonElement>(".bar").forEach((bar) => {
      bar.classList.toggle("selected", Boolean(period) && bar.dataset.period === period);
    });
    grid.querySelectorAll<HTMLElement>(".card").forEach((element) => {
      element.hidden = Boolean(period) && element.dataset.period !== period;
    });
  }
  filter.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>(".filter-chip");
    if (button) show(button.dataset.period ?? "");
  });
  container.querySelector(".bars")!.addEventListener("click", (event) => {
    const bar = (event.target as HTMLElement).closest<HTMLButtonElement>(".bar");
    if (!bar || bar.disabled) return;
    const period = bar.dataset.period ?? "";
    // Pulsar de nuevo el año elegido vuelve a mostrar todos.
    const selected = bar.classList.contains("selected");
    show(selected || !counts.has(period) ? "" : period);
    if (!selected && counts.has(period)) {
      container.querySelector(".history-fires")!.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}

/** Indicador de carga para mostrar mientras se piden los datos del histórico. */
export function historyLoadingHtml(): string {
  return `<div class="history-loading"><span class="spinner"></span>Buscando incendios de años anteriores…</div>`;
}
