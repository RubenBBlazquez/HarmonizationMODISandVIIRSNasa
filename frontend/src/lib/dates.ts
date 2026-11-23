// Fechas. Los días van como "AAAA-MM-DD" (UTC), el formato de la API y del selector de rango.

/** Hoy (UTC). */
export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Día de un año, mes (0-11) y día del mes. */
export function isoFromParts(year: number, month: number, day: number): string {
  return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
}

/** Suma (o resta) días a una fecha "AAAA-MM-DD". */
export function addDays(iso: string, days: number): string {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

/** Instante ISO como "2022-08-01 14:30 UTC". */
export function formatDate(iso: string | number | null | undefined): string {
  return typeof iso === "string" ? `${iso.slice(0, 16).replace("T", " ")} UTC` : "—";
}

/** Día UTC de un instante ISO (2022-08-01T14:30:00Z) en formato día-mes-año (01-08-2022). */
export function formatDay(iso: string | number | null | undefined): string {
  if (typeof iso !== "string") return "—";
  const [year, month, day] = iso.slice(0, 10).split("-");
  return `${day}-${month}-${year}`;
}

/** "AAAA-MM-DD" -> "DD-MM" (para describir las mismas fechas en otros años). */
export function formatDayMonth(isoDate: string): string {
  const [, month, day] = isoDate.split("-");
  return `${day}-${month}`;
}

// --- Fechas legibles ("14 Aug 2024") -----------------------------------------------------------
const SHORT_DATE = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const DAY_MONTH = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

function parseDate(value: string | number | null | undefined): Date | null {
  if (value == null || value === "") return null;
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "14 Aug 2024", "12 → 14 Aug 2024" o "30 Jul → 2 Aug 2024". */
export function dateRangeLabel(
  first: string | number | null | undefined,
  last: string | number | null | undefined,
): string {
  const [a, b] = [parseDate(first), parseDate(last)];
  if (!a) return "No date";
  const clean = (text: string): string => text.replace(/\./g, "");
  if (!b || a.toISOString().slice(0, 10) === b.toISOString().slice(0, 10)) return clean(SHORT_DATE.format(a));
  if (a.getUTCFullYear() !== b.getUTCFullYear()) {
    return `${clean(SHORT_DATE.format(a))} → ${clean(SHORT_DATE.format(b))}`;
  }
  if (a.getUTCMonth() === b.getUTCMonth()) {
    return `${a.getUTCDate()} → ${clean(SHORT_DATE.format(b))}`;
  }
  return `${clean(DAY_MONTH.format(a))} → ${clean(SHORT_DATE.format(b))}`;
}

/** "12 min ago", "3 h ago", "2 d ago": cuánto hace de un instante ISO ("—" si no es válido). */
export function timeAgoLabel(iso: string | number | null | undefined): string {
  const time = typeof iso === "string" ? Date.parse(iso) : NaN;
  if (Number.isNaN(time)) return "—";
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}
