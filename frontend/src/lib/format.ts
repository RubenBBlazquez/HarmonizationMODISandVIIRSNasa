// Formato de números y coordenadas para la interfaz.

export function formatNumber(value: number): string {
  return value.toLocaleString("en-US");
}

/** Un decimal: "11.5". */
export function formatDecimal(value: number): string {
  return value.toFixed(1);
}

/** "37.12° N, 4.55° W" para una posición [lng, lat]. */
export function coordinatesLabel(position: GeoJSON.Position): string {
  const [lng, lat] = position;
  return `${Math.abs(lat).toFixed(2)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lng).toFixed(2)}° ${lng >= 0 ? "E" : "W"}`;
}
