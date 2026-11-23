// Indicadores de carga: la pantalla de bienvenida (index.html) y la barra del panel con el spinner
// de la línea de estado. La barra se enciende al pedir datos y se apaga cuando el mapa termina de
// pintarlos (evento "idle"), no solo cuando llega la respuesta. Cada carga tiene su número: si
// llega otra más nueva, la antigua no apaga el indicador.
import { ui } from "../dom.js";
import { map } from "../map/map.js";

/** Espera máxima a que el mapa quede inactivo antes de retirar la pantalla de carga. */
export const SPLASH_MAX_WAIT_MS = 3000;
/** Red de seguridad si el arranque falla: la pantalla de carga nunca se queda puesta. */
const SPLASH_SAFETY_MS = 12_000;

let busyToken = 0;

/** Retira la pantalla de carga inicial (una sola vez). */
export function hideSplash(): void {
  if (document.body.classList.contains("app-ready")) return;
  document.body.classList.add("app-ready");
  const splash = document.querySelector<HTMLElement>("#splash");
  splash?.classList.add("done");
  window.setTimeout(() => splash?.remove(), 800);
}

export function initBusy(): void {
  window.setTimeout(hideSplash, SPLASH_SAFETY_MS);
}

/** Enciende o apaga el indicador (y opcionalmente cambia el estado). Devuelve el número de carga. */
export function setBusy(busy: boolean, text?: string): number {
  if (!busy) hideSplash();
  busyToken += 1;
  ui.panel.classList.toggle("busy", busy);
  ui.panel.setAttribute("aria-busy", String(busy));
  ui.status.classList.toggle("loading", busy);
  if (text) ui.status.textContent = text;
  return busyToken;
}

/** ¿Sigue siendo `token` la carga más reciente? */
export const isLatestLoad = (token: number): boolean => token === busyToken;

/** Apaga el indicador de la carga `token` cuando el mapa termine de pintar (si no hay otra después). */
export function clearBusyWhenDrawn(token: number): void {
  const clear = (): void => {
    if (isLatestLoad(token)) setBusy(false);
  };
  if (map.loaded()) map.once("idle", clear);
  else clear();
}
