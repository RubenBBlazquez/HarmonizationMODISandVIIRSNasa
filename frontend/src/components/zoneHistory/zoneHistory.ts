// Histórico de una zona (las mismas fechas en los años anteriores): cabecera, indicadores,
// gráfico por años (también filtro), ayuda plegable y las tarjetas de los incendios con su barra
// de herramientas (año, severidad y orden). Se usa en una ventana del globo y en fires.html.
import { getZoneHistory, type ZoneHistoryQuery } from "../../api/endpoints.js";
import type { FireFeature, HistoryResponse } from "../../api/types.js";
import { MAX_CARDS } from "../../domain/config.js";
import { BIOMASS_HELP, DETECTION_HELP, DURATION_HELP, FOOTPRINT_NOTE } from "../../domain/help.js";
import { SEVERITY_COLORS, SEVERITY_ORDER, severityLabel } from "../../domain/severity.js";
import { formatDayMonth } from "../../lib/dates.js";
import { formatNumber } from "../../lib/format.js";
import { escapeHtml } from "../../lib/html.js";
import { renderCards } from "../fireCards/fireCards.js";
import { ICONS } from "../icons.js";
import { chartHtml, kpisHtml } from "./historyCharts.js";

export interface HistoryContext extends ZoneHistoryQuery {
  /** Enlace para abrir el histórico en una pestaña (en la propia pestaña no se pone). */
  tabUrl?: string;
}

const SORT_LABELS: Record<string, string> = {
  severity: "Most severe",
  recent: "Most recent",
  detections: "Most detections",
};

/** Indicador de carga para mostrar mientras se piden los datos del histórico. */
export function historyLoadingHtml(): string {
  return `<div class="history-loading"><span class="spinner"></span>Looking for fires from previous years…</div>`;
}

/** "40.12° N · 3.70° W · 12 × 9 km": centro y tamaño aproximado del recuadro. */
function zoneLabel(bbox: string): string {
  const [west, south, east, north] = bbox.split(",").map(Number);
  const lat = (south + north) / 2;
  const lon = (west + east) / 2;
  const fmt = (value: number, positive: string, negative: string): string =>
    `${Math.abs(value).toFixed(2)}° ${value >= 0 ? positive : negative}`;
  // Tamaño aproximado del recuadro en km (1° de latitud ≈ 111 km).
  const height = (north - south) * 111;
  const width = (east - west) * 111 * Math.cos((lat * Math.PI) / 180);
  return `${fmt(lat, "N", "S")} · ${fmt(lon, "E", "W")} · ${width.toFixed(0)} × ${height.toFixed(0)} km`;
}

/** Pinta en `container` el histórico de una zona ya descargado (`data`). */
export function renderHistory(
  container: HTMLElement,
  data: HistoryResponse,
  context: HistoryContext,
): void {
  let features = data.events.features.slice(0, MAX_CARDS) as FireFeature[];
  const reference = data.periods.find((period) => period.is_reference)?.label ?? context.start.slice(0, 4);
  const previous = data.periods.filter((period) => !period.is_reference);
  const withFires = previous.filter((period) => period.events > 0);
  const totalFires = previous.reduce((sum, period) => sum + period.events, 0);
  const yearOptions = [
    `<option value="">All years · ${formatNumber(totalFires)} fires</option>`,
    ...withFires.map(
      (period) =>
        `<option value="${escapeHtml(period.label)}">${escapeHtml(period.label)} · ${formatNumber(period.events)} fires</option>`,
    ),
  ].join("");
  const sortOptions = Object.entries(SORT_LABELS)
    .map(([value, label]) => `<option value="${value}">${label}</option>`)
    .join("");
  container.innerHTML = `
    <section class="history-hero">
      <div class="history-chips">
        <span class="chip">${ICONS.calendar}${formatDayMonth(context.start)} → ${formatDayMonth(context.end)} of every year</span>
        <span class="chip">${ICONS.pin}${zoneLabel(context.bbox)}</span>
        <span class="chip">${ICONS.history}${context.years} years before ${escapeHtml(reference)}</span>
      </div>
      ${context.tabUrl ? `<a class="hero-action" href="${context.tabUrl}" target="_blank" rel="noopener">Open in a tab ${ICONS.external}</a>` : ""}
    </section>
    ${kpisHtml(data.periods)}
    ${chartHtml(data.periods)}
    <details class="history-help">
      <summary>How to read this data</summary>
      <p>${escapeHtml(DETECTION_HELP)}</p>
      <p>${escapeHtml(DURATION_HELP)}</p>
      <p>**${escapeHtml(BIOMASS_HELP)}</p>
      <p>${FOOTPRINT_NOTE}</p>
    </details>
    <section class="history-fires">
      <header class="history-toolbar">
        <h3>Fires from previous years</h3>
        <label class="tool"><span>Year</span><select class="year-select">${yearOptions}</select></label>
        <label class="tool"><span>Sort by</span><select class="sort-select">${sortOptions}</select></label>
        <div class="filter severity-filter" role="group" aria-label="Filter by severity"></div>
      </header>
      <p class="history-note" hidden></p>
      <div class="grid"></div>
      <p class="history-empty" hidden></p>
    </section>`;

  const grid = container.querySelector<HTMLElement>(".grid")!;
  const yearSelect = container.querySelector<HTMLSelectElement>(".year-select")!;
  const sortSelect = container.querySelector<HTMLSelectElement>(".sort-select")!;
  const severityFilter = container.querySelector<HTMLElement>(".severity-filter")!;
  const note = container.querySelector<HTMLElement>(".history-note")!;
  const empty = container.querySelector<HTMLElement>(".history-empty")!;
  if (totalFires === 0) {
    empty.hidden = false;
    empty.textContent = "There were no fires in this zone on the same dates in previous years.";
    yearSelect.disabled = sortSelect.disabled = true;
    return;
  }

  let currentPeriod = "";
  let currentSeverity = "";
  let currentSort = "severity"; // el orden con el que el servidor devuelve la primera carga

  /** Incendios por severidad del año elegido (o de todos), con los totales reales del servidor. */
  function severityTotals(): Map<string, number> {
    const totals = new Map<string, number>();
    for (const period of currentPeriod ? withFires.filter((p) => p.label === currentPeriod) : withFires) {
      for (const [level, count] of Object.entries(period.severity ?? {})) {
        totals.set(level, (totals.get(level) ?? 0) + count);
      }
    }
    return totals;
  }

  function renderSeverityChips(): void {
    const totals = severityTotals();
    const all = [...totals.values()].reduce((sum, count) => sum + count, 0);
    const chip = (value: string, label: string, count: number): string =>
      `<button type="button" class="filter-chip" data-severity="${value}" aria-pressed="${value === currentSeverity}">
        ${value ? `<i style="--c:${SEVERITY_COLORS[value]}"></i>` : ""}${escapeHtml(label)}<span>${formatNumber(count)}</span></button>`;
    severityFilter.innerHTML = [
      chip("", "Any severity", all),
      ...[...SEVERITY_ORDER, "none"]
        .filter((level) => totals.has(level))
        .map((level) => chip(level, severityLabel(level), totals.get(level) ?? 0)),
    ].join("");
  }

  function updateNote(): void {
    const matching = currentPeriod
      ? (withFires.find((period) => period.label === currentPeriod)?.events ?? 0)
      : totalFires;
    const visible = [...grid.querySelectorAll<HTMLElement>(".card")].filter((card) => !card.hidden).length;
    note.hidden = false;
    note.textContent =
      visible < matching && !currentSeverity
        ? `Showing ${formatNumber(visible)} of ${formatNumber(matching)} fires (${SORT_LABELS[currentSort].toLowerCase()} first).`
        : `${formatNumber(visible)} ${visible === 1 ? "fire" : "fires"} shown.`;
  }

  function applyFilters(): void {
    grid.querySelectorAll<HTMLElement>(".card").forEach((element) => {
      element.hidden =
        (Boolean(currentPeriod) && element.dataset.period !== currentPeriod) ||
        (Boolean(currentSeverity) && element.dataset.severity !== currentSeverity);
    });
    updateNote();
  }

  /** Año elegido: lo comparten el desplegable y las barras del gráfico. */
  function show(period: string): void {
    currentPeriod = period;
    yearSelect.value = period;
    container.querySelectorAll<HTMLButtonElement>(".bar").forEach((bar) => {
      bar.classList.toggle("selected", Boolean(period) && bar.dataset.period === period);
    });
    renderSeverityChips();
    applyFilters();
  }

  async function changeSort(sort: string): Promise<void> {
    currentSort = sort;
    sortSelect.disabled = true;
    grid.setAttribute("aria-busy", "true");
    try {
      const response = await getZoneHistory(context, sort);
      features = response.events.features.slice(0, MAX_CARDS) as FireFeature[];
      renderCards(grid, features, true);
      applyFilters();
    } catch (error) {
      note.hidden = false;
      note.textContent = `Could not sort the fires: ${(error as Error).message}`;
    } finally {
      sortSelect.disabled = false;
      grid.removeAttribute("aria-busy");
    }
  }

  renderCards(grid, features, true);
  renderSeverityChips();
  applyFilters();

  yearSelect.addEventListener("change", () => show(yearSelect.value));
  sortSelect.addEventListener("change", () => void changeSort(sortSelect.value));
  severityFilter.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>(".filter-chip");
    if (!button) return;
    currentSeverity = button.dataset.severity ?? "";
    renderSeverityChips();
    applyFilters();
  });
  container.querySelector(".bars")!.addEventListener("click", (event) => {
    const bar = (event.target as HTMLElement).closest<HTMLButtonElement>(".bar");
    if (!bar || bar.disabled) return;
    const period = bar.dataset.period ?? "";
    // Pulsar de nuevo el año elegido vuelve a mostrar todos; el año de referencia no tiene tarjetas.
    const selected = bar.classList.contains("selected");
    const known = withFires.some((p) => p.label === period);
    show(selected || !known ? "" : period);
    if (!selected && known) {
      container.querySelector(".history-fires")!.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}

/**
 * Descarga y pinta el histórico de una zona en `container`, con el indicador de carga mientras
 * llega y el mensaje de error si falla.
 */
export async function loadHistoryInto(container: HTMLElement, context: HistoryContext): Promise<void> {
  container.innerHTML = historyLoadingHtml();
  try {
    renderHistory(container, await getZoneHistory(context), context);
  } catch (error) {
    container.innerHTML = `<p class="history-empty">${escapeHtml((error as Error).message)}</p>`;
  }
}
