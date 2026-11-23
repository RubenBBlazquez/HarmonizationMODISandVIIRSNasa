// Gestos que dibujan la zona: arrastrar con el botón activo o con Ctrl pulsado (escritorio), y con
// el dedo tras una pulsación larga (móvil). El arrastre se sigue en `document`, no en el mapa: si el
// cursor pasa por un panel o se suelta fuera del mapa, la selección no queda a medias.
import type * as maplibregl from "maplibre-gl";

import { isMobile } from "../../../lib/responsive.js";
import { map } from "../../map/map.js";
import { state } from "../../state.js";
import { setPanelOpen } from "../../ui/layout.js";
import {
  boundsOf,
  clearSelection,
  closeMenu,
  drag,
  drawSelection,
  openSelectionMenu,
  setSelecting,
} from "./selection.js";

// Pulsación larga (móvil): mantener el dedo 0,5 s sobre el mapa equivale a Ctrl en escritorio. Un
// círculo de progreso marca la espera; al cumplirse (vibración) el mapa deja de moverse y se sigue
// arrastrando para dibujar el recuadro. El pequeño temblor del dedo durante la espera no la
// cancela (solo un desplazamiento claro), y el recuadro empieza donde está el dedo en ese momento.
const LONG_PRESS_MS = 500;
const LONG_PRESS_MOVE_PX = 24; // más que esto es un desplazamiento normal del mapa

let ctrlSelecting = false;
// Al soltar sobre una detección o un país, el navegador lanza además un `click` que abriría su
// popup o cambiaría de país. Ese clic se descarta.
let swallowClick = false;
let pressTimer: number | undefined;
let pressOrigin: { x: number; y: number } | null = null;
let pressLast: { x: number; y: number } | null = null;
let pressRing: HTMLElement | null = null;

/** Posición en el mapa de un evento del ratón o del dedo de `document` (también fuera del lienzo). */
function lngLatOf(event: { clientX: number; clientY: number }): maplibregl.LngLat {
  const rect = map.getCanvasContainer().getBoundingClientRect();
  return map.unproject([event.clientX - rect.left, event.clientY - rect.top]);
}

function startDrag(at: maplibregl.LngLat): void {
  closeMenu(); // el menú de una selección anterior no debe quedar debajo del arrastre
  drag.anchor = at;
  drawSelection(boundsOf(at, at));
}

/** Termina el arrastre en `end`: abre el menú de la zona o descarta un toque sin arrastre. */
function finishSelection(end: maplibregl.LngLat): void {
  if (!state.selecting || !drag.anchor) return;
  swallowClick = true;
  window.setTimeout(() => (swallowClick = false), 100);
  const bounds = boundsOf(drag.anchor, end);
  setSelecting(false);
  if (bounds.west === bounds.east || bounds.south === bounds.north) {
    clearSelection(); // un clic sin arrastrar no es una zona
    return;
  }
  drawSelection(bounds);
  openSelectionMenu(bounds);
}

function cancelLongPress(): void {
  window.clearTimeout(pressTimer);
  pressTimer = undefined;
  pressOrigin = null;
  pressLast = null;
  pressRing?.remove();
  pressRing = null;
}

function showPressRing(clientX: number, clientY: number): void {
  pressRing = document.createElement("div");
  pressRing.className = "hold-ring";
  pressRing.style.left = `${clientX}px`;
  pressRing.style.top = `${clientY}px`;
  pressRing.style.setProperty("--hold-ms", `${LONG_PRESS_MS}ms`);
  document.body.append(pressRing);
}

/** Teclado: Esc cancela y quita la zona; con Ctrl pulsado el arrastre selecciona. */
function initKeyboard(): void {
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (state.selecting) setSelecting(false);
      clearSelection();
    }
    if (event.key === "Control" && !state.selecting) {
      ctrlSelecting = true;
      setSelecting(true);
    }
  });
  document.addEventListener("keyup", (event) => {
    if (event.key === "Control" && ctrlSelecting && !drag.anchor) setSelecting(false);
    if (event.key === "Control") ctrlSelecting = false;
  });
  window.addEventListener("blur", () => {
    if (ctrlSelecting && !drag.anchor) setSelecting(false);
    ctrlSelecting = false;
  });
}

function initMouse(): void {
  map.on("mousedown", (event) => {
    if (!state.selecting) return;
    event.preventDefault();
    startDrag(event.lngLat);
  });
  document.addEventListener("mousemove", (event) => {
    if (!state.selecting || !drag.anchor) return;
    drawSelection(boundsOf(drag.anchor, lngLatOf(event)));
  });
  map.getContainer().addEventListener(
    "click",
    (event) => {
      if (swallowClick) event.stopPropagation(); // en captura: MapLibre no llega a verlo
    },
    true,
  );
  document.addEventListener("mouseup", (event) => finishSelection(lngLatOf(event)));
}

function initTouch(): void {
  map.on("touchstart", (event) => {
    if (state.selecting) {
      if (event.points.length !== 1) return;
      event.preventDefault();
      startDrag(event.lngLat);
      return;
    }
    cancelLongPress();
    if (!isMobile() || event.points.length !== 1) return;
    pressOrigin = { x: event.point.x, y: event.point.y };
    pressLast = pressOrigin;
    const touch = (event.originalEvent as TouchEvent).touches[0];
    showPressRing(touch.clientX, touch.clientY);
    pressTimer = window.setTimeout(() => {
      const finger = pressLast ?? pressOrigin;
      cancelLongPress();
      if (!finger) return;
      navigator.vibrate?.(30); // aviso táctil de que ya se puede arrastrar
      closeMenu();
      setPanelOpen(false);
      setSelecting(true); // desactiva el arrastre del mapa: ya no se mueve
      const anchor = map.unproject([finger.x, finger.y]); // donde está el dedo ahora, no donde empezó
      drag.anchor = anchor;
      drawSelection(boundsOf(anchor, anchor));
    }, LONG_PRESS_MS);
  });
  map.on("touchmove", (event) => {
    if (!pressOrigin) return;
    pressLast = { x: event.point.x, y: event.point.y };
    const moved = Math.hypot(event.point.x - pressOrigin.x, event.point.y - pressOrigin.y);
    if (moved > LONG_PRESS_MOVE_PX || event.points.length !== 1) cancelLongPress();
  });
  for (const type of ["touchend", "touchcancel"] as const) map.on(type, cancelLongPress);
  // El menú contextual del navegador al mantener pulsado taparía el gesto.
  map.getContainer().addEventListener("contextmenu", (event) => {
    if (isMobile()) event.preventDefault();
  });
  // El arrastre del recuadro se atiende en captura sobre el contenedor del mapa y NO se deja llegar
  // a MapLibre: así el mapa no se mueve aunque el dedo ya estuviera apoyado (pulsación larga) cuando
  // empezó la selección. `touchmove` no es pasivo para poder cancelarlo.
  map.getContainer().addEventListener(
    "touchmove",
    (event) => {
      if (!state.selecting || !drag.anchor) return;
      event.preventDefault();
      event.stopPropagation();
      drawSelection(boundsOf(drag.anchor, lngLatOf(event.touches[0])));
    },
    { capture: true, passive: false },
  );
  document.addEventListener("touchend", (event) => {
    if (state.selecting && drag.anchor) finishSelection(lngLatOf(event.changedTouches[0]));
  });
  document.addEventListener("touchcancel", () => {
    if (state.selecting && drag.anchor) {
      clearSelection();
      setSelecting(false);
    }
  });
}

export function initGestures(): void {
  initKeyboard();
  initMouse();
  initTouch();
}
