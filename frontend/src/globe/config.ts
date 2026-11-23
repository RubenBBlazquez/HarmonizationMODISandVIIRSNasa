// Constantes del globo que usan varios módulos (las de una sola función viven en su módulo).

/** Zoom con el que se encuadra un país (y máximo del zoom mínimo calculado para el globo). */
export const COUNTRY_MAX_ZOOM = 6;
/** Desde este zoom las detecciones se dibujan como su píxel (área ancho × alto); por debajo, puntos. */
export const PIXEL_MIN_ZOOM = 10;
/** Vista mientras cargan las fronteras: ya sobre España, para no ver antes el mundo entero. */
export const INITIAL_VIEW = { center: [-3.7, 40.2] as [number, number], zoom: 5 };
