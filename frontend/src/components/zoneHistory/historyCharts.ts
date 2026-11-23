// Indicadores y gráfico de barras por año del histórico de una zona.
import type { HistoryPeriod } from "../../api/types.js";
import { SEVERITY_COLORS, SEVERITY_ORDER, severityLabel } from "../../domain/severity.js";
import { formatNumber } from "../../lib/format.js";
import { escapeHtml } from "../../lib/html.js";

/** Cuatro indicadores de los años anteriores: incendios, detecciones, año más activo y FRP máx. */
export function kpisHtml(periods: HistoryPeriod[]): string {
  const previous = periods.filter((period) => !period.is_reference);
  const fires = previous.reduce((sum, period) => sum + period.events, 0);
  const detections = previous.reduce((sum, period) => sum + period.detections, 0);
  const withFire = previous.filter((period) => period.events > 0);
  const busiest = [...withFire].sort((a, b) => b.detections - a.detections || b.events - a.events)[0];
  const frpValues = previous.map((period) => period.frp_max_mw).filter((v): v is number => v != null);
  const frpMax = frpValues.length ? Math.max(...frpValues) : null;
  const frpYear = previous.find((period) => period.frp_max_mw === frpMax)?.label;
  return `<div class="kpis">
    <div class="kpi"><span class="kpi-label">Fires</span><b>${formatNumber(fires)}</b>
      <small>in ${withFire.length} of ${previous.length} years</small></div>
    <div class="kpi"><span class="kpi-label">Detections in the zone</span><b>${formatNumber(detections)}</b>
      <small>fire pixels seen by satellite</small></div>
    <div class="kpi"><span class="kpi-label">Most active year</span>
      <b>${busiest ? escapeHtml(busiest.label) : "—"}</b>
      <small>${busiest ? `${formatNumber(busiest.events)} fires · ${formatNumber(busiest.detections)} detections` : "no fires in the zone"}</small></div>
    <div class="kpi fire"><span class="kpi-label">Max FRP</span>
      <b>${frpMax == null ? "—" : `${frpMax}<small> MW</small>`}</b>
      <small>${frpYear ? `in ${escapeHtml(frpYear)} · fire intensity` : "no data"}</small></div>
  </div>`;
}

/** Puntos de color de la severidad, para explicar los tramos de las barras. */
function severityKeyHtml(): string {
  return [...SEVERITY_ORDER]
    .map((level) => `<span class="sev-key"><i style="--c:${SEVERITY_COLORS[level]}"></i>${severityLabel(level)}</span>`)
    .join("");
}

/** Tramos de una barra: un color por nivel de severidad, proporcional al número de incendios. */
function stackHtml(period: HistoryPeriod): string {
  const total = Math.max(1, period.events);
  const levels = [...SEVERITY_ORDER, "none"].filter((level) => (period.severity?.[level] ?? 0) > 0);
  if (levels.length === 0) levels.push("none");
  return `<span class="bar-stack">${levels
    .map((level) => {
      const count = period.severity?.[level] ?? period.events;
      return `<span class="bar-seg" style="flex:${(count / total) * 100} 0 0;background:${SEVERITY_COLORS[level]}"
        title="${escapeHtml(`${severityLabel(level)}: ${count}`)}"></span>`;
    })
    .join("")}</span>`;
}

/** Texto del tooltip de una barra: "2016: 52 fires (3 severe, 20 moderate…), 534 detections". */
function barTitle(period: HistoryPeriod): string {
  const levels = [...SEVERITY_ORDER, "none"]
    .filter((level) => (period.severity?.[level] ?? 0) > 0)
    .map((level) => `${period.severity[level]} ${severityLabel(level).toLowerCase()}`);
  return (
    `${period.label}: ${formatNumber(period.events)} fires` +
    (levels.length ? ` (${levels.join(", ")})` : "") +
    `, ${formatNumber(period.detections)} detections` +
    (period.frp_max_mw == null ? "" : `, max FRP ${period.frp_max_mw} MW`)
  );
}

/**
 * Gráfico de barras por año (del más antiguo al más reciente): altura = detecciones en la zona y
 * colores = severidad de sus incendios. Cada barra (`.bar[data-period]`) sirve de filtro por año.
 */
export function chartHtml(periods: HistoryPeriod[]): string {
  const ordered = [...periods].reverse();
  const max = Math.max(1, ...ordered.map((period) => period.detections));
  const bars = ordered.map((period) => {
    const empty = period.events === 0;
    // Raíz cuadrada: un año con 2 detecciones sigue viéndose al lado de otro con 200.
    const height = empty ? 0 : Math.max(8, Math.round((Math.sqrt(period.detections) / Math.sqrt(max)) * 100));
    return `<button type="button" class="bar ${empty ? "empty" : ""} ${period.is_reference ? "reference" : ""}"
        data-period="${escapeHtml(period.label)}" title="${escapeHtml(barTitle(period))}" ${empty ? "disabled" : ""}>
      <span class="bar-track">
        ${empty ? "" : `<span class="bar-fill" style="height:${height}%">${stackHtml(period)}<span class="bar-value">${formatNumber(period.events)}</span></span>`}
      </span>
      <span class="bar-label">${escapeHtml(period.label)}${period.is_reference ? "<em>chosen</em>" : ""}</span>
    </button>`;
  });
  return `<section class="history-chart" aria-label="Fires per year">
    <header><h3>Fires per year</h3>
      <span>Number of fires above each bar · height by detections in the zone · click a year to filter</span></header>
    <div class="sev-keys" aria-label="Severity colours">${severityKeyHtml()}</div>
    <div class="bars">${bars.join("")}</div>
  </section>`;
}
