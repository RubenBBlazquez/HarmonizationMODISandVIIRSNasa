// Móvil = ancho de 650 px o menos (el mismo corte que los @media de los CSS). Se consulta en cada
// acción (no solo al cargar) para que cambiar el tamaño de la ventana o girar el dispositivo no
// deje activo el comportamiento equivocado.
export const MOBILE_MAX_WIDTH = 650;
export const mobileQuery = window.matchMedia(`(max-width: ${MOBILE_MAX_WIDTH}px)`);
export const isMobile = (): boolean => mobileQuery.matches;
