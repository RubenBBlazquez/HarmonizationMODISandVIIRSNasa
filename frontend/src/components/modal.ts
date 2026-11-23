// Ventanas emergentes grandes (histórico de una zona, incendio ampliado). Esc o un clic fuera
// cierran la de encima. Estilos en styles/fireCards.css.
import { escapeHtml } from "../lib/html.js";

export interface Modal {
  body: HTMLElement;
  close: () => void;
  /** Fondo de la ventana (su elemento raíz en la página). */
  element: HTMLElement;
}

/** Ventanas apartadas con `suspendModals`. */
export interface SuspendedModals {
  /** Las vuelve a mostrar tal como estaban (scroll, filtros). */
  resume: () => void;
  /** Las cierra. */
  discard: () => void;
}

const openModals: Modal[] = [];

/** Aparta las ventanas abiertas sin cerrarlas (para ver algo en el globo). */
export function suspendModals(): SuspendedModals | null {
  if (openModals.length === 0) return null;
  const suspended = openModals.splice(0);
  for (const modal of suspended) modal.element.hidden = true;
  return {
    resume: () => {
      for (const modal of suspended) {
        modal.element.hidden = false;
        openModals.push(modal);
      }
    },
    discard: () => suspended.forEach((modal) => modal.element.remove()),
  };
}

/** Abre una ventana grande sobre la página; el contenido se pone en `modal.body`. */
export function openModal(title: string, className = "", subtitle = ""): Modal {
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `
    <section class="modal ${className}" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
      <header class="modal-head">
        <div class="modal-heading">
          ${subtitle ? `<span class="modal-eyebrow">${escapeHtml(subtitle)}</span>` : ""}
          <h2>${escapeHtml(title)}</h2>
        </div>
        <button type="button" class="modal-close" aria-label="Close">✕</button>
      </header>
      <div class="modal-body"></div>
    </section>`;
  const modal: Modal = {
    element: backdrop,
    body: backdrop.querySelector<HTMLElement>(".modal-body")!,
    close: () => {
      backdrop.remove();
      const index = openModals.indexOf(modal);
      if (index >= 0) openModals.splice(index, 1);
    },
  };
  backdrop.addEventListener("mousedown", (event) => {
    if (event.target === backdrop) modal.close();
  });
  backdrop.querySelector(".modal-close")!.addEventListener("click", modal.close);
  document.body.append(backdrop);
  openModals.push(modal);
  return modal;
}

// En captura: con una ventana abierta, Esc solo la cierra (no llega a los demás atajos del mapa).
document.addEventListener(
  "keydown",
  (event) => {
    if (event.key !== "Escape" || openModals.length === 0) return;
    event.stopPropagation();
    openModals[openModals.length - 1].close();
  },
  true,
);
