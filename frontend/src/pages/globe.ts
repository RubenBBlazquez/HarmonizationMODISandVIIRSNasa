// Página principal (index.html): el globo con los incendios del país elegido y todas sus
// herramientas. Este fichero solo compone: importa los estilos en orden, arranca cada módulo y
// conecta los que dependen unos de otros. Cada funcionalidad vive en globe/features/.
//
// Flujo de datos: se descarga una vez el rango de fechas del país (dataLoader) → state → el mapa
// lo pinta (render.pushData). Lo que oculta la leyenda, los años o la reproducción se aplica como
// filtro (render.applyFilters), sin volver a enviar los datos al mapa.

// Estilos, en este orden (con la misma especificidad gana el último): MapLibre, el globo y sus
// controles, el selector de fechas, Leaflet (mapas de las tarjetas) y tarjetas/histórico/ventanas.
import "maplibre-gl/dist/maplibre-gl.css";
import "../styles/globe.css";
import "../styles/mapControls.css";
import "../components/dateRangePicker/dateRangePicker.css";
import "leaflet/dist/leaflet.css";
import "../styles/fireCards.css";

import { getCountries, getSummary } from "../api/endpoints.js";
import { initActivityPanel } from "../globe/features/activity.js";
import { initCountries, initialCountry, selectCountry } from "../globe/features/countries.js";
import { initDataLoader, refreshNow, scheduleRefresh } from "../globe/features/dataLoader.js";
import { initDateRange, setDataBounds } from "../globe/features/dateRange.js";
import { focusFire, initFocus } from "../globe/features/focus.js";
import { initLegend } from "../globe/features/legend.js";
import { initPlayback, stopPlayback } from "../globe/features/playback.js";
import { initPointer } from "../globe/features/pointer.js";
import { initYearsComparison, updateYearsLegend } from "../globe/features/yearsComparison.js";
import { initGestures } from "../globe/features/zoneSelection/gestures.js";
import { initSelection } from "../globe/features/zoneSelection/selection.js";
import { ui } from "../globe/dom.js";
import { addLayers, setData } from "../globe/map/layers.js";
import { map, mapLoaded, updateMinZoom } from "../globe/map/map.js";
import { initPixelsOnDemand } from "../globe/render.js";
import { state } from "../globe/state.js";
import { hideSplash, initBusy, SPLASH_MAX_WAIT_MS, setBusy } from "../globe/ui/busy.js";
import { initLayout } from "../globe/ui/layout.js";
import { watchPixelZoom } from "../globe/ui/status.js";

// --- Módulos y sus conexiones ----------------------------------------------------------------
initBusy();
initDateRange(() => {
  stopPlayback();
  scheduleRefresh();
});
initLegend(updateYearsLegend); // "Show all" también reactiva los chips de años
initLayout(); // después de la leyenda: usa sus elementos
initYearsComparison(scheduleRefresh);
initDataLoader();
initPlayback(refreshNow);
initCountries();
initPointer();
initSelection();
initGestures();
initFocus();
initActivityPanel((lat, lon, zoom) => map.flyTo({ center: [lon, lat], zoom }));

// --- Arranque -----------------------------------------------------------------------------------
async function start(): Promise<void> {
  const [summary, borders] = await Promise.all([
    getSummary().catch(() => null),
    getCountries().catch(() => null),
  ]);
  if (summary?.first_detection && summary.last_detection) {
    // Rango inicial: el último año con datos (o el último elegido).
    setDataBounds(summary.first_detection.slice(0, 10), summary.last_detection.slice(0, 10));
  }
  if (!borders) {
    ui.countryName.textContent = "No connection";
    setBusy(false, "Could not load the countries. Is the API running?");
    return;
  }
  state.countries = borders;
  await mapLoaded;
  updateMinZoom();
  window.addEventListener("resize", () => window.requestAnimationFrame(updateMinZoom));
  addLayers();
  setData("countries", borders);
  watchPixelZoom();
  initPixelsOnDemand();

  // Un enlace con #PRT abre directamente ese país; sin él, el país por defecto. Sin animación:
  // la página se abre ya encuadrada.
  selectCountry(initialCountry(), false);
  // La pantalla de carga se retira en cuanto el mapa está pintado (sin esperar a los datos de
  // incendios, que siguen cargándose con la barra del panel), con un tope por si no llega a quedar
  // inactivo (por ejemplo, teselas lentas).
  map.once("idle", hideSplash);
  window.setTimeout(hideSplash, SPLASH_MAX_WAIT_MS);

  // ?fire=ID (desde la pestaña de incendios): se abre ya enfocado en ese incendio.
  const fireId = new URLSearchParams(location.search).get("fire");
  if (fireId) void focusFire(fireId, true);
}

void start();
