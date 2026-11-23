// Línea de estado bajo los controles del panel: qué se está viendo y cuántos elementos hay.
import type { FireCollection } from "../../api/types.js";
import { formatNumber } from "../../lib/format.js";
import { PIXEL_MIN_ZOOM } from "../config.js";
import { ui } from "../dom.js";
import { rangeEnd, rangeStart } from "../features/dateRange.js";
import { map } from "../map/map.js";
import { state } from "../state.js";

export const FOCUS_STATUS =
  "Viewing a single fire. Remove it to go back to the fires of the date range.";

export function setStatus(text: string): void {
  ui.status.textContent = text;
}

function describe(label: string, collection: FireCollection | null): string | null {
  if (!collection) return null;
  const shown = formatNumber(collection.count);
  return collection.truncated
    ? `${label}: ${shown} of ${formatNumber(collection.total)} (narrow the date range)`
    : `${label}: ${shown}`;
}

export function updateStatus(): void {
  if (state.focus) {
    setStatus(FOCUS_STATUS);
    return;
  }
  if (state.allYears) {
    const key = `${rangeStart()}|${rangeEnd()}|${state.selectedCode}`;
    if (state.yearlyKey !== key) {
      setStatus("Loading the per-year counts…");
      return;
    }
    const total = (field: "events" | "detections"): number =>
      state.yearly.reduce((sum, row) => sum + row[field], 0);
    setStatus(
      `${state.yearly.length} years · ${formatNumber(total("events"))} events · ` +
        `${formatNumber(total("detections"))} detections (per-year detail above)`,
    );
    return;
  }
  const parts = [describe("Events", state.events), describe("Detections", state.detections)];
  let text = parts.filter(Boolean).join(" · ") || "No active layers";
  if (state.hiddenKeys.size > 0) text += " · some items are hidden (legend)";
  if (state.detections && map.getZoom() < PIXEL_MIN_ZOOM) {
    text += ` · zoom the map in (zoom ${PIXEL_MIN_ZOOM}) to see the pixel`;
  }
  setStatus(text);
}

/** Actualiza el estado salvo durante la reproducción del rango (que pone el suyo). */
export function updateStatusUnlessPlaying(): void {
  if (state.playDay === null) updateStatus();
}

/** El aviso de "acerca el mapa" depende del zoom: se actualiza al cruzar PIXEL_MIN_ZOOM. */
export function watchPixelZoom(): void {
  let wasPixels = map.getZoom() >= PIXEL_MIN_ZOOM;
  map.on("zoomend", () => {
    const pixels = map.getZoom() >= PIXEL_MIN_ZOOM;
    if (pixels !== wasPixels) updateStatusUnlessPlaying();
    wasPixels = pixels;
  });
}
