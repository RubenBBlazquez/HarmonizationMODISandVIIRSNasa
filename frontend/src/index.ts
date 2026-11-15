import L from "leaflet";
import "leaflet/dist/leaflet.css";

import { MAX_CARDS, openModal, renderCards, summaryTableHtml } from "./fireCards.js";
import {
  CONFIDENCE_COLORS,
  DETECTION_HELP,
  EVENT_COLORS,
  FRP_COLORS,
  HISTORY_YEARS,
  NO_CATEGORY_COLOR,
  pixelStyle,
  detectionPopup,
  bindPinnableTooltip,
  sortByConfidence,
  detectionTooltip,
  enableTooltipPinning,
  escapeHtml,
  eventPopup,
  fetchJson,
  firesPageUrl,
  formatDay,
  formatDayMonth,
  formatNumber,
  type FireCollection,
  type HistoryResponse,
} from "./shared.js";
import { DateRangePicker, addDays } from "./dateRange.js";
import { initActivityPanel } from "./activity.js";

const SPAIN_CENTER: L.LatLngTuple = [40.2, -3.7];
const SPAIN_ZOOM = 6;
const ITEM_LIMIT = 5000;
const REFRESH_DELAY_MS = 300;
const DEFAULT_RANGE_DAYS = 365;
// Desde este zoom las detecciones se dibujan como su píxel (su área: ancho × alto); por debajo, como puntos
// (a zoom bajo un píxel de 0,4-1 km mide menos de 1 px y no se vería).
const PIXEL_MIN_ZOOM = 10;
// Selección de zona con el ratón y búsquedas sobre ese recuadro.
const SELECTION_STYLE: L.PathOptions = {
  color: "#e2603a",
  weight: 2,
  fillColor: "#e2603a",
  fillOpacity: 0.35,
  interactive: false,
};
// Leyenda interactiva: cada elemento se puede ocultar/mostrar pulsándolo.
const LEGEND_ITEMS = [
  { key: "frp:low", label: "Detección FRP baja", color: FRP_COLORS.low },
  { key: "frp:medium", label: "Detección FRP media", color: FRP_COLORS.medium },
  { key: "frp:high", label: "Detección FRP alta", color: FRP_COLORS.high },
  { key: "status:active", label: "Evento activo", color: EVENT_COLORS.active },
  { key: "status:closed", label: "Evento cerrado", color: EVENT_COLORS.closed },
];

// Con "Comparar con todos los años" el contorno de cada elemento indica su año.
const YEAR_COLORS = [
  "#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b",
  "#17becf", "#e377c2", "#bcbd22", "#7f7f7f", "#d62728",
];
const yearColor = (year: number): string => YEAR_COLORS[year % YEAR_COLORS.length];
const featureYear = (feature: GeoJSON.Feature | undefined, field: string): number =>
  Number(String(feature?.properties?.[field] ?? "").slice(0, 4));

const SELECT_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/>
  <path d="M12 8v8M8 12h8"/></svg>`;

interface YearlyRow {
  year: number;
  events: number;
  detections: number;
  frp_max_mw: number | null;
}

interface Summary {
  valid_detections: number;
  skipped_detections: number;
  first_detection: string | null;
  last_detection: string | null;
  events: number;
  active_events: number;
}

// Último rango elegido, recordado entre visitas.
const RANGE_STORAGE_KEY = "fire-map:date-range";

function saveRange(range: { start: string; end: string } | null): void {
  try {
    if (range) localStorage.setItem(RANGE_STORAGE_KEY, JSON.stringify(range));
    else localStorage.removeItem(RANGE_STORAGE_KEY);
  } catch {
    // localStorage no disponible: simplemente no se recuerda.
  }
}

function loadRange(): { start: string; end: string } | null {
  try {
    const range = JSON.parse(localStorage.getItem(RANGE_STORAGE_KEY) ?? "null");
    const valid = (day: unknown): boolean => typeof day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(day);
    return range && valid(range.start) && valid(range.end) && range.start <= range.end ? range : null;
  } catch {
    return null;
  }
}

const mapContainer = document.querySelector<HTMLElement>(".map");
if (mapContainer) {
  mapContainer.id = "map";
  mapContainer.innerHTML = ""; // quita el dibujo decorativo de la maqueta
  const map = L.map(mapContainer, { preferCanvas: true }).setView(SPAIN_CENTER, SPAIN_ZOOM);
  enableTooltipPinning(map);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  const detectionsLayer = L.layerGroup().addTo(map);
  const eventsLayer = L.layerGroup().addTo(map);

  // --- Panel de filtros (arriba a la izquierda) ---------------------------------------------
  const filters = new L.Control({ position: "topleft" });
  filters.onAdd = () => {
    const element = L.DomUtil.create("div", "filters-control");
    element.innerHTML = `
      <label><span><input type="checkbox" id="layer-events" checked> Eventos</span></label>
      <label><span><input type="checkbox" id="layer-detections" checked> Detecciones válidas</span></label>
      <div class="date-field"><span>Fechas</span><div id="date-range"></div></div>
      <label class="months-option" for="all-years-months"
        title="Muestra el mismo rango de días (por ejemplo 12-18 ago) en cada año disponible">
        <input type="checkbox" id="all-years-months">
        <span><b>Comparar con todos los años</b>
          <small id="all-years-hint">Mismas fechas del rango en cada año</small></span>
      </label>
      <div class="years-legend" id="years-legend" hidden>
        <div class="years-title" title="Cada año se dibuja con el contorno de su color. Pulsa un año para ocultarlo o mostrarlo. Los totales son de todo el mapa y no cambian al moverlo. Evento = incendio (grupo de detecciones); detección = punto detectado por el satélite. El relleno indica la intensidad (FRP).">Color del contorno por año <span aria-hidden="true">ⓘ</span></div>
        <div class="years-chips"></div>
      </div>
      <button type="button" class="play-btn" id="play-btn"
        title="Muestra día a día cómo avanzan los incendios del rango elegido"></button>
      <button type="button" class="select-area" id="select-area"
        title="Dibuja un recuadro en el mapa. También: mantén Ctrl y arrastra"></button>
      <div class="map-status" id="map-status">Cargando…</div>`;
    L.DomEvent.disableClickPropagation(element);
    L.DomEvent.disableScrollPropagation(element);
    return element;
  };
  filters.addTo(map);

  // Lo que se oculta desde la leyenda se filtra en el cliente, sin volver a pedir datos.
  // Por defecto los eventos cerrados están ocultos: se activan desde la leyenda.
  const hiddenKeys = new Set<string>(["status:closed"]);
  let lastEvents: FireCollection | null = null;
  let lastDetections: FireCollection | null = null;
  let lastAsPixels = false;
  // Totales por año de todo el mapa: fijos, solo cambian con el rango de fechas (no al mover el mapa).
  let lastYearly: YearlyRow[] = [];
  let lastYearlyKey = "";
  const seenYears = new Set<number>(); // años ya listados: los nuevos nacen desactivados
  let lastAllYears = false; // la última carga fue de las mismas fechas en todos los años

  function syncLegend(element: HTMLElement): void {
    element.querySelectorAll<HTMLButtonElement>("button.legend-item").forEach((button) => {
      button.setAttribute("aria-pressed", String(!hiddenKeys.has(button.dataset.key ?? "")));
    });
    const reset = element.querySelector<HTMLButtonElement>(".legend-reset");
    if (reset) reset.hidden = hiddenKeys.size === 0;
  }

  const legend = new L.Control({ position: "bottomright" });
  legend.onAdd = () => {
    const element = L.DomUtil.create("div", "legend-control");
    const items = LEGEND_ITEMS.map(
      (item) => `<button type="button" class="legend-item" data-key="${item.key}"
        aria-pressed="true" title="Pulsa para ocultar o mostrar">
        <i style="--c:${item.color}"></i>${item.label}</button>`,
    );
    element.innerHTML = `${items.join("")}
      <button type="button" class="legend-reset" data-reset hidden>Mostrar todo</button>
      <div class="legend-note">Pulsa un elemento para ocultarlo o mostrarlo.<br>
        Zoom ≥ ${PIXEL_MIN_ZOOM}: área de la detección (ancho × alto, km)</div>`;
    L.DomEvent.disableClickPropagation(element); // los clics no llegan al mapa
    element.addEventListener("click", (event) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button");
      if (!button) return;
      if (button.dataset.reset !== undefined) {
        hiddenKeys.clear();
        updateYearsLegend(); // los chips de años reflejan que vuelven a estar todos activos
      } else if (button.dataset.key) {
        const key = button.dataset.key;
        if (hiddenKeys.has(key)) hiddenKeys.delete(key);
        else hiddenKeys.add(key);
      }
      syncLegend(element);
      redraw();
    });
    syncLegend(element);
    return element;
  };
  legend.addTo(map);

  const eventsToggle = document.querySelector<HTMLInputElement>("#layer-events")!;
  const detectionsToggle = document.querySelector<HTMLInputElement>("#layer-detections")!;
  // Un único selector para el rango: primer clic, día inicial; segundo clic, día final.
  const picker = new DateRangePicker(document.querySelector<HTMLElement>("#date-range")!, (range) => {
    stopPlayback();
    saveRange(range);
    updateMonthsHint();
    scheduleRefresh();
  });
  // Opción "todos los años": refresh() envía all_years=true y la API devuelve las mismas fechas de cada año.
  const allYearsToggle = document.querySelector<HTMLInputElement>("#all-years-months")!;
  const allYearsHint = document.querySelector<HTMLElement>("#all-years-hint")!;
  function updateMonthsHint(): void {
    const start = picker.value?.start;
    const end = picker.value?.end;
    allYearsHint.textContent =
      start && end
        ? `Mismas fechas (${formatDayMonth(start)} → ${formatDayMonth(end)}) en cada año`
        : "Mismas fechas del rango en cada año";
  }
  updateMonthsHint();
  // Leyenda de años (en el panel, no en el mapa): un chip por año cargado; pulsar lo oculta.
  const yearsLegend = document.querySelector<HTMLElement>("#years-legend")!;
  function updateYearsLegend(): void {
    yearsLegend.hidden = !lastAllYears || lastYearly.length === 0;
    if (yearsLegend.hidden) return;
    // Por defecto solo está activo el primer año (el más reciente); el resto se activa a mano.
    // Los años que aparecen más tarde (al mover el mapa) también nacen desactivados.
    for (const { year } of lastYearly) {
      if (!seenYears.has(year) && seenYears.size > 0) hiddenKeys.add(`year:${year}`);
      seenYears.add(year);
    }
    const rows = lastYearly.map(
      (row) => `<button type="button" class="year-chip" data-year="${row.year}"
        aria-pressed="${!hiddenKeys.has(`year:${row.year}`)}"
        title="${row.year}: ${formatNumber(row.events)} incendios (eventos) y ${formatNumber(row.detections)} detecciones de satélite en todo el mapa">
        <i style="--c:${yearColor(row.year)}"></i><b>${row.year}</b>
        <span>${formatNumber(row.events)} eventos · ${formatNumber(row.detections)} detecciones</span></button>`,
    );
    yearsLegend.querySelector<HTMLElement>(".years-chips")!.innerHTML = rows.join("");
  }
  yearsLegend.addEventListener("click", (event) => {
    const chip = (event.target as HTMLElement).closest<HTMLButtonElement>("button.year-chip");
    if (!chip) return;
    const key = `year:${chip.dataset.year}`;
    if (hiddenKeys.has(key)) hiddenKeys.delete(key);
    else hiddenKeys.add(key);
    chip.setAttribute("aria-pressed", String(!hiddenKeys.has(key)));
    redraw();
  });
  allYearsToggle.addEventListener("change", () => {
    // Al salir del modo se olvida la selección de años; al entrar, empieza solo con el primero.
    seenYears.clear();
    for (const key of [...hiddenKeys]) if (key.startsWith("year:")) hiddenKeys.delete(key);
    scheduleRefresh(); // recarga con o sin all_years y muestra el spinner de carga
  });
  const rangeStart = (): string => picker.value?.start ?? "";
  const rangeEnd = (): string => picker.value?.end ?? "";
  const statusElement = document.querySelector<HTMLElement>("#map-status")!;
  const selectButton = document.querySelector<HTMLButtonElement>("#select-area")!;

  const alerts = new L.Control({ position: "topleft" });
  alerts.onAdd = () => {
    const element = L.DomUtil.create("div", "alerts-control");
    element.innerHTML = `<button type="button" class="alerts-toggle" aria-expanded="false">
      <span class="alerts-bell" aria-hidden="true">⚠</span><span class="alerts-count">0</span>
      <span>Alertas activas</span></button><div class="alerts-list" hidden></div>`;
    L.DomEvent.disableClickPropagation(element);
    return element;
  };
  alerts.addTo(map);
  const alertsElement = document.querySelector<HTMLElement>(".alerts-control")!;
  const alertsToggle = alertsElement.querySelector<HTMLButtonElement>(".alerts-toggle")!;
  const alertsList = alertsElement.querySelector<HTMLElement>(".alerts-list")!;
  alertsToggle.addEventListener("click", () => {
    const open = alertsList.hidden;
    alertsList.hidden = !open;
    alertsToggle.setAttribute("aria-expanded", String(open));
  });

  function confidenceColor(value: unknown): string {
    return CONFIDENCE_COLORS[String(value)] ?? NO_CATEGORY_COLOR;
  }

  function updateAlerts(collection: FireCollection | null): void {
    const active = collection?.features.filter((feature) => feature.properties?.status === "active") ?? [];
    alertsToggle.querySelector<HTMLElement>(".alerts-count")!.textContent = String(active.length);
    alertsToggle.classList.toggle("has-alerts", active.length > 0);
    alertsList.innerHTML = active.length
      ? active.map((feature, index) => {
          const p = feature.properties ?? {};
          const color = confidenceColor(p.confidence);
          return `<button type="button" class="alert-item" data-index="${index}">
            <i style="--alert-color:${color}" aria-hidden="true"></i>
            <span><b>${escapeHtml(p.event_id)}</b><small>Confianza: ${escapeHtml(p.confidence)}</small></span>
          </button>`;
        }).join("")
      : `<div class="alerts-empty">No hay zonas activas</div>`;
    alertsList.querySelectorAll<HTMLButtonElement>(".alert-item").forEach((button) => {
      button.addEventListener("click", () => {
        const feature = active[Number(button.dataset.index)];
        const geometry = feature.geometry;
        if (geometry.type === "Point") map.flyTo([geometry.coordinates[1], geometry.coordinates[0]], 10);
      });
    });
  }

  function describe(label: string, collection: FireCollection | null): string | null {
    if (!collection) return null;
    const shown = formatNumber(collection.count);
    return collection.truncated
      ? `${label}: ${shown} de ${formatNumber(collection.total)} (acerca el mapa)`
      : `${label}: ${shown}`;
  }

  // Con `asPixels` la API devuelve polígonos (área del píxel); si no, puntos (círculos).
  function drawDetections(collection: FireCollection | null, asPixels: boolean): void {
    detectionsLayer.clearLayers();
    if (!collection) return;
    detectionsLayer.addLayer(
      L.geoJSON(sortByConfidence(collection as GeoJSON.FeatureCollection), {
        filter: (feature) =>
          !hiddenKeys.has(`frp:${feature.properties?.frp_category}`) &&
          !(lastAllYears && hiddenKeys.has(`year:${featureYear(feature, "acq_datetime")}`)),
        pointToLayer: (_feature, latlng) => L.circleMarker(latlng, { radius: 4 }),
        // `style` se aplica tanto a los polígonos (píxeles) como a los círculos (puntos).
        // En modo "todos los años" el contorno es del color del año.
        style: (feature) => {
          const outline = lastAllYears
            ? { color: yearColor(featureYear(feature, "acq_datetime")), weight: 2, opacity: 1 }
            : {};
          return asPixels
            ? { ...pixelStyle(feature), ...outline }
            : {
                weight: 0.5,
                color: "#ffffff",
                fillColor:
                  FRP_COLORS[String(feature?.properties?.frp_category)] ?? NO_CATEGORY_COLOR,
                fillOpacity: 0.8,
                ...outline,
              };
        },
        onEachFeature: (feature, layer) => {
          layer.bindPopup(detectionPopup(feature.properties));
          // Al pasar el ratón por el área: sensor, satélite y fecha (día-mes-año), pegado al cursor.
          bindPinnableTooltip(layer, detectionTooltip(feature.properties));
        },
      }),
    );
  }

  function drawEvents(collection: FireCollection | null): void {
    eventsLayer.clearLayers();
    updateAlerts(collection);
    if (!collection) return;
    eventsLayer.addLayer(
      L.geoJSON(collection as GeoJSON.FeatureCollection, {
        filter: (feature) =>
          !hiddenKeys.has(`status:${feature.properties?.status}`) &&
          !(lastAllYears && hiddenKeys.has(`year:${featureYear(feature, "first_detection")}`)),
        pointToLayer: (feature, latlng) => {
          const properties = feature.properties ?? {};
          if (properties.status === "active") {
            const color = confidenceColor(properties.confidence);
            return L.marker(latlng, {
              icon: L.divIcon({
                className: "active-zone-marker",
                html: `<span class="active-zone-pulse" style="--alert-color:${color}"></span>
                  <span class="active-zone-icon" style="--alert-color:${color}" aria-label="Zona activa">!</span>`,
                iconSize: [44, 44],
                iconAnchor: [22, 22],
              }),
              zIndexOffset: 500,
            });
          }
          return L.circleMarker(latlng, {
            radius: Math.min(14, 6 + Math.sqrt(Number(properties.frp_max_mw ?? 0)) / 2),
            weight: lastAllYears ? 3 : 2,
            color: lastAllYears
              ? yearColor(featureYear(feature, "first_detection"))
              : (EVENT_COLORS[String(properties.status)] ?? NO_CATEGORY_COLOR),
            fillColor: EVENT_COLORS[String(properties.status)] ?? NO_CATEGORY_COLOR,
            fillOpacity: 0.25,
          });
        },
        onEachFeature: (feature, layer) => {
          const properties = feature.properties ?? {};
          const point = feature.geometry.type === "Point" ? feature.geometry.coordinates : null;
          const googleMapsButton = properties.status === "active" && point
            ? `<br><a class="google-maps-report" target="_blank" rel="noopener noreferrer"
                href="https://www.google.com/maps/search/?api=1&query=${point[1]},${point[0]}">
                Reportar en Google Maps</a>`
            : "";
          layer.bindPopup(`${eventPopup(properties)}${googleMapsButton}`);
        },
      }),
    );
  }

  /** Vuelve a dibujar lo último cargado (al cambiar lo que oculta la leyenda). */
  // Con la reproducción activa solo se dibuja lo ocurrido hasta `playDay` (acumulado).
  let playDay: string | null = null;
  const upTo = (collection: FireCollection | null, field: string): FireCollection | null => {
    if (!collection || !playDay) return collection;
    const features = collection.features.filter(
      (feature) => String(feature.properties?.[field] ?? "9999").slice(0, 10) <= playDay!,
    );
    return { ...collection, features, count: features.length };
  };
  function redraw(): void {
    drawEvents(upTo(lastEvents, "first_detection"));
    drawDetections(upTo(lastDetections, "acq_datetime"), lastAsPixels);
  }

  // --- Carga de datos: solo lo visible, con las fechas elegidas -----------------------------
  let controller: AbortController | null = null;
  let timer: number | undefined;

  // Contadores por año: globales (todo el mapa), los cachea la API y solo se piden al cambiar el
  // rango. Van aparte del mapa para que no lo retrasen.
  let yearlyRequested = "";
  async function loadYearly(start: string, end: string): Promise<void> {
    const key = `${start}|${end}`;
    if (key === lastYearlyKey || key === yearlyRequested) return;
    yearlyRequested = key;
    try {
      const data = await fetchJson<{ years: YearlyRow[] }>("/yearly", { start, end });
      if (yearlyRequested !== key) return; // llegó tarde: ya se pidió otro rango
      lastYearly = data.years;
      lastYearlyKey = key;
      if (lastAllYears) {
        updateYearsLegend();
        updateYearsStatus(key);
        redraw();
      }
    } catch {
      yearlyRequested = ""; // se reintentará en la siguiente carga
    }
  }

  function updateYearsStatus(key: string): void {
    if (lastYearlyKey !== key) {
      statusElement.textContent = "Cargando los contadores por año…";
      return;
    }
    const total = (field: "events" | "detections"): number =>
      lastYearly.reduce((sum, row) => sum + row[field], 0);
    statusElement.textContent =
      `${lastYearly.length} años · ${formatNumber(total("events"))} eventos · ` +
      `${formatNumber(total("detections"))} detecciones (detalle por año en la leyenda)`;
  }

  async function refresh(): Promise<void> {
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    const params: Record<string, string> = {
      bbox: map.getBounds().toBBoxString(),
      limit: String(ITEM_LIMIT),
    };
    if (rangeStart()) params.start = rangeStart();
    if (rangeEnd()) params.end = rangeEnd();
    // Mismas fechas del rango en todos los años: lo resuelve la API (necesita start y end).
    if (allYearsToggle.checked && rangeStart() && rangeEnd()) params.all_years = "true";

    const asPixels = map.getZoom() >= PIXEL_MIN_ZOOM;
    const detectionParams = { ...params, geometry: asPixels ? "pixel" : "point" };

    statusElement.textContent = allYearsToggle.checked
      ? "Cargando las mismas fechas de todos los años…"
      : "Cargando…";
    statusElement.classList.add("loading");
    try {
      const allYears = params.all_years === "true";
      const yearlyKey = `${params.start}|${params.end}`;
      if (allYears) void loadYearly(params.start, params.end); // contadores: no bloquean el mapa
      const [events, detections] = await Promise.all([
        eventsToggle.checked ? fetchJson<FireCollection>("/events", params, signal) : null,
        detectionsToggle.checked
          ? fetchJson<FireCollection>("/detections", detectionParams, signal)
          : null,
      ]);
      [lastEvents, lastDetections, lastAsPixels] = [events, detections, asPixels];
      lastAllYears = params.all_years === "true";
      updateYearsLegend();
      redraw();
      if (allYears) {
        updateYearsStatus(yearlyKey);
        return;
      }
      const parts = [describe("Eventos", events), describe("Detecciones", detections)];
      statusElement.textContent = parts.filter(Boolean).join(" · ") || "Sin capas activas";
      if (hiddenKeys.size > 0) statusElement.textContent += " · hay elementos ocultos (leyenda)";
      if (detections && !asPixels) {
        statusElement.textContent += ` · acerca el mapa (zoom ${PIXEL_MIN_ZOOM}) para ver el píxel`;
      }
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      statusElement.textContent = "No se pudieron cargar los datos. ¿Está la API en marcha?";
    } finally {
      // Si la petición fue cancelada por otra más nueva, el spinner sigue hasta que acabe esa.
      if (!signal.aborted) statusElement.classList.remove("loading");
    }
  }

  // --- Reproducción: avanza un día por segundo dentro del rango elegido ----------------------
  const PLAY_STEP_MS = 1000;
  const playButton = document.querySelector<HTMLButtonElement>("#play-btn")!;
  let playTimer: number | undefined;
  const PLAY_ICON = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"
    aria-hidden="true"><path d="M7 4l13 8-13 8z"/></svg>`;
  const STOP_ICON = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"
    aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>`;
  playButton.innerHTML = `${PLAY_ICON}<span>Ver avance día a día</span>`;

  function stopPlayback(): void {
    window.clearTimeout(playTimer);
    playTimer = undefined;
    if (playDay === null) return;
    playDay = null;
    playButton.classList.remove("playing");
    playButton.innerHTML = `${PLAY_ICON}<span>Ver avance día a día</span>`;
    redraw();
    scheduleRefresh(); // recupera el estado de estado/contadores
  }

  async function startPlayback(): Promise<void> {
    if (!rangeStart() || !rangeEnd()) {
      statusElement.textContent = "Elige un rango de fechas para ver el avance";
      return;
    }
    const first = rangeStart();
    const last = rangeEnd();
    playButton.classList.add("playing");
    playButton.innerHTML = `${STOP_ICON}<span>Detener</span>`;
    playDay = first;
    window.clearTimeout(timer);
    await refresh(); // carga de una vez todo el rango; los días se filtran sin pedir más datos
    if (playDay === null) return; // detenido mientras cargaba
    const step = (): void => {
      if (playDay === null) return;
      redraw();
      const shown = (lastEvents ? upTo(lastEvents, "first_detection")!.count : 0) +
        (lastDetections ? upTo(lastDetections, "acq_datetime")!.count : 0);
      statusElement.textContent = `Avance: ${formatDay(playDay)} (hasta ${formatDay(last)}) · ${formatNumber(shown)} elementos`;
      if (playDay >= last) {
        playTimer = undefined;
        playButton.classList.remove("playing");
        playButton.innerHTML = `${PLAY_ICON}<span>Repetir avance</span>`;
        return; // se queda mostrando el rango completo; pulsar de nuevo vuelve a empezar
      }
      playDay = addDays(playDay, 1);
      playTimer = window.setTimeout(step, PLAY_STEP_MS);
    };
    step();
  }

  playButton.addEventListener("click", () => {
    if (playButton.classList.contains("playing")) stopPlayback();
    else {
      playDay = null;
      void startPlayback();
    }
  });

  function scheduleRefresh(): void {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => void refresh(), REFRESH_DELAY_MS);
  }

  map.on("moveend", scheduleRefresh);
  for (const input of [eventsToggle, detectionsToggle]) {
    input.addEventListener("change", scheduleRefresh);
  }

  // Rango inicial: el último año con datos (los datos llegan hasta la última detección válida).
  async function start(): Promise<void> {
    try {
      const summary = await fetchJson<Summary>("/summary", {});
      if (summary.first_detection && summary.last_detection) {
        const last = summary.last_detection.slice(0, 10);
        picker.setBounds(summary.first_detection.slice(0, 10), last);
        picker.setRange(
          loadRange() ?? { start: addDays(last, -(DEFAULT_RANGE_DAYS - 1)), end: last },
        );
      }
    } catch {
      // Sin resumen: se muestran todas las fechas y refresh() informará del error.
    }
    await refresh();
  }
  void start();

  // --- Selección de zona: arrastrar un recuadro y elegir qué buscar en él ---------------------
  let selecting = false;
  let anchor: L.LatLng | null = null;
  let selection: L.Rectangle | null = null;

  // "Años anteriores": misma zona y mismas fechas del año en los años anteriores, en un popup grande
  // con el resumen por años y el mapa de cada incendio (un clic en un mapa lo amplía).
  async function showHistory(bounds: L.LatLngBounds): Promise<void> {
    const title = "Incendios de años anteriores";
    const modal = openModal(title);
    if (!rangeStart() || !rangeEnd()) {
      modal.body.innerHTML =
        "<p>Elige las fechas Desde y Hasta para comparar con años anteriores.</p>";
      return;
    }
    modal.body.innerHTML = '<p class="modal-status">Buscando…</p>';
    try {
      const query = {
        bbox: bounds.toBBoxString(),
        start: rangeStart(),
        end: rangeEnd(),
        years: String(HISTORY_YEARS),
      };
      const data = await fetchJson<HistoryResponse>("/history", { ...query, limit: String(MAX_CARDS) });
      const shown = Math.min(data.events.features.length, MAX_CARDS);
      modal.body.innerHTML = `
        <p class="modal-criteria">Incendios con detecciones dentro del recuadro, en las mismas fechas
          (${formatDayMonth(rangeStart())} → ${formatDayMonth(rangeEnd())}) en los
          ${HISTORY_YEARS} años anteriores.
          <a href="${firesPageUrl({ mode: "history", ...query })}" target="_blank"
          rel="noopener">Abrir en una pestaña ↗</a></p>
        <p class="modal-help">${escapeHtml(DETECTION_HELP)}<br>*La huella no es superficie quemada.
          Pulsa un mapa para verlo más grande.</p>
        <table class="summary">${summaryTableHtml(data.periods)}</table>
        <p class="modal-status">${formatNumber(shown)} incendios de años anteriores, los mayores primero.</p>
        <section class="grid"></section>`;
      renderCards(modal.body.querySelector<HTMLElement>(".grid")!, data.events.features, true);
    } catch (error) {
      modal.body.innerHTML = `<p>${escapeHtml((error as Error).message)}</p>`;
    }
  }

  function clearSelection(): void {
    selection?.remove();
    selection = null;
  }

  function openSelectionMenu(bounds: L.LatLngBounds): void {
    const element = document.createElement("div");
    element.className = "selection-menu";
    element.innerHTML = `
      <b>Zona seleccionada</b>
      <small>Lat ${bounds.getSouth().toFixed(3)} a ${bounds.getNorth().toFixed(3)} ·
        Lon ${bounds.getWest().toFixed(3)} a ${bounds.getEast().toFixed(3)}</small>
      <button type="button" data-action="history">Incendios de los últimos ${HISTORY_YEARS} años</button>
      <button type="button" data-action="predict" disabled
        title="Predicción con series temporales: ¿habrá fuego hoy o en X tiempo en esta zona?">
        Predecir fuego en la zona (series temporales) <em>· Próximamente</em></button>
      <button type="button" data-action="clear">Quitar selección</button>`;
    element.querySelectorAll<HTMLButtonElement>("button[data-action]").forEach((button) =>
      button.addEventListener("click", () => {
        map.closePopup();
        const action = button.dataset.action;
        if (action === "history") void showHistory(bounds);
        else clearSelection();
      }),
    );
    // closeOnClick: false -> el clic que termina el arrastre no cierra el menú nada más abrirlo.
    L.popup({ closeOnClick: false, maxWidth: 280 })
      .setLatLng(bounds.getCenter())
      .setContent(element)
      .openOn(map);
  }

  function setSelecting(active: boolean): void {
    selecting = active;
    anchor = null;
    selectButton.classList.toggle("active", active);
    selectButton.innerHTML = `${SELECT_ICON}<span>${
      active ? "Arrastra sobre el mapa… (Esc cancela)" : "Seleccionar zona"
    }</span>${active ? "" : "<kbd>Ctrl</kbd>"}`;
    map.getContainer().style.cursor = active ? "crosshair" : "";
    if (active) {
      map.dragging.disable();
      map.boxZoom.disable();
    } else {
      map.dragging.enable();
      map.boxZoom.enable();
    }
  }

  setSelecting(false); // pinta el botón (icono + texto)
  selectButton.addEventListener("click", () => {
    if (selecting && anchor) clearSelection(); // arrastre a medias: se descarta
    setSelecting(!selecting);
  });
  // Con Ctrl pulsado el mapa selecciona zona sin pulsar el botón (al soltarlo, vuelve a arrastrar).
  let ctrlSelecting = false;
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      // Esc cancela el modo selección y quita la zona seleccionada (rectángulo, menú y resultados).
      if (selecting) setSelecting(false);
      map.closePopup();
      clearSelection();
    }
    if (event.key === "Control" && !selecting) {
      ctrlSelecting = true;
      setSelecting(true);
    }
  });
  document.addEventListener("keyup", (event) => {
    if (event.key === "Control" && ctrlSelecting && !anchor) setSelecting(false);
    if (event.key === "Control") ctrlSelecting = false;
  });
  window.addEventListener("blur", () => {
    if (ctrlSelecting && !anchor) setSelecting(false);
    ctrlSelecting = false;
  });

  map.on("mousedown", (event: L.LeafletMouseEvent) => {
    if (!selecting) return;
    map.closePopup(); // el menú de una selección anterior no debe quedar debajo del arrastre
    anchor = event.latlng;
    selection?.remove();
    selection = L.rectangle(L.latLngBounds(anchor, anchor), SELECTION_STYLE).addTo(map);
  });
  // El arrastre se sigue en `document`, no en el mapa: si el cursor pasa por un menú, un control
  // o un tooltip, o se suelta fuera del mapa, el mapa no recibe el `mouseup` y la selección
  // quedaba a medias (modo selección atascado, y el botón dejaba de responder).
  document.addEventListener("mousemove", (event: MouseEvent) => {
    if (!selecting || !anchor || !selection) return;
    selection.setBounds(L.latLngBounds(anchor, map.mouseEventToLatLng(event)));
  });
  // Al soltar sobre una detección o un evento, el navegador lanza además un `click` que abría su
  // popup y cerraba el menú de la zona (solo hay un popup a la vez). Ese clic se descarta.
  let swallowClick = false;
  map.getContainer().addEventListener("click", (event) => {
    if (!swallowClick) return;
    event.stopPropagation(); // en captura: Leaflet no llega a verlo
  }, true);
  document.addEventListener("mouseup", (event: MouseEvent) => {
    if (!selecting || !anchor || !selection) return;
    swallowClick = true;
    window.setTimeout(() => (swallowClick = false), 100);
    const bounds = L.latLngBounds(anchor, map.mouseEventToLatLng(event));
    setSelecting(false);
    if (bounds.getNorthEast().equals(bounds.getSouthWest())) {
      clearSelection(); // un clic sin arrastrar no es una zona
      return;
    }
    selection.setBounds(bounds);
    openSelectionMenu(bounds);
  });

  // --- Coordenadas del cursor (arriba a la derecha) -----------------------------------------
  initActivityPanel(map);

  const coordinates = new L.Control({ position: "bottomleft" });
  coordinates.onAdd = () => {
    const element = L.DomUtil.create("div", "coordinates-control");
    element.innerHTML = "Mueve el cursor sobre el mapa";
    return element;
  };
  coordinates.addTo(map);

  map.on("mousemove", (event: L.LeafletMouseEvent) => {
    const element = document.querySelector<HTMLElement>(".coordinates-control");
    if (element) {
      element.innerHTML = `Latitud: <b>${event.latlng.lat.toFixed(5)}°</b><br>Longitud: <b>${event.latlng.lng.toFixed(5)}°</b>`;
    }
  });
}
