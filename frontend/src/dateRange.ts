// Selector de rango de fechas en un único elemento: un botón con el rango que abre dos calendarios
// uno al lado del otro, Inicio (izquierda) y Fin (derecha), cada uno con su propia navegación de
// mes y año. Cada elección se aplica al momento y `Esc` deshace la última (con la historia vacía,
// cierra el calendario). Cada calendario solo cambia su propia fecha: en Inicio se deshabilitan los
// días posteriores al fin, y en Fin los anteriores al inicio. Fechas "AAAA-MM-DD" (UTC).
import "./dateRange.css";

export interface DateRange {
  start: string;
  end: string;
}

type Panel = "start" | "end";

interface View {
  year: number;
  month: number; // 0-11
}

interface Preset {
  label: string;
  days: number | null; // null = todas las fechas
}

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
const WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"];
const PRESETS: Preset[] = [
  { label: "Last week", days: 7 },
  { label: "Last month", days: 30 },
  { label: "Last year", days: 365 },
  // Sin "All dates": el mapa carga el rango entero de una vez y todo el histórico son
  // cientos de miles de detecciones.
];
const PANEL_LABELS: Record<Panel, string> = { start: "Start", end: "End" };
const PANELS: Panel[] = ["start", "end"];
const FALLBACK_FIRST_YEAR = 2000;
// Icono propio (SVG): algunos tipos de letra no pintan bien el emoji del calendario.
const CALENDAR_ICON =
  '<svg class="date-range-icon" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">' +
  '<rect x="1.5" y="3" width="13" height="11.5" rx="2" fill="none" stroke="currentColor" ' +
  'stroke-width="1.4"/><path d="M1.5 6.5h13M5 1.5v3M11 1.5v3" stroke="currentColor" ' +
  'stroke-width="1.4" stroke-linecap="round"/></svg>';

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function isoFromParts(year: number, month: number, day: number): string {
  return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
}

/** Suma (o resta) días a una fecha "AAAA-MM-DD". */
export function addDays(iso: string, days: number): string {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function displayDay(iso: string): string {
  const [year, month, day] = iso.split("-");
  return `${day}-${month}-${year}`;
}

export class DateRangePicker {
  private readonly root: HTMLDivElement;
  private readonly toggle: HTMLButtonElement;
  private readonly popover: HTMLDivElement;
  private range: DateRange | null = null;
  private draft: DateRange = { start: todayIso(), end: todayIso() };
  private views: Record<Panel, View> = {
    start: { year: new Date().getUTCFullYear(), month: new Date().getUTCMonth() },
    end: { year: new Date().getUTCFullYear(), month: new Date().getUTCMonth() },
  };
  private history: (DateRange | null)[] = []; // rangos anteriores de esta apertura, para `Esc`
  private min: string | null = null;
  private max: string | null = null;

  constructor(
    container: HTMLElement,
    private readonly onChange: (range: DateRange | null) => void,
  ) {
    this.root = document.createElement("div");
    this.root.className = "date-range";
    this.toggle = document.createElement("button");
    this.toggle.type = "button";
    this.toggle.className = "date-range-toggle";
    this.toggle.setAttribute("aria-haspopup", "dialog");
    this.toggle.setAttribute("aria-expanded", "false");
    this.popover = document.createElement("div");
    this.popover.className = "date-range-popover";
    this.popover.setAttribute("role", "dialog");
    this.popover.setAttribute("aria-label", "Choose date range");
    this.popover.hidden = true;
    this.root.append(this.toggle, this.popover);
    container.append(this.root);

    this.toggle.addEventListener("click", () => (this.popover.hidden ? this.open() : this.close()));
    this.popover.addEventListener("click", (event) => this.handleClick(event));
    this.popover.addEventListener("change", (event) => this.handleSelect(event));
    // Se cierra al pulsar fuera o con Esc.
    document.addEventListener("mousedown", (event) => {
      if (!this.popover.hidden && !this.root.contains(event.target as Node)) this.close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || this.popover.hidden) return;
      // Primero deshace las elecciones hechas con el calendario abierto; después, lo cierra.
      if (!this.undoLast()) this.close();
    });
    this.updateLabel();
  }

  /** Rango elegido o null (todas las fechas). */
  get value(): DateRange | null {
    return this.range;
  }

  /** Limita el calendario a las fechas con datos. */
  setBounds(min: string | null, max: string | null): void {
    this.min = min;
    this.max = max;
  }

  /** Cambia el rango sin avisar a `onChange` (para fijar el valor inicial). */
  setRange(range: DateRange | null): void {
    this.range = range;
    if (range) this.draft = { ...range };
    this.updateLabel();
  }

  /** Aplica el rango y avisa; `close` cierra el calendario. */
  private apply(range: DateRange | null, close: boolean): void {
    this.setRange(range);
    if (close) this.close();
    this.onChange(range);
  }

  private open(): void {
    this.draft = this.range
      ? { ...this.range }
      : { start: this.min ?? todayIso(), end: this.max ?? todayIso() };
    this.history = [];
    this.goToMonthOf("start");
    this.goToMonthOf("end");
    this.popover.hidden = false;
    this.toggle.setAttribute("aria-expanded", "true");
    this.render();
  }

  private close(): void {
    this.popover.hidden = true;
    this.toggle.setAttribute("aria-expanded", "false");
  }

  /** Lleva el calendario de un panel al mes de su fecha. */
  private goToMonthOf(panel: Panel): void {
    const [year, month] = this.draft[panel].split("-").map(Number);
    this.views[panel] = { year, month: month - 1 };
  }

  private updateLabel(): void {
    const text = this.range
      ? `${displayDay(this.range.start)} → ${displayDay(this.range.end)}`
      : "All dates";
    this.toggle.innerHTML = `${CALENDAR_ICON}<span>${text}</span>`;
  }

  private yearOptions(): number[] {
    const first = this.min ? Number(this.min.slice(0, 4)) : FALLBACK_FIRST_YEAR;
    const last = Number((this.max ?? todayIso()).slice(0, 4));
    const years: number[] = [];
    for (let year = last; year >= first; year--) years.push(year);
    return years;
  }

  private isDisabled(iso: string, panel: Panel): boolean {
    const outOfBounds = (this.min !== null && iso < this.min) || (this.max !== null && iso > this.max);
    // El inicio no puede ser posterior al fin ni el fin anterior al inicio.
    const crossesOther = panel === "start" ? iso > this.draft.end : iso < this.draft.start;
    return outOfBounds || crossesOther;
  }

  private calendarHtml(panel: Panel): string {
    const { year, month } = this.views[panel];
    const { start, end } = this.draft;
    const offset = (new Date(Date.UTC(year, month, 1)).getUTCDay() + 6) % 7; // semana desde lunes
    const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    const cells = Array.from({ length: offset }, () => "<span></span>");
    for (let day = 1; day <= daysInMonth; day++) {
      const iso = isoFromParts(year, month, day);
      const classes = ["day"];
      if (iso >= start && iso <= end) classes.push("in-range");
      if (iso === start || iso === end) classes.push("edge");
      const disabled = this.isDisabled(iso, panel) ? "disabled" : "";
      cells.push(
        `<button type="button" class="${classes.join(" ")}" data-panel="${panel}" ` +
          `data-day="${iso}" aria-label="${displayDay(iso)}" ${disabled}>${day}</button>`,
      );
    }

    const months = MONTHS.map(
      (name, index) =>
        `<option value="${index}" ${index === month ? "selected" : ""}>${name}</option>`,
    );
    const years = this.yearOptions().map(
      (value) => `<option value="${value}" ${value === year ? "selected" : ""}>${value}</option>`,
    );
    const weekdays = WEEKDAYS.map((name) => `<span class="weekday">${name}</span>`);

    return `
      <section class="date-range-panel" aria-label="${PANEL_LABELS[panel]}">
        <div class="date-range-panel-title"><small>${PANEL_LABELS[panel]}</small>
          <b>${displayDay(this.draft[panel])}</b></div>
        <div class="date-range-head">
          <button type="button" class="nav" data-panel="${panel}" data-step="-1"
            aria-label="Previous month">‹</button>
          <select class="month" data-panel="${panel}" aria-label="Month">${months.join("")}</select>
          <select class="year" data-panel="${panel}" aria-label="Year">${years.join("")}</select>
          <button type="button" class="nav" data-panel="${panel}" data-step="1"
            aria-label="Next month">›</button>
        </div>
        <div class="date-range-grid">${weekdays.join("")}${cells.join("")}</div>
      </section>`;
  }

  private render(): void {
    const presets = PRESETS.map(
      (preset, index) => `<button type="button" data-preset="${index}">${preset.label}</button>`,
    );
    this.popover.innerHTML = `
      <div class="date-range-panels">${PANELS.map((panel) => this.calendarHtml(panel)).join("")}</div>
      <p class="date-range-note">Esc undoes the last choice · the start cannot be later than the end
        (to move the start past the end, change the end first).</p>
      <div class="date-range-footer">
        <div class="date-range-presets">${presets.join("")}</div>
        <button type="button" class="date-range-done" data-done>Done</button>
      </div>`;
  }

  private shiftMonth(panel: Panel, step: number): void {
    const { year, month } = this.views[panel];
    const date = new Date(Date.UTC(year, month + step, 1));
    this.views[panel] = { year: date.getUTCFullYear(), month: date.getUTCMonth() };
    this.render();
  }

  private applyPreset(preset: Preset): void {
    if (preset.days === null) {
      this.apply(null, true);
      return;
    }
    const end = this.max ?? todayIso();
    let start = addDays(end, -(preset.days - 1));
    if (this.min !== null && start < this.min) start = this.min;
    this.apply({ start, end }, true);
  }

  /** Elige un día en un panel. Se aplica al momento y el calendario sigue abierto. */
  private pickDay(panel: Panel, day: string): void {
    if (this.draft[panel] === day) return; // sin cambios: no añade un paso al historial
    this.history.push(this.range ? { ...this.range } : null);
    this.draft[panel] = day;
    this.apply({ ...this.draft }, false);
    this.render();
  }

  /** Deshace la última elección. Devuelve false si no hay ninguna que deshacer. */
  private undoLast(): boolean {
    if (this.history.length === 0) return false;
    const previous = this.history.pop() ?? null;
    this.draft = previous
      ? { ...previous }
      : { start: this.min ?? todayIso(), end: this.max ?? todayIso() };
    this.goToMonthOf("start");
    this.goToMonthOf("end");
    this.apply(previous, false);
    this.render();
    return true;
  }

  private handleClick(event: MouseEvent): void {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!button || button.disabled) return;

    const panel = button.dataset.panel as Panel | undefined;
    if (button.dataset.done !== undefined) this.close();
    else if (button.dataset.preset) this.applyPreset(PRESETS[Number(button.dataset.preset)]);
    else if (panel && button.dataset.step) this.shiftMonth(panel, Number(button.dataset.step));
    else if (panel && button.dataset.day) this.pickDay(panel, button.dataset.day);
  }

  private handleSelect(event: Event): void {
    const select = event.target as HTMLSelectElement;
    const panel = select.dataset.panel as Panel | undefined;
    if (!panel) return;
    if (select.classList.contains("month")) this.views[panel].month = Number(select.value);
    if (select.classList.contains("year")) this.views[panel].year = Number(select.value);
    this.render();
  }
}
