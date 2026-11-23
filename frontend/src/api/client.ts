// Cliente HTTP de la API. El navegador llama a /api/... y Vite (en desarrollo) o Caddy (en
// producción) lo reenvía a la API, así que no hay problemas de CORS (ver vite.config.ts).
export const API_BASE = "/api";

/** GET `path` con `params` en la query. Si falla, lanza un Error con el `detail` de la API. */
export async function fetchJson<T>(
  path: string,
  params: Record<string, string>,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}?${new URLSearchParams(params)}`, { signal });
  if (!response.ok) {
    let message = `${path}: HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // Respuesta sin JSON: se queda el código HTTP.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}
