// Panel flotante de "Actividad reciente" sobre el mapa: lista los últimos incendios (/api/activity)
// y se puede ocultar; oculto, solo queda una línea en el borde para volver a mostrarlo. No depende
// de la librería del mapa: recibe cómo enfocar un punto y cómo avisar de que cambia su tamaño.

import { escapeHtml, fetchJson, formatDay, formatNumber, sensorsLabel } from "./shared.js";

const ACTIVITY_LIMIT = 15;
const STORAGE_KEY = "activity-panel-collapsed";
const FOCUS_ZOOM = 12;

interface ActivityItem {
  event_id: string;
  status: string;
  sensors: string;
  n_detections: number;
  frp_max_mw: number | null;
  first_detection: string | null;
  last_detection: string | null;
  lat: number;
  lon: number;
}

interface ActivityResponse {
  count: number;
  items: ActivityItem[];
}

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // Sin almacenamiento: el panel vuelve a abrirse al recargar.
  }
}

function itemHtml(item: ActivityItem, index: number): string {
  const active = item.status === "active";
  const detections = `${formatNumber(item.n_detections)} det.`;
  return `<li><button type="button" class="activity-item" data-index="${index}">
    <div class="activity-item-top"><span>${formatDay(item.last_detection)}</span>
      <span class="activity-badge ${escapeHtml(item.status)}">${active ? "activo" : "cerrado"}</span></div>
    <div class="activity-item-meta">${sensorsLabel(item.sensors)} · ${detections} ·
      FRP máx. ${escapeHtml(item.frp_max_mw)} MW</div></button></li>`;
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
    saveCollapsed(collapsed);
    window.setTimeout(onResize, 300);
  }
  hideButton.addEventListener("click", () => setCollapsed(true));
  showButton.addEventListener("click", () => setCollapsed(false));
  setCollapsed(readCollapsed());

  // Evita que los clics y la rueda sobre el panel lleguen al mapa.
  for (const element of [panel, showButton]) {
    element.addEventListener("click", (event) => event.stopPropagation());
    element.addEventListener("wheel", (event) => event.stopPropagation());
    element.addEventListener("mousedown", (event) => event.stopPropagation());
    element.addEventListener("dblclick", (event) => event.stopPropagation());
  }

  async function load(): Promise<void> {
    try {
      const data = await fetchJson<ActivityResponse>("/activity", {
        limit: String(ACTIVITY_LIMIT),
      });
      if (data.items.length === 0) {
        list!.innerHTML = `<li class="activity-empty">Aún no hay incendios registrados.</li>`;
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
      list!.innerHTML = `<li class="activity-empty">No se pudo cargar la actividad. ¿Está la API en marcha?</li>`;
    }
  }
  void load();
}
