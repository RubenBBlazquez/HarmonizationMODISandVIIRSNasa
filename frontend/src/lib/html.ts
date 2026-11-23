/** Escapa un valor para meterlo en HTML (null/undefined → "—"). */
export function escapeHtml(value: string | number | null | undefined): string {
  return String(value ?? "—").replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}
