import L from "leaflet";
import "leaflet/dist/leaflet.css";

import {
  BIOMASS_HELP,
  DETECTION_HELP,
  EVENT_COLORS,
  FRP_COLORS,
  HISTORY_YEARS,
  NO_CATEGORY_COLOR,
  PIXEL_BORDER_COLOR,
  PIXEL_FILL_OPACITY,
  biomassLabel,
  detectionPopup,
  detectionTooltip,
  escapeHtml,
  eventPopup,
  fetchJson,
  firesPageUrl,
  formatDay,
  sensorsLabel,
  formatDayMonth,
  formatNumber,
  zoneDetectionsLabel,
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
// Desde este zoom las detecciones se dibujan como su píxel (scan × track); por debajo, como puntos
// (a zoom bajo un píxel de 0,4-1 km mide menos de 1 px y no se vería).
const PIXEL_MIN_ZOOM = 10;
// Selección de zona con el ratón y búsquedas sobre ese recuadro.
const ZONE_EVENT_LIMIT = 500;
const LIST_ITEMS = 15;
const SELECTION_STYLE: L.PathOptions = {
  color: "#e2603a",
  weight: 2,
  fillColor: "#e2603a",
  fillOpacity: 0.35,
  interactive: false,
};
const HISTORY_COLOR = "#7b4fbf";
// Leyenda interactiva: cada elemento se puede ocultar/mostrar pulsándolo.
const LEGEND_ITEMS = [
  { key: "frp:baja", label: "Detección FRP baja", color: FRP_COLORS.baja },
  { key: "frp:media", label: "Detección FRP media", color: FRP_COLORS.media },
  { key: "frp:alta", label: "Detección FRP alta", color: FRP_COLORS.alta },
  { key: "status:active", label: "Evento activo", color: EVENT_COLORS.active },
  { key: "status:closed", label: "Evento cerrado", color: EVENT_COLORS.closed },
];

const SELECT_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/>
  <path d="M12 8v8M8 12h8"/></svg>`;

interface Summary {
  valid_detections: number;
  skipped_detections: number;
  first_detection: string | null;
  last_detection: string | null;
  events: number;
  active_events: number;
}

const mapContainer = document.querySelector<HTMLElement>(".map");
if (mapContainer) {
  mapContainer.id = "map";
  mapContainer.innerHTML = ""; // quita el dibujo decorativo de la maqueta
  const map = L.map(mapContainer, { preferCanvas: true }).setView(SPAIN_CENTER, SPAIN_ZOOM);
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
      <button type="button" class="select-area" id="select-area"
        title="Dibuja un recuadro en el mapa. También: mantén Ctrl y arrastra"></button>
      <div class="map-status" id="map-status">Cargando…</div>`;
    L.DomEvent.disableClickPropagation(element);
    L.DomEvent.disableScrollPropagation(element);
    return element;
  };
  filters.addTo(map);

  // Lo que se oculta desde la leyenda se filtra en el cliente, sin volver a pedir datos.
  const hiddenKeys = new Set<string>();
  let lastEvents: FireCollection | null = null;
  let lastDetections: FireCollection | null = null;
  let lastAsPixels = false;

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
        Zoom ≥ ${PIXEL_MIN_ZOOM}: área del píxel (scan × track)</div>`;
    L.DomEvent.disableClickPropagation(element); // los clics no llegan al mapa
    element.addEventListener("click", (event) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button");
      if (!button) return;
      if (button.dataset.reset !== undefined) {
        hiddenKeys.clear();
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
  const picker = new DateRangePicker(document.querySelector<HTMLElement>("#date-range")!, () =>
    scheduleRefresh(),
  );
  const rangeStart = (): string => picker.value?.start ?? "";
  const rangeEnd = (): string => picker.value?.end ?? "";
  const statusElement = document.querySelector<HTMLElement>("#map-status")!;
  const selectButton = document.querySelector<HTMLButtonElement>("#select-area")!;

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
      L.geoJSON(collection as GeoJSON.FeatureCollection, {
        filter: (feature) => !hiddenKeys.has(`frp:${feature.properties?.frp_category}`),
        pointToLayer: (_feature, latlng) => L.circleMarker(latlng, { radius: 4 }),
        // `style` se aplica tanto a los polígonos (píxeles) como a los círculos (puntos).
        style: (feature) => ({
          weight: asPixels ? 1 : 0.5,
          color: asPixels ? PIXEL_BORDER_COLOR : "#ffffff",
          fillColor: FRP_COLORS[String(feature?.properties?.frp_category)] ?? NO_CATEGORY_COLOR,
          fillOpacity: asPixels ? PIXEL_FILL_OPACITY : 0.8,
        }),
        onEachFeature: (feature, layer) => {
          layer.bindPopup(detectionPopup(feature.properties));
          // Al pasar el ratón por el área: sensor, satélite y fecha (día-mes-año), pegado al cursor.
          layer.bindTooltip(detectionTooltip(feature.properties), {
            sticky: true,
            direction: "top",
          });
        },
      }),
    );
  }

  function drawEvents(collection: FireCollection | null): void {
    eventsLayer.clearLayers();
    if (!collection) return;
    eventsLayer.addLayer(
      L.geoJSON(collection as GeoJSON.FeatureCollection, {
        filter: (feature) => !hiddenKeys.has(`status:${feature.properties?.status}`),
        pointToLayer: (feature, latlng) =>
          L.circleMarker(latlng, {
            radius: Math.min(14, 6 + Math.sqrt(Number(feature.properties?.frp_max_mw ?? 0)) / 2),
            weight: 2,
            color: EVENT_COLORS[String(feature.properties?.status)] ?? NO_CATEGORY_COLOR,
            fillColor: EVENT_COLORS[String(feature.properties?.status)] ?? NO_CATEGORY_COLOR,
            fillOpacity: 0.25,
          }),
        onEachFeature: (feature, layer) => layer.bindPopup(eventPopup(feature.properties)),
      }),
    );
  }

  /** Vuelve a dibujar lo último cargado (al cambiar lo que oculta la leyenda). */
  function redraw(): void {
    drawEvents(lastEvents);
    drawDetections(lastDetections, lastAsPixels);
  }

  // --- Carga de datos: solo lo visible, con las fechas elegidas -----------------------------
  let controller: AbortController | null = null;
  let timer: number | undefined;

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

    const asPixels = map.getZoom() >= PIXEL_MIN_ZOOM;
    const detectionParams = { ...params, geometry: asPixels ? "pixel" : "point" };

    statusElement.textContent = "Cargando…";
    try {
      const [events, detections] = await Promise.all([
        eventsToggle.checked ? fetchJson<FireCollection>("/events", params, signal) : null,
        detectionsToggle.checked
          ? fetchJson<FireCollection>("/detections", detectionParams, signal)
          : null,
      ]);
      [lastEvents, lastDetections, lastAsPixels] = [events, detections, asPixels];
      redraw();
      const parts = [describe("Eventos", events), describe("Detecciones", detections)];
      statusElement.textContent = parts.filter(Boolean).join(" · ") || "Sin capas activas";
      if (hiddenKeys.size > 0) statusElement.textContent += " · hay elementos ocultos (leyenda)";
      if (detections && !asPixels) {
        statusElement.textContent += ` · acerca el mapa (zoom ${PIXEL_MIN_ZOOM}) para ver el píxel`;
      }
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      statusElement.textContent = "No se pudieron cargar los datos. ¿Está la API en marcha?";
    }
  }

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
        picker.setRange({ start: addDays(last, -(DEFAULT_RANGE_DAYS - 1)), end: last });
      }
    } catch {
      // Sin resumen: se muestran todas las fechas y refresh() informará del error.
    }
    await refresh();
  }
  void start();

  // --- Selección de zona: arrastrar un recuadro y elegir qué buscar en él ---------------------
  const historyLayer = L.layerGroup().addTo(map);
  let selecting = false;
  let anchor: L.LatLng | null = null;
  let selection: L.Rectangle | null = null;

  const results = new L.Control({ position: "bottomleft" });
  results.onAdd = () => {
    const element = L.DomUtil.create("div", "results-control");
    element.hidden = true;
    L.DomEvent.disableClickPropagation(element);
    L.DomEvent.disableScrollPropagation(element);
    return element;
  };
  results.addTo(map);
  const resultsElement = document.querySelector<HTMLElement>(".results-control")!;

  function hideResults(): void {
    resultsElement.hidden = true;
    historyLayer.clearLayers();
  }

  function showResults(title: string, body: string): void {
    resultsElement.innerHTML = `
      <div class="results-head"><b>${escapeHtml(title)}</b>
        <button type="button" class="results-close" aria-label="Cerrar">✕</button></div>
      <div class="results-body">${body}</div>`;
    resultsElement.hidden = false;
    resultsElement.querySelector(".results-close")?.addEventListener("click", hideResults);
    // Cada incendio de la lista centra el mapa en él.
    resultsElement.querySelectorAll<HTMLButtonElement>("button[data-lat]").forEach((button) =>
      button.addEventListener("click", () =>
        map.flyTo([Number(button.dataset.lat), Number(button.dataset.lon)], 12),
      ),
    );
  }

  function eventList(features: FireCollection["features"], withPeriod: boolean): string {
    if (features.length === 0) return "<p>No hay incendios.</p>";
    const items = features.map((feature) => {
      const [lon, lat] = (feature.geometry as GeoJSON.Point).coordinates;
      const properties = feature.properties;
      const period = withPeriod ? `<b>${escapeHtml(properties.period)}</b> · ` : "";
      return `<li><button type="button" data-lat="${lat}" data-lon="${lon}">${period}
        ${formatDay(properties.first_detection)} · ${zoneDetectionsLabel(properties)} ·
        FRP máx. ${escapeHtml(properties.zone_frp_max_mw ?? properties.frp_max_mw)} MW ·
        biomasa ${biomassLabel(properties.biomass_burned_kg)}</button></li>`;
    });
    return `<ol class="results-list">${items.join("")}</ol>`;
  }

  function periodText(): string {
    if (rangeStart() && rangeEnd()) {
      return `entre ${formatDay(rangeStart())} y ${formatDay(rangeEnd())}`;
    }
    return "en todas las fechas";
  }

  async function showZoneEvents(bounds: L.LatLngBounds): Promise<void> {
    const title = "Incendios en la zona";
    const params: Record<string, string> = {
      bbox: bounds.toBBoxString(),
      limit: String(ZONE_EVENT_LIMIT),
      match: "inside", // solo incendios con detecciones dentro del recuadro dibujado
    };
    if (rangeStart()) params.start = rangeStart();
    if (rangeEnd()) params.end = rangeEnd();

    showResults(title, "<p>Buscando…</p>");
    try {
      const data = await fetchJson<FireCollection>("/events", params);
      const inZone = (feature: FireCollection["features"][number]): number =>
        Number(feature.properties.zone_detections ?? feature.properties.n_detections);
      const largest = [...data.features].sort((a, b) => inZone(b) - inZone(a)).slice(0, LIST_ITEMS);
      const tab = { mode: "zone", ...params };
      const header = `<p>${formatNumber(data.total)} incendios con detecciones dentro del
        recuadro ${periodText()}.
        <a class="open-tab" href="${firesPageUrl(tab)}" target="_blank" rel="noopener">
        Ver todos con sus mapas en una pestaña ↗</a></p>
        <p class="results-help">${DETECTION_HELP}</p>
        <p class="results-help">${BIOMASS_HELP}</p><p>Los mayores:</p>`;
      showResults(title, header + eventList(largest, false));
    } catch (error) {
      showResults(title, `<p>${escapeHtml((error as Error).message)}</p>`);
    }
  }

  function drawHistory(collection: HistoryResponse["events"]): void {
    historyLayer.clearLayers();
    historyLayer.addLayer(
      L.geoJSON(collection as GeoJSON.FeatureCollection, {
        pointToLayer: (_feature, latlng) => L.circleMarker(latlng, { radius: 7 }),
        style: () => ({ color: HISTORY_COLOR, weight: 2, fillColor: HISTORY_COLOR, fillOpacity: 0.3 }),
        onEachFeature: (feature, layer) => {
          layer.bindPopup(eventPopup(feature.properties));
          layer.bindTooltip(
            `${feature.properties.period} · ${formatDay(feature.properties.first_detection)} · ` +
              sensorsLabel(feature.properties.sensors),
            { sticky: true, direction: "top" },
          );
        },
      }),
    );
  }

  // "Similares": misma zona y mismas fechas del año en los años anteriores.
  async function showHistory(bounds: L.LatLngBounds): Promise<void> {
    const title = "Incendios similares en años anteriores";
    if (!rangeStart() || !rangeEnd()) {
      showResults(title, "<p>Elige las fechas Desde y Hasta para comparar con años anteriores.</p>");
      return;
    }

    showResults(title, "<p>Buscando…</p>");
    try {
      const query = {
        bbox: bounds.toBBoxString(),
        start: rangeStart(),
        end: rangeEnd(),
        years: String(HISTORY_YEARS),
      };
      const data = await fetchJson<HistoryResponse>("/history", query);
      drawHistory(data.events);
      const rows = data.periods.map(
        (period) => `<tr class="${period.is_reference ? "reference" : ""}">
          <td>${escapeHtml(period.label)}${period.is_reference ? " (elegido)" : ""}</td>
          <td>${formatNumber(period.events)}</td><td>${formatNumber(period.detections)}</td>
          <td>${period.frp_max_mw ?? "—"}</td></tr>`,
      );
      const criteria = `<p>Incendios con detecciones dentro del recuadro, en las mismas fechas
        (${formatDayMonth(rangeStart())} →
        ${formatDayMonth(rangeEnd())}) en los ${HISTORY_YEARS} años anteriores.
        En morado en el mapa.
        <a class="open-tab" href="${firesPageUrl({ mode: "history", ...query })}" target="_blank"
        rel="noopener">Ver todos con sus mapas en una pestaña ↗</a></p>
        <p class="results-help">${DETECTION_HELP}</p>`;
      const table = `<table class="results-table"><thead><tr><th>Periodo</th><th>Incendios</th>
        <th>Detecciones en la zona</th><th>FRP máx. en la zona (MW)</th></tr></thead>
        <tbody>${rows.join("")}</tbody></table>`;
      const list = `<p><b>Mayores incendios de años anteriores</b></p>${eventList(
        data.events.features.slice(0, LIST_ITEMS),
        true,
      )}`;
      showResults(title, criteria + table + list);
    } catch (error) {
      showResults(title, `<p>${escapeHtml((error as Error).message)}</p>`);
    }
  }

  function clearSelection(): void {
    selection?.remove();
    selection = null;
    hideResults();
  }

  function openSelectionMenu(bounds: L.LatLngBounds): void {
    const element = document.createElement("div");
    element.className = "selection-menu";
    element.innerHTML = `
      <b>Zona seleccionada</b>
      <small>Lat ${bounds.getSouth().toFixed(3)} a ${bounds.getNorth().toFixed(3)} ·
        Lon ${bounds.getWest().toFixed(3)} a ${bounds.getEast().toFixed(3)}</small>
      <button type="button" data-action="zone">Incendios en esta zona (fechas elegidas)</button>
      <button type="button" data-action="history">Incendios similares en los últimos ${HISTORY_YEARS} años</button>
      <button type="button" data-action="zoom">Acercar a la zona</button>
      <button type="button" data-action="clear">Quitar selección</button>`;
    element.querySelectorAll<HTMLButtonElement>("button[data-action]").forEach((button) =>
      button.addEventListener("click", () => {
        map.closePopup();
        const action = button.dataset.action;
        if (action === "zone") void showZoneEvents(bounds);
        else if (action === "history") void showHistory(bounds);
        else if (action === "zoom") map.fitBounds(bounds);
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
  selectButton.addEventListener("click", () => setSelecting(!selecting));
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
    anchor = event.latlng;
    selection?.remove();
    selection = L.rectangle(L.latLngBounds(anchor, anchor), SELECTION_STYLE).addTo(map);
  });
  map.on("mousemove", (event: L.LeafletMouseEvent) => {
    if (selecting && anchor && selection) selection.setBounds(L.latLngBounds(anchor, event.latlng));
  });
  map.on("mouseup", (event: L.LeafletMouseEvent) => {
    if (!selecting || !anchor || !selection) return;
    const bounds = L.latLngBounds(anchor, event.latlng);
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

  const coordinates = new L.Control({ position: "topright" });
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
