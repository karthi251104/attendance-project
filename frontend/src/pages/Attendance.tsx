// The attendance grid. Managers see their own team; HR sees everyone and can filter.
// Click a day to change it; changes stay "unsaved" (yellow) until Save sends them in one request.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Lock, Search, Users } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { StatusPicker, type PickerTarget } from "../components/StatusPicker";
import { useToast } from "../components/toast";
import { Alert, Badge, Card, Empty, Loading, PageHeader, Spinner } from "../components/ui";
import {
  STATUS_INFO, TOTAL_CODES, applyEdit, editKey, statusTone, totals, withEdits, type PendingEdit,
} from "../lib/attendance";
import { dayHeader, formatDate, formatDateTime, formatDuration, plural } from "../lib/format";
import { useSession } from "../session";
import type { CycleDetail, Employee } from "../types";

export function AttendancePage({ hrView }: { hrView: boolean }) {
  const { cycleId = "" } = useParams();
  const cycle = useQuery({ queryKey: ["cycle", cycleId], queryFn: () => api.cycle(cycleId) });
  if (cycle.error) return <Alert tone="error">{(cycle.error as Error).message}</Alert>;
  if (!cycle.data) return <Loading />;
  return <Attendance cycle={cycle.data} hrView={hrView && cycle.data.role === "hr"} />;
}

function Attendance({ cycle, hrView }: { cycle: CycleDetail; hrView: boolean }) {
  const { timezone, statuses } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const manager = hrView ? params.get("manager") ?? "" : "";
  const department = hrView ? params.get("department") ?? "" : "";
  const [search, setSearch] = useState("");
  const [pending, setPending] = useState<Map<string, PendingEdit>>(new Map());
  const [picker, setPicker] = useState<PickerTarget | null>(null);
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);

  const employeesKey = ["employees", cycle.id, manager, department];
  const employees = useQuery({ queryKey: employeesKey, queryFn: () => api.employees(cycle.id, { manager, department }) });
  const filters = useQuery({ queryKey: ["managers", cycle.id], queryFn: () => api.managers(cycle.id), enabled: hrView });

  // Warn before leaving the page with unsaved changes.
  useEffect(() => {
    if (!pending.size) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending.size]);

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (employees.data?.employees ?? []).filter((e) => !needle
      || e.employee_name.toLowerCase().includes(needle) || e.employee_id.includes(needle));
  }, [employees.data, search]);

  // Pending edits grouped per employee, so unchanged rows keep the same props and skip re-rendering.
  const pendingByEmployee = useMemo(() => {
    const grouped = new Map<string, Map<string, PendingEdit>>();
    for (const [key, edit] of pending) {
      const forRow = grouped.get(edit.employee_id) ?? new Map<string, PendingEdit>();
      forRow.set(key, edit);
      grouped.set(edit.employee_id, forRow);
    }
    return grouped;
  }, [pending]);

  const openPicker = useCallback((target: PickerTarget) => setPicker(target), []);

  function choose(value: string) {
    if (!picker) return;
    setPending((current) => applyEdit(current, picker.employee.employee_id, picker.date, value,
                                      picker.employee.days[picker.date] ?? null));
    setPicker(null);
  }

  function setFilter(name: "manager" | "department", value: string) {
    if (pending.size && !window.confirm("You have unsaved changes. Discard them and change the filter?")) return;
    setPending(new Map());
    const next = new URLSearchParams(params);
    if (value) next.set(name, value); else next.delete(name);
    setParams(next, { replace: true });
  }

  async function save() {
    setSaving(true);
    try {
      const result = await api.save(cycle.id, [...pending.values()], comment.trim() || null);
      const conflicted = new Set(result.conflicts.map((c) => editKey(c.employee_id, c.date)));
      setPending((current) => new Map([...current].filter(([key]) => conflicted.has(key))));
      setComment("");
      await queryClient.invalidateQueries({ queryKey: ["employees", cycle.id] });
      if (conflicted.size) {
        toast(`${plural(result.saved, "change")} saved. ${plural(conflicted.size, "day")} changed by someone else in the meantime; check them and save again.`, "warn");
      } else {
        toast(`${plural(result.saved, "change")} saved.`);
      }
    } catch (error) {
      toast((error as Error).message, "error");
    } finally {
      setSaving(false);
    }
  }

  const managerName = filters.data?.managers.find((m) => m.manager_email === manager)?.manager_name;

  return (
    <>
      <PageHeader
        back={hrView ? <Link className="breadcrumb" to={`/hr/cycles/${cycle.id}`}><ArrowLeft size={16} />Review</Link> : undefined}
        title={hrView ? (managerName ? `${managerName}'s team` : "All employees") : "Your team's attendance"}
        subtitle={<>
          <span>{cycle.name}</span>
          {cycle.correction_deadline && <span>Corrections open until {formatDateTime(cycle.correction_deadline, timezone)}</span>}
          {cycle.status === "locked" && <Badge tone="amber" icon={<Lock />}>Locked</Badge>}
        </>}
      />

      <div className="stack">
        {cycle.read_only_reason && <Alert tone="warn">{cycle.read_only_reason} The attendance is shown read-only.</Alert>}
        {!hrView && cycle.can_edit && (
          <Alert tone="info">Check each day. Click a day to change it, then press <b>Save changes</b>. Hover a day to see the SAP schedule and worked hours.</Alert>
        )}

        <Card flush>
          <div className="toolbar">
            <div className="input-icon">
              <Search aria-hidden />
              <input className="input" placeholder="Search name or ID" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            {hrView && (
              <>
                <select className="select" value={manager} onChange={(e) => setFilter("manager", e.target.value)} aria-label="Manager">
                  <option value="">All managers</option>
                  {filters.data?.managers.filter((m) => m.manager_email).map((m) => (
                    <option key={m.manager_email} value={m.manager_email}>{m.manager_name} ({m.team_size})</option>
                  ))}
                </select>
                <select className="select" value={department} onChange={(e) => setFilter("department", e.target.value)} aria-label="Department">
                  <option value="">All departments</option>
                  {filters.data?.departments.map((d) => <option key={d}>{d}</option>)}
                </select>
              </>
            )}
            <span className="spacer" />
            <span className="muted"><Users size={14} /> {plural(rows.length, "employee")}</span>
          </div>
          <div className="legend">
            {Object.entries(STATUS_INFO).filter(([code]) => code === "?" || statuses.includes(code)).map(([code, info]) => (
              <span key={code}><span className={`badge tone-${info.tone}`}>{code}</span> {info.label}</span>
            ))}
            <span><span className="dot edited" />Changed in the portal</span>
            <span><span className="dot dirty" />Not saved yet</span>
          </div>

          {employees.isLoading ? <Loading /> : employees.error ? (
            <div className="card-body"><Alert tone="error">{(employees.error as Error).message}</Alert></div>
          ) : !rows.length ? <Empty title="No employees to show" /> : (
            <div className="grid-wrap">
              <table className="grid">
                <thead>
                  <tr>
                    <th className="sticky-col emp">Employee</th>
                    {cycle.dates.map((iso) => {
                      const head = dayHeader(iso);
                      return <th key={iso} className={`day-head${head.weekend ? " weekend" : ""}`}>{head.label}<small>{head.weekday}</small></th>;
                    })}
                    {[...TOTAL_CODES, "Total"].map((code) => <th key={code} className="total-head" title={STATUS_INFO[code]?.label}>{code}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((emp) => (
                    <Row key={emp.employee_id} emp={emp} dates={cycle.dates} hrView={hrView} canEdit={cycle.can_edit}
                         pending={pendingByEmployee.get(emp.employee_id) ?? NO_EDITS} onOpen={openPicker} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {picker && (
        <StatusPicker target={picker} statuses={statuses}
                      current={withEdits(picker.employee.employee_id, picker.employee.days, pending)[picker.date] ?? null}
                      onChoose={choose} onClose={() => setPicker(null)} />
      )}

      {pending.size > 0 && (
        <div className="savebar" role="region" aria-label="Unsaved changes">
          <span className="count">{plural(pending.size, "unsaved change")}</span>
          <input className="input" maxLength={500} placeholder="Comment for HR (optional)" value={comment}
                 onChange={(e) => setComment(e.target.value)} />
          <button className="btn ghost" onClick={() => setPending(new Map())} disabled={saving}>Discard</button>
          <button className="btn primary" onClick={save} disabled={saving}>{saving && <Spinner />}Save changes</button>
        </div>
      )}
    </>
  );
}

const NO_EDITS = new Map<string, PendingEdit>();

const Row = memo(function Row({ emp, dates, hrView, canEdit, pending, onOpen }: {
  emp: Employee; dates: string[]; hrView: boolean; canEdit: boolean;
  pending: Map<string, PendingEdit>; onOpen: (target: PickerTarget) => void;
}) {
  const days = withEdits(emp.employee_id, emp.days, pending);
  const counts = totals(days);
  const withdrawn = (emp.emp_status ?? "").toLowerCase() === "withdrawn";

  return (
    <tr>
      <th scope="row" className="sticky-col emp">
        <div className="name" title={emp.employee_name}>{emp.employee_name}</div>
        <div className="meta">
          <span>{emp.employee_id}</span>
          {hrView && emp.manager_name && <span>· {emp.manager_name}</span>}
          {withdrawn && <Badge tone="amber">Withdrawn{emp.lwd ? ` · LWD ${formatDate(emp.lwd)}` : ""}</Badge>}
        </div>
      </th>
      {dates.map((iso) => {
        const value = days[iso] ?? null;
        const dirty = pending.has(editKey(emp.employee_id, iso));
        const edited = !dirty && (emp.days[iso] ?? null) !== (emp.original_days[iso] ?? null);
        const head = dayHeader(iso);
        const schedule = emp.schedule[iso];
        const tip = [`${head.weekday} ${formatDate(iso)}`, schedule, formatDuration(emp.worked_seconds[iso]),
                     emp.calculated_days[iso] && emp.calculated_days[iso] !== value ? `SAP: ${emp.calculated_days[iso]}` : null,
                     schedule ? null : "No SAP data"].filter(Boolean).join(" · ");
        const className = `cell tone-${statusTone(value)}${dirty ? " dirty" : ""}${edited ? " edited" : ""}`;
        return (
          <td key={iso} className={`day${head.weekend ? " weekend" : ""}`}>
            {canEdit ? (
              <button type="button" className={className} title={tip} aria-label={`${emp.employee_name}, ${formatDate(iso)}: ${value ?? "empty"}`}
                      onClick={(e) => onOpen({ employee: emp, date: iso, rect: e.currentTarget.getBoundingClientRect() })}>
                {value ?? "–"}
              </button>
            ) : <span className={className} title={tip}>{value ?? "–"}</span>}
          </td>
        );
      })}
      {[...TOTAL_CODES, "Total"].map((code) => (
        <td key={code} className={`total${code === "Total" ? " sum" : ""}`}>{counts[code] ?? 0}</td>
      ))}
    </tr>
  );
});
