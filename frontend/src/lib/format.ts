// Dates as the rest of the company reads them: dd/mm/yyyy, times in the portal's time zone.

const pad = (n: number) => String(n).padStart(2, "0");

/** "2026-09-21" -> "21/09/2026" (no time zone shift: it is a calendar date). */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

export function formatDateTime(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "";
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("day")}/${get("month")}/${get("year")} ${get("hour")}:${get("minute")}`;
}

/** Column header for a date: day/month and the weekday. */
export function dayHeader(iso: string): { label: string; weekday: string; weekend: boolean } {
  const date = new Date(`${iso}T00:00:00Z`);
  const weekdayIndex = date.getUTCDay();
  return {
    label: `${pad(date.getUTCDate())}/${pad(date.getUTCMonth() + 1)}`,
    weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][weekdayIndex] ?? "",
    weekend: weekdayIndex === 0 || weekdayIndex === 6,
  };
}

export function formatDuration(seconds: number | undefined): string {
  if (seconds === undefined) return "";
  return `${Math.floor(seconds / 3600)}:${pad(Math.floor((seconds % 3600) / 60))}:${pad(seconds % 60)}`;
}

export function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}
