// Iconos SVG en línea (algunos tipos de letra no pintan bien los emojis equivalentes).

const svg = (size: number, body: string, attrs = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"'): string =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" ${attrs} aria-hidden="true">${body}</svg>`;

export const ICONS = {
  calendar: svg(14, '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>'),
  pin: svg(14, '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>'),
  history: svg(14, '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/>'),
  external: svg(13, '<path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>'),
  globe: svg(14, '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
  play: svg(14, '<path d="M7 4l13 8-13 8z"/>', 'fill="currentColor"'),
  stop: svg(14, '<rect x="6" y="6" width="12" height="12" rx="1"/>', 'fill="currentColor"'),
  select: svg(
    16,
    '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/>' +
      '<path d="M12 8v8M8 12h8"/>',
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"',
  ),
  /** Dedo pulsando (mantener pulsado el mapa). */
  hold: svg(
    14,
    '<path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V11"/>' +
      '<path d="M12 10.5a1.5 1.5 0 0 1 3 0V12a1.5 1.5 0 0 1 3 0v3.5a5.5 5.5 0 0 1-5.5 5.5h-1A5.5 5.5 0 0 1 7 16.5L5.6 13.4a1.4 1.4 0 0 1 2.3-1.5L9 13"/>',
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"',
  ),
  /** Calendario del selector de fechas (16 px de caja, dibujado a 13 px). */
  dateRange:
    '<svg class="date-range-icon" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">' +
    '<rect x="1.5" y="3" width="13" height="11.5" rx="2" fill="none" stroke="currentColor" ' +
    'stroke-width="1.4"/><path d="M1.5 6.5h13M5 1.5v3M11 1.5v3" stroke="currentColor" ' +
    'stroke-width="1.4" stroke-linecap="round"/></svg>',
};
