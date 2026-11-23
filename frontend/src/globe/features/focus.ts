// Un incendio en el globo ("View on the globe" del histórico o ?fire=ID desde la pestaña de
// incendios): las capas muestran solo ese incendio, con una barra arriba para volver o quitarlo.
// El rango de fechas cargado se queda en memoria y se vuelve a pintar al salir.
import * as maplibregl from "maplibre-gl";

import { getEventDetail } from "../../api/endpoints.js";
import type { EventDetail } from "../../api/types.js";
import { setShowOnGlobe } from "../../components/fireCards/showOnGlobe.js";
import { suspendModals } from "../../components/modal.js";
import { sortByConfidence } from "../../domain/detections.js";
import { dateRangeLabel } from "../../lib/dates.js";
import { formatNumber } from "../../lib/format.js";
import { ui } from "../dom.js";
import { EMPTY, pixelCenter, positionsOf, withHelpers } from "../map/geojson.js";
import { applyYearOutlines, setData } from "../map/layers.js";
import { currentView, map } from "../map/map.js";
import { hideTip } from "../map/tooltip.js";
import { applyFilters, pushData } from "../render.js";
import { state } from "../state.js";
import { clearBusyWhenDrawn, setBusy } from "../ui/busy.js";
import { viewPadding } from "../ui/layout.js";
import { FOCUS_STATUS, setStatus, updateStatusUnlessPlaying } from "../ui/status.js";
import { clearPlayback, resetPlayButton, stopPlayback } from "./playback.js";
import { clearSelection } from "./zoneSelection/selection.js";

const FOCUS_DETECTIONS = 5000;
const FOCUS_MAX_ZOOM = 14;
// Qué hacer al volver atrás en el navegador (o con "Back" / "Remove", que también van atrás).
let pendingExit: "back" | "remove" = "back";

/** Pasadas del satélite (horas de adquisición distintas) y detecciones acumuladas hasta cada una. */
function passTimeline(features: GeoJSON.Feature[]): { time: string; total: number }[] {
  const perPass = new Map<string, number>();
  for (const feature of features) {
    const time = String(feature.properties?.acq_datetime ?? "");
    if (time) perPass.set(time, (perPass.get(time) ?? 0) + 1);
  }
  let accumulated = 0;
  return [...perPass.keys()].sort().map((time) => {
    accumulated += perPass.get(time) ?? 0;
    return { time, total: accumulated };
  });
}

export async function focusFire(eventId: string, fromUrl = false): Promise<void> {
  const modals = fromUrl ? null : suspendModals();
  const token = setBusy(true, "Loading the fire…");
  let detail: EventDetail;
  try {
    detail = await getEventDetail(eventId, FOCUS_DETECTIONS);
  } catch (error) {
    setBusy(false, `Could not load the fire: ${(error as Error).message}`);
    modals?.resume();
    return;
  }

  stopPlayback();
  if (state.focus) {
    state.focus.modals?.discard(); // ya había otro enfocado: se sustituye
    state.focus.modals = modals;
  } else {
    state.focus = { previousView: currentView(), modals, fromUrl, timeline: [] };
    if (!fromUrl) history.pushState({ siachiFocus: eventId }, "");
  }
  hideTip();
  clearSelection();

  // Píxeles (zoom alto) y puntos (zoom bajo) del incendio, con el mismo estilo que el rango.
  const sorted = sortByConfidence(detail.detections).features;
  state.focus.timeline = passTimeline(sorted);
  resetPlayButton();
  const pixels: GeoJSON.Feature[] = [];
  const points: GeoJSON.Feature[] = [];
  sorted.forEach((feature, order) => {
    const properties = withHelpers(feature.properties, "acq_datetime", order);
    pixels.push({ type: "Feature", geometry: feature.geometry, properties });
    const center = pixelCenter(feature.geometry);
    if (center) points.push({ type: "Feature", geometry: center, properties });
  });
  setData("pixels", { type: "FeatureCollection", features: pixels });
  setData("detections", { type: "FeatureCollection", features: points });
  setData("events", EMPTY);
  setData("focus", { type: "FeatureCollection", features: [detail.event] });
  applyYearOutlines();
  applyFilters();

  // Barra superior con el incendio y cómo salir.
  const p = detail.event.properties;
  ui.focusTitle.textContent = `Fire of ${dateRangeLabel(p.first_detection, p.last_detection)}`;
  ui.focusSub.textContent =
    `${formatNumber(Number(p.n_detections ?? 0))} detections · max FRP ${p.frp_max_mw ?? "—"} MW` +
    (detail.detections.truncated ? ` · showing ${formatNumber(detail.detections.count)}` : "");
  ui.focusBack.innerHTML = `<span aria-hidden="true">←</span> ${fromUrl ? "Back" : "Back to the history"}`;
  ui.focusBar.hidden = false;
  ui.alertsControl.hidden = true;
  setStatus(FOCUS_STATUS);

  const bounds = new maplibregl.LngLatBounds();
  for (const position of positionsOf(detail.event.geometry)) bounds.extend(position);
  for (const feature of pixels) for (const position of positionsOf(feature.geometry)) bounds.extend(position);
  if (!bounds.isEmpty()) {
    map.fitBounds(bounds, { padding: viewPadding(), maxZoom: FOCUS_MAX_ZOOM, duration: 2200 });
  }
  clearBusyWhenDrawn(token);
}

/**
 * Sale del incendio enfocado y vuelve a pintar el rango de fechas que ya estaba en memoria (sin
 * descargarlo otra vez). `back` reabre el histórico tal como estaba; `remove` lo cierra.
 */
export function exitFocus(mode: "back" | "remove", restoreView = true): void {
  if (!state.focus) return;
  const previous = state.focus;
  clearPlayback();
  state.focus = null;
  resetPlayButton();
  setData("focus", EMPTY);
  ui.focusBar.hidden = true;
  ui.alertsControl.hidden = false;
  pushData();
  updateStatusUnlessPlaying();
  if (restoreView) map.easeTo({ ...previous.previousView, duration: 1400 });
  if (mode === "back") previous.modals?.resume();
  else previous.modals?.discard();
}

export function initFocus(): void {
  // "Back" y "Remove" van atrás en el historial del navegador (se entró con pushState), así el
  // botón Atrás del navegador hace lo mismo que "Back".
  ui.focusBack.addEventListener("click", () => {
    if (!state.focus) return;
    if (!state.focus.fromUrl) pendingExit = "back";
    history.back(); // desde la URL, regresa a la pestaña de incendios
  });
  ui.focusClose.addEventListener("click", () => {
    if (!state.focus) return;
    if (state.focus.fromUrl) {
      exitFocus("remove");
      history.replaceState(null, "", `${location.pathname}#${state.selectedCode ?? ""}`);
      return;
    }
    pendingExit = "remove";
    history.back();
  });
  window.addEventListener("popstate", () => {
    if (state.focus && !state.focus.fromUrl) exitFocus(pendingExit);
    pendingExit = "back";
  });
  setShowOnGlobe((eventId) => void focusFire(eventId));
}
