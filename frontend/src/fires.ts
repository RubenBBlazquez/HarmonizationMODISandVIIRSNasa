// Pestaña con todos los incendios de una búsqueda del mapa: una tarjeta con su mapa por incendio.
// Parámetros de la URL: mode=zone|history, bbox, start, end y (en history) years.
import { MAX_CARDS, renderCards, summaryTableHtml } from "./fireCards.js";
import {
  BIOMASS_HELP,
  DETECTION_HELP,
  DURATION_HELP,
  HISTORY_YEARS,
  escapeHtml,
  fetchJson,
  formatDay,
  formatDayMonth,
  formatNumber,
  type FireCollection,
  type FireFeature,
  type HistoryResponse,
} from "./shared.js";

const ZONE_QUERY_LIMIT = 5000;

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

function renderSummary(data: HistoryResponse): void {
  summaryElement.innerHTML = summaryTableHtml(data.periods);
  summaryElement.hidden = false;
}

async function load(): Promise<void> {
  if (!bbox) {
    statusElement.textContent = "Falta la zona. Abre esta página desde el mapa (Seleccionar zona).";
    return;
  }

  try {
    if (mode === "history") {
      titleElement.textContent = "Incendios de años anteriores";
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
      renderCards(gridElement, data.events.features, true);
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
      renderCards(gridElement, largest, false);
    }
  } catch (error) {
    statusElement.textContent = `No se pudieron cargar los incendios: ${(error as Error).message}`;
  }
}

void load();
