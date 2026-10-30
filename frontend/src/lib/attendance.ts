// Attendance codes, their labels and colours, totals, and the pending-edit bookkeeping for the grid.

import type { DayMap } from "../types";

export const STATUS_INFO: Record<string, { label: string; tone: string }> = {
  P: { label: "Present", tone: "green" },
  A: { label: "Absent", tone: "red" },
  OFF: { label: "Weekly off", tone: "slate" },
  PH: { label: "Public holiday", tone: "violet" },
  CO: { label: "Comp off", tone: "blue" },
  ML: { label: "Maternity leave", tone: "pink" },
  PL: { label: "Leave (PL)", tone: "amber" },
  "?": { label: "Unknown schedule", tone: "orange" },
};

/** The columns the Excel totals count, in the same order. */
export const TOTAL_CODES = ["P", "A", "OFF", "CO", "ML", "PL", "PH"] as const;

export function statusTone(code: string | null | undefined): string {
  return (code && STATUS_INFO[code]?.tone) || "empty";
}

export function totals(days: DayMap): Record<string, number> {
  const counts: Record<string, number> = Object.fromEntries(TOTAL_CODES.map((code) => [code, 0]));
  for (const value of Object.values(days)) {
    if (value && value in counts) counts[value] = (counts[value] ?? 0) + 1;
  }
  counts.Total = TOTAL_CODES.reduce((sum, code) => sum + (counts[code] ?? 0), 0);
  return counts;
}

export const editKey = (employeeId: string, date: string) => `${employeeId}|${date}`;

export interface PendingEdit {
  employee_id: string;
  date: string;
  value: string;
  old_value: string | null;
}

/**
 * Record a change in the pending map. Setting a cell back to the value it had when loaded removes
 * the pending edit, so "change and change back" leaves nothing to save.
 */
export function applyEdit(
  pending: Map<string, PendingEdit>, employeeId: string, date: string, value: string, loaded: string | null,
): Map<string, PendingEdit> {
  const next = new Map(pending);
  const key = editKey(employeeId, date);
  if (value === (loaded ?? "")) next.delete(key);
  else next.set(key, { employee_id: employeeId, date, value, old_value: loaded });
  return next;
}

/** Days with the pending edits applied (what the grid shows and the live totals count). */
export function withEdits(employeeId: string, days: DayMap, pending: Map<string, PendingEdit>): DayMap {
  const result: DayMap = { ...days };
  for (const edit of pending.values()) {
    if (edit.employee_id === employeeId) result[edit.date] = edit.value;
  }
  return result;
}
