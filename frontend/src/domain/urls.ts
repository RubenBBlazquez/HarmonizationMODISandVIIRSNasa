// Enlaces entre las dos páginas.

/** Pestaña con todos los incendios de una búsqueda (ver pages/fires.ts). */
export function firesPageUrl(params: Record<string, string>): string {
  return `/fires.html?${new URLSearchParams(params)}`;
}

/** Globo ya enfocado en un incendio (?fire=). */
export function globeFireUrl(eventId: string): string {
  return `/?fire=${encodeURIComponent(eventId)}`;
}
