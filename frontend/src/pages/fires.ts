// Página fires.html: todos los incendios de una búsqueda del mapa, una tarjeta con su mapa por
// incendio. Parámetros de la URL: mode=zone|history, bbox, start, end y (en history) years.
//
// Orden de los estilos: el mismo que en el globo (ver pages/globe.ts).
import "leaflet/dist/leaflet.css";
import "../styles/fireCards.css";

import { getFireLayer } from "../api/endpoints.js";
import type { FireFeature } from "../api/types.js";
import { renderCards } from "../components/fireCards/fireCards.js";
import { loadHistoryInto } from "../components/zoneHistory/zoneHistory.js";
import { HISTORY_YEARS, MAX_CARDS } from "../domain/config.js";
import { BIOMASS_HELP, DETECTION_HELP, DURATION_HELP, FOOTPRINT_NOTE } from "../domain/help.js";
import { formatDay } from "../lib/dates.js";
import { $ } from "../lib/dom.js";
import { formatNumber } from "../lib/format.js";
import { escapeHtml } from "../lib/html.js";

const ZONE_QUERY_LIMIT = 5000;

const params = new URLSearchParams(window.location.search);
const mode = params.get("mode") === "history" ? "history" : "zone";
const bbox = params.get("bbox") ?? "";
const start = params.get("start") ?? "";
const end = params.get("end") ?? "";
const years = params.get("years") ?? String(HISTORY_YEARS);

const titleElement = $("#page-title");
const criteriaElement = $("#criteria");
const statusElement = $("#status");
const historyElement = $("#history");
const helpElement = $("#help");
const gridElement = $("#grid");

helpElement.innerHTML = [DETECTION_HELP, DURATION_HELP, `**${BIOMASS_HELP}`, FOOTPRINT_NOTE]
  .map((text) => escapeHtml(text))
  .join("<br>");

function zoneText(): string {
  const [west, south, east, north] = bbox.split(",").map(Number);
  return `Zone: lat ${south.toFixed(3)} to ${north.toFixed(3)} · lon ${west.toFixed(3)} to ${east.toFixed(3)}`;
}

/** Histórico de la zona: trae su propia cabecera, indicadores, gráfico y ayuda. */
async function loadHistory(): Promise<void> {
  titleElement.textContent = "Fires from previous years";
  for (const element of [criteriaElement, helpElement, statusElement]) element.hidden = true;
  historyElement.hidden = false;
  await loadHistoryInto(historyElement, { bbox, start, end, years: Number(years) });
}

/** Incendios con detecciones dentro de la zona, de mayor a menor. */
async function loadZone(): Promise<void> {
  titleElement.textContent = "Fires in the zone";
  const dates = start && end ? ` · from ${formatDay(start)} to ${formatDay(end)}` : "";
  criteriaElement.textContent = `${zoneText()}${dates}.`;
  const query: Record<string, string> = {
    bbox,
    limit: String(ZONE_QUERY_LIMIT),
    match: "inside", // solo incendios con detecciones dentro de la zona
  };
  if (start) query.start = start;
  if (end) query.end = end;
  try {
    const data = await getFireLayer("events", query);
    const inZone = (feature: FireFeature): number =>
      Number(feature.properties.zone_detections ?? feature.properties.n_detections);
    const largest = [...data.features].sort((a, b) => inZone(b) - inZone(a));
    const shown = Math.min(largest.length, MAX_CARDS);
    statusElement.textContent = `${formatNumber(data.total)} fires; showing the ${formatNumber(shown)} largest. *The footprint is not burned area.`;
    renderCards(gridElement, largest, false);
  } catch (error) {
    statusElement.textContent = `Could not load the fires: ${(error as Error).message}`;
  }
}

if (!bbox) statusElement.textContent = "Missing zone. Open this page from the map (Select zone).";
else if (mode === "history") void loadHistory();
else void loadZone();
