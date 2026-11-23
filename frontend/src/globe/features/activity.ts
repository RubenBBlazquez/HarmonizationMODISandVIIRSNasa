// Panel flotante de "Recent activity" (derecha): los últimos incendios registrados (/activity). Se
// puede ocultar; oculto, solo queda una pestaña en el borde para volver a mostrarlo. No depende de
// la librería del mapa: recibe cómo enfocar un punto.
import { getActivity } from "../../api/endpoints.js";
import type { ActivityItem } from "../../api/types.js";
import { sensorsLabel } from "../../domain/detections.js";
import { formatDay } from "../../lib/dates.js";
import { isolateFromMap } from "../../lib/dom.js";
import { formatNumber } from "../../lib/format.js";
import { escapeHtml } from "../../lib/html.js";
import { isMobile } from "../../lib/responsive.js";
import { readStorage, writeStorage } from "../../lib/storage.js";

const ACTIVITY_LIMIT = 15;
const STORAGE_KEY = "activity-panel-collapsed";
const FOCUS_ZOOM = 12;

function readCollapsed(): boolean {
  const stored = readStorage(STORAGE_KEY);
  if (stored !== null) return stored === "1";
  return isMobile(); // en móvil empieza cerrado (ocupa media pantalla)
}

function itemHtml(item: ActivityItem, index: number): string {
  const active = item.status === "active";
  const detections = `${formatNumber(item.n_detections)} det.`;
  return `<li><button type="button" class="activity-item" data-index="${index}">
    <div class="activity-item-top"><span>${formatDay(item.last_detection)}</span>
      <span class="activity-badge ${escapeHtml(item.status)}">${active ? "active" : "closed"}</span></div>
    <div class="activity-item-meta">${sensorsLabel(item.sensors)} · ${detections} ·
      Max FRP ${escapeHtml(item.frp_max_mw)} MW</div></button></li>`;
}

/**
 * Activa el panel: `focus` centra el mapa en un incendio y `onResize` se llama cuando el panel se
 * abre o se cierra (los controles de la derecha se recolocan).
 */
export function initActivityPanel(
  focus: (lat: number, lon: number, zoom: number) => void,
  onResize: () => void = () => {},
): void {
  const panel = document.querySelector<HTMLElement>("#activity-panel");
  const hideButton = document.querySelector<HTMLButtonElement>("#activity-hide");
  const showButton = document.querySelector<HTMLButtonElement>("#activity-show");
  const list = document.querySelector<HTMLElement>("#activity-list");
  if (!panel || !hideButton || !showButton || !list) return;

  function setCollapsed(collapsed: boolean): void {
    panel!.classList.toggle("collapsed", collapsed);
    document.body.classList.toggle("activity-open", !collapsed);
    showButton!.hidden = !collapsed;
    writeStorage(STORAGE_KEY, collapsed ? "1" : "0");
    window.setTimeout(onResize, 300);
  }
  hideButton.addEventListener("click", () => setCollapsed(true));
  showButton.addEventListener("click", () => setCollapsed(false));
  setCollapsed(readCollapsed());
  isolateFromMap(panel, showButton);

  async function load(): Promise<void> {
    try {
      const data = await getActivity(ACTIVITY_LIMIT);
      if (data.items.length === 0) {
        list!.innerHTML = `<li class="activity-empty">No fires recorded yet.</li>`;
        return;
      }
      list!.innerHTML = data.items.map(itemHtml).join("");
      list!.querySelectorAll<HTMLButtonElement>("button[data-index]").forEach((button) =>
        button.addEventListener("click", () => {
          const item = data.items[Number(button.dataset.index)];
          focus(item.lat, item.lon, FOCUS_ZOOM);
        }),
      );
    } catch {
      list!.innerHTML = `<li class="activity-empty">Could not load the activity. Is the API running?</li>`;
    }
  }
  void load();
}
