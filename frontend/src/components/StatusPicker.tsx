// The small menu that opens under a day cell to pick its status.

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { STATUS_INFO, statusTone } from "../lib/attendance";
import { dayHeader, formatDate, formatDuration } from "../lib/format";
import type { Employee } from "../types";

export interface PickerTarget {
  employee: Employee;
  date: string;
  rect: DOMRect;
}

const WIDTH = 236;

export function StatusPicker({ target, statuses, current, onChoose, onClose }: {
  target: PickerTarget; statuses: string[]; current: string | null;
  onChoose: (value: string) => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ top: target.rect.bottom + 6, left: target.rect.left });

  // Keep the menu inside the window: below the cell, or above it when there is no room.
  useLayoutEffect(() => {
    const height = ref.current?.offsetHeight ?? 220;
    const below = target.rect.bottom + 6;
    const top = below + height > window.innerHeight - 8 ? Math.max(8, target.rect.top - height - 6) : below;
    const left = Math.min(Math.max(8, target.rect.left + target.rect.width / 2 - WIDTH / 2), window.innerWidth - WIDTH - 8);
    setPosition({ top, left });
  }, [target]);

  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>("button.current, button")?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    const onDown = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) onClose(); };
    const onScroll = () => onClose();
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("resize", onScroll);
    document.querySelector(".grid-wrap")?.addEventListener("scroll", onScroll);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", onScroll);
      document.querySelector(".grid-wrap")?.removeEventListener("scroll", onScroll);
    };
  }, [onClose]);

  const { employee, date } = target;
  const head = dayHeader(date);
  const schedule = employee.schedule[date];
  const sap = employee.calculated_days[date];

  // The position comes from the clicked cell. React applies a style object through the DOM, which the CSP allows.
  return (
    <div ref={ref} className="card picker" role="menu" aria-label="Choose a status"
         style={{ top: position.top, left: position.left }}>
      <header>
        <b>{employee.employee_name}</b>
        {head.weekday} {formatDate(date)}
        {schedule ? ` · ${schedule} · ${formatDuration(employee.worked_seconds[date])}` : " · no SAP data"}
        {sap ? ` · SAP says ${sap}` : ""}
      </header>
      <div className="picker-options">
        {statuses.map((code) => (
          <button key={code} type="button" role="menuitem"
                  className={`tone-${statusTone(code)}${code === current ? " current" : ""}`}
                  onClick={() => onChoose(code)}>
            <code>{code}</code>{STATUS_INFO[code]?.label ?? code}
          </button>
        ))}
      </div>
    </div>
  );
}
