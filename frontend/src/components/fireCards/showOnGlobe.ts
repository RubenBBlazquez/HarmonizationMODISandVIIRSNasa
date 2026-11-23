// Botón "View on the globe" de las tarjetas y ventanas. En el globo (globe/features/focus.ts) se registra
// cómo enfocar un incendio; en la pestaña de incendios no hay globo, así que el botón abre la
// página principal ya enfocada en él (?fire=).
import { globeFireUrl } from "../../domain/urls.js";
import { escapeHtml } from "../../lib/html.js";
import { ICONS } from "../icons.js";

let showOnGlobe: ((eventId: string) => void) | null = null;

export function setShowOnGlobe(handler: (eventId: string) => void): void {
  showOnGlobe = handler;
}

export function globeButtonHtml(eventId: string | number | null): string {
  return `<button type="button" class="globe-button" data-event-id="${escapeHtml(eventId)}"
    title="View this fire on the globe">${ICONS.globe}View on the globe</button>`;
}

/** Activa los botones de `globeButtonHtml` que haya dentro de `root`. */
export function bindGlobeButtons(root: HTMLElement): void {
  root.querySelectorAll<HTMLButtonElement>(".globe-button").forEach((button) =>
    button.addEventListener("click", () => {
      const eventId = button.dataset.eventId ?? "";
      if (showOnGlobe) showOnGlobe(eventId);
      else window.location.href = globeFireUrl(eventId);
    }),
  );
}
