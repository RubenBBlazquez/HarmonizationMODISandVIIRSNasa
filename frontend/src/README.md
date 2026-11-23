# Frontend: arquitectura

TypeScript sin framework, empaquetado con Vite. Dos páginas:

| Página       | Entrada            | Qué es                                                        |
|--------------|--------------------|---------------------------------------------------------------|
| `index.html` | `pages/globe.ts`   | Globo (MapLibre) con los incendios del país elegido           |
| `fires.html` | `pages/fires.ts`   | Tarjetas de los incendios de una zona o su histórico (Leaflet) |

## Capas

Cada capa solo importa de las de abajo; no hay ciclos de importación.

```
pages/        Entradas: importan estilos en orden, arrancan módulos y los conectan.
globe/        Todo lo propio del globo (ver abajo).
components/   Piezas de UI que usan las dos páginas: modal, selector de fechas, tarjetas de
              incendio (fireCards/), histórico de una zona (zoneHistory/), iconos.
domain/       Conocimiento de incendios sin DOM: colores, severidad, tipo de suelo, etiquetas
              de detecciones y medidas, textos de ayuda, HTML de popups, enlaces entre páginas.
api/          Contrato con la API: tipos de las respuestas (types.ts) y una función por
              endpoint (endpoints.ts). Ninguna otra parte construye URLs de la API.
lib/          Utilidades genéricas: fechas, números, escape de HTML, DOM, localStorage, móvil.
styles/       CSS globales (el del selector de fechas vive junto a su componente).
```

## El globo (`globe/`)

```
config.ts     Constantes compartidas (zooms, vista inicial).
dom.ts        `ui`: todos los elementos fijos de index.html en un solo sitio.
state.ts      `state`: estado compartido (país, datos cargados, claves ocultas, reproducción,
              incendio enfocado, modo selección) y consultas derivadas.
render.ts     `pushData` (datos → mapa) y `applyFilters` (lo visible → filtros + marcadores).
map/          El mapa MapLibre: instancia y zoom mínimo (map.ts), estilo base, fuentes y capas
              (layers.ts), filtros, conversión a GeoJSON (geojson.ts) y tooltip.
ui/           Indicador de carga (busy), línea de estado (status), disposición de paneles
              escritorio/móvil (layout).
features/     Una funcionalidad por fichero:
  dateRange         selector de fechas del panel, recordado entre visitas
  dataLoader        descarga del rango del país (con caché y cancelación)
  countries         elegir y encuadrar país
  legend            leyenda interactiva (ocultar/mostrar)
  yearsComparison   "Compare with all years" y chips por año
  playback          avance día a día / pasada a pasada
  alerts            lista de incendios activos
  activeMarkers     marcadores con pulso de los activos
  pointer           ratón: coordenadas, tooltip y clics
  zoneSelection/    recuadro + menú (selection) y gestos ratón/Ctrl/táctil (gestures)
  focus             un incendio enfocado ("View on the globe")
  activity          panel de actividad reciente
```

### Flujo

1. Un cambio de país, fechas o capas llama a `dataLoader.scheduleRefresh()`.
2. La descarga guarda el resultado en `state` y llama a `render.pushData()`.
3. Leyenda, años y reproducción solo cambian `state` y llaman a `render.applyFilters()`: el mapa
   no recibe los datos otra vez.

Las pocas dependencias "hacia arriba" (p. ej. la reproducción necesita recargar datos) se pasan
como funciones al iniciar el módulo, y `pages/globe.ts` hace esas conexiones.

## Estilos

El orden importa (con la misma especificidad gana el último): lo fija cada `pages/*.ts` al
principio. Los CSS solo se han movido, sin dividir por componente: hay reglas que se
sobrescriben entre secciones y conviene dividirlos con revisión visual.
