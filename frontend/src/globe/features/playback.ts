// Reproducción. Con el rango de fechas: un día por segundo. Con un incendio enfocado ("View on the
// globe"): pasada a pasada del satélite, porque la mayoría duran horas o pocos días (un día por
// paso apenas daría uno o dos fotogramas). En ambos casos se filtran los datos ya cargados, sin
// pedir más.
import type { FireCollection } from "../../api/types.js";
import { ICONS } from "../../components/icons.js";
import { addDays, formatDay } from "../../lib/dates.js";
import { formatNumber } from "../../lib/format.js";
import { ui } from "../dom.js";
import { applyFilters } from "../render.js";
import { isVisibleByDate, state, type FocusState } from "../state.js";
import { setStatus, updateStatus } from "../ui/status.js";
import { rangeEnd, rangeStart } from "./dateRange.js";

const PLAY_STEP_MS = 1000;
const FOCUS_STEP_MS = 900;
const PASS_TIME = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

let playTimer: number | undefined;
/** Carga el rango entero antes de reproducirlo (lo da dataLoader al iniciar). */
let reloadRange: () => Promise<void> = async () => {};

const isPlaying = (): boolean => ui.playButton.classList.contains("playing");

export function resetPlayButton(label?: string): void {
  ui.playButton.classList.remove("playing");
  const text = label ?? (state.focus ? "Watch the fire progress" : "Watch day-by-day progress");
  ui.playButton.innerHTML = `${ICONS.play}<span>${text}</span>`;
  ui.playButton.title = state.focus
    ? "Shows how the fire grew, satellite pass by satellite pass"
    : "Shows day by day how the fires in the chosen range progress";
}

function showStopButton(): void {
  ui.playButton.classList.add("playing");
  ui.playButton.innerHTML = `${ICONS.stop}<span>Detener</span>`;
}

/** Corta el paso pendiente y olvida hasta dónde se estaba viendo (sin volver a pintar). */
export function clearPlayback(): void {
  window.clearTimeout(playTimer);
  playTimer = undefined;
  state.playDay = null;
  state.playTime = null;
}

/** Detiene la reproducción y vuelve a mostrarlo todo. */
export function stopPlayback(): void {
  window.clearTimeout(playTimer);
  playTimer = undefined;
  if (state.playDay === null && state.playTime === null) return;
  state.playDay = null;
  state.playTime = null;
  resetPlayButton();
  applyFilters();
  updateStatus();
}

const countUpTo = (collection: FireCollection | null, field: string): number =>
  collection?.features.filter((feature) => isVisibleByDate(feature, field)).length ?? 0;

function startFocusPlayback(timeline: FocusState["timeline"]): void {
  if (timeline.length === 0) return;
  showStopButton();
  let index = 0;
  const step = (): void => {
    if (!state.focus || !isPlaying()) return;
    const { time, total } = timeline[index];
    state.playTime = time;
    applyFilters();
    const when = PASS_TIME.format(new Date(time)).replace(/\./g, "");
    setStatus(
      `Fire progress: pass ${index + 1} of ${timeline.length} · ${when} UTC · ` +
        `${formatNumber(total)} ${total === 1 ? "detection" : "detections"}`,
    );
    if (index >= timeline.length - 1) {
      playTimer = undefined;
      resetPlayButton("Replay the fire progress");
      return; // se queda mostrando el incendio completo; pulsar de nuevo vuelve a empezar
    }
    index += 1;
    playTimer = window.setTimeout(step, FOCUS_STEP_MS);
  };
  step();
}

async function startRangePlayback(): Promise<void> {
  if (!rangeStart() || !rangeEnd()) {
    setStatus("Choose a date range to see the progress");
    return;
  }
  const first = rangeStart();
  const last = rangeEnd();
  showStopButton();
  state.playDay = first;
  await reloadRange(); // carga de una vez todo el rango; los días se filtran sin pedir más datos
  if (state.playDay === null) return; // detenido mientras cargaba
  const step = (): void => {
    if (state.playDay === null) return;
    applyFilters();
    const shown =
      countUpTo(state.events, "first_detection") + countUpTo(state.detections, "acq_datetime");
    setStatus(`Progress: ${formatDay(state.playDay)} (until ${formatDay(last)}) · ${formatNumber(shown)} items`);
    if (state.playDay >= last) {
      playTimer = undefined;
      resetPlayButton("Replay progress");
      return; // se queda mostrando el rango completo; pulsar de nuevo vuelve a empezar
    }
    state.playDay = addDays(state.playDay, 1);
    playTimer = window.setTimeout(step, PLAY_STEP_MS);
  };
  step();
}

/** `reload` carga ya (sin esperar) todo el rango de fechas elegido. */
export function initPlayback(reload: () => Promise<void>): void {
  reloadRange = reload;
  resetPlayButton();
  ui.playButton.addEventListener("click", () => {
    if (isPlaying()) {
      stopPlayback();
      return;
    }
    clearPlayback();
    if (state.focus) startFocusPlayback(state.focus.timeline);
    else void startRangePlayback();
  });
}
