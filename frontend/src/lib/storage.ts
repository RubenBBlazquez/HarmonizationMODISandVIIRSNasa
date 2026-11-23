// localStorage sin sustos: en modo privado o con el almacenamiento bloqueado puede lanzar, y
// entonces simplemente no se recuerda nada.

export function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Guarda `value` en `key` (null lo borra). */
export function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Sin almacenamiento: no se recuerda.
  }
}
