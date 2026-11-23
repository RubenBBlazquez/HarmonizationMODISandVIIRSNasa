// Ayudas para el DOM.

/** Elemento que tiene que existir en la página (los de index.html / fires.html). */
export function $<T extends HTMLElement = HTMLElement>(selector: string, root: ParentNode = document): T {
  return root.querySelector<T>(selector)!;
}

const MAP_EVENTS = ["mousedown", "click", "dblclick", "wheel"] as const;

/** Los clics y la rueda sobre estos elementos (paneles, botones flotantes) no llegan al mapa. */
export function isolateFromMap(...elements: HTMLElement[]): void {
  for (const element of elements) {
    for (const type of MAP_EVENTS) {
      element.addEventListener(type, (event) => event.stopPropagation());
    }
  }
}
