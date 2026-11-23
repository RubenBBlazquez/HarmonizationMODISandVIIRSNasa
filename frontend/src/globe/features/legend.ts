// Leyenda interactiva (abajo a la derecha): cada elemento oculta o muestra lo suyo pulsándolo.
import { EVENT_COLORS, FRP_COLORS } from "../../domain/colors.js";
import { LAND_COVER_KEYS } from "../../domain/landCover.js";
import { PIXEL_MIN_ZOOM } from "../config.js";
import { ui } from "../dom.js";
import { applyFilters } from "../render.js";
import { state, toggleHidden } from "../state.js";
import { updateStatusUnlessPlaying } from "../ui/status.js";

const LEGEND_ITEMS = [
  { key: "frp:low", label: "Low FRP detection", color: FRP_COLORS.low },
  { key: "frp:medium", label: "Medium FRP detection", color: FRP_COLORS.medium },
  { key: "frp:high", label: "High FRP detection", color: FRP_COLORS.high },
  { key: "status:active", label: "Active event", color: EVENT_COLORS.active },
  { key: "status:closed", label: "Closed event", color: EVENT_COLORS.closed },
];

const itemHtml = (key: string, label: string, color: string): string =>
  `<button type="button" class="legend-item" data-key="${key}"
    aria-pressed="true" title="Click to hide or show">
    <i style="--c:${color}"></i>${label}</button>`;

function legendHtml(): string {
  return `<details class="legend-all" open><summary>Legend</summary>
  <button type="button" class="legend-close" data-legend-close aria-label="Hide legend">✕</button>${LEGEND_ITEMS.map(
    (item) => itemHtml(item.key, item.label, item.color),
  ).join("")}
  <details class="legend-group" open>
    <summary>Land type (parcel)</summary>
    ${LAND_COVER_KEYS.map((item) => itemHtml(`lc:${item.key}`, item.label, item.color)).join("")}
  </details>
  <button type="button" class="legend-reset" data-reset hidden>Show all</button>
  <div class="legend-note">Click an item to hide or show it.<br>
    Zoom ≥ ${PIXEL_MIN_ZOOM}: detection area (width × height, km)</div></details>`;
}

/** Refleja `state.hiddenKeys` en los botones de la leyenda. */
export function syncLegend(): void {
  ui.legend.querySelectorAll<HTMLButtonElement>("button.legend-item").forEach((button) => {
    button.setAttribute("aria-pressed", String(!state.hiddenKeys.has(button.dataset.key ?? "")));
  });
  ui.legend.querySelector<HTMLButtonElement>(".legend-reset")!.hidden = state.hiddenKeys.size === 0;
}

/** Pinta la leyenda. `onReset` se llama al pulsar "Show all" (vuelven todos, años incluidos). */
export function initLegend(onReset: () => void): void {
  ui.legend.innerHTML = legendHtml();
  syncLegend();
  ui.legend.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!button) return;
    if (button.dataset.reset !== undefined) {
      state.hiddenKeys.clear();
      onReset();
    } else if (button.dataset.key) {
      toggleHidden(button.dataset.key);
    }
    syncLegend();
    applyFilters();
    updateStatusUnlessPlaying();
  });
}
