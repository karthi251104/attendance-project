import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowLeft, CalendarCheck, Download, FileClock, History, Lock, Mail, Search, Send, Unlock, Users,
  UserSquare2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, links } from "../api";
import { Alert, Badge, Card, ConfirmDialog, Empty, Loading, PageHeader, Spinner, Stat } from "../components/ui";
import { useToast } from "../components/toast";
import { formatDate, formatDateTime, plural } from "../lib/format";
import { useSession } from "../session";
import type { CycleDetail, ExceptionRow, ManagerRow } from "../types";
import { CycleStatus } from "./HrCycles";

export function CycleReviewPage() {
  const { cycleId = "" } = useParams();
  const cycle = useQuery({ queryKey: ["cycle", cycleId], queryFn: () => api.cycle(cycleId) });

  if (cycle.error) return <Alert tone="error">{(cycle.error as Error).message}</Alert>;
  if (!cycle.data) return <Loading />;
  return <Review cycle={cycle.data} />;
}

function Review({ cycle }: { cycle: CycleDetail }) {
  const { timezone } = useSession();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function act(action: "lock" | "unlock" | "activate") {
    setBusy(true);
    try {
      const updated = await api.setStatus(cycle.id, action);
      queryClient.setQueryData(["cycle", cycle.id], updated);
      await queryClient.invalidateQueries({ queryKey: ["cycles"] });
      toast({ lock: "Cycle locked. Nobody can edit until you unlock it.", unlock: "Cycle unlocked.",
               activate: "This upload is now the active version." }[action]);
    } catch (error) {
      toast((error as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        back={<Link className="breadcrumb" to="/hr"><ArrowLeft size={16} />Cycles</Link>}
        title={cycle.name}
        subtitle={<>
          <span>Uploaded {formatDateTime(cycle.uploaded_at, timezone)} by {cycle.uploaded_by}</span>
          <span>{cycle.original_filename}</span>
          <CycleStatus cycle={cycle} />
        </>}
        actions={<>
          <Link className="btn" to={`/hr/cycles/${cycle.id}/employees`}><Users />All employees</Link>
          <Link className="btn" to={`/hr/cycles/${cycle.id}/changes`}><History />Change history</Link>
          <a className="btn primary" href={links.download(cycle.id)} download><Download />Download workbook</a>
        </>}
      />

      <div className="stack">
        {!cycle.active && (
          <Alert tone="warn">
            <div className="row">
              <span>This is an older upload for this period. Managers can only view it.</span>
              <button className="btn sm" onClick={() => act("activate")} disabled={busy}>Make this the active version</button>
            </div>
          </Alert>
        )}

        <div className="stats">
          <Stat icon={<Users />} tone="primary" value={cycle.stats.employees ?? 0} label="Employees" />
          <Stat icon={<UserSquare2 />} tone="violet" value={cycle.stats.managers ?? 0} label="Managers" />
          <Stat icon={<AlertTriangle />} tone={cycle.stats.exceptions ? "amber" : "green"} value={cycle.stats.exceptions ?? 0} label="Exceptions" />
          <Stat icon={<CalendarCheck />} tone="green" value={cycle.stats.filled_from_sap ?? 0} label="Days calculated from SAP" />
        </div>

        <Corrections cycle={cycle} busy={busy} onAction={act} />
        <Managers cycle={cycle} />
        <Exceptions cycleId={cycle.id} />
      </div>
    </>
  );
}

function Corrections({ cycle, busy, onAction }: {
  cycle: CycleDetail; busy: boolean; onAction: (action: "lock" | "unlock") => Promise<void>;
}) {
  const { timezone } = useSession();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [deadline, setDeadline] = useState(cycle.deadline_date ?? "");
  const [saving, setSaving] = useState(false);
  const [confirmLock, setConfirmLock] = useState(false);
  const locked = cycle.status === "locked";

  async function saveDeadline(value: string | null) {
    setSaving(true);
    try {
      const updated = await api.setDeadline(cycle.id, value);
      queryClient.setQueryData(["cycle", cycle.id], updated);
      setDeadline(updated.deadline_date ?? "");
      toast(value ? `Managers can edit until ${formatDate(value)} 23:59.` : "Deadline removed.");
    } catch (error) {
      toast((error as Error).message, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card
      title="Corrections"
      subtitle={locked ? "Locked: nobody can edit, including HR." : cycle.correction_deadline
        ? `Managers can edit until ${formatDateTime(cycle.correction_deadline, timezone)}.`
        : "Managers can edit until you lock the cycle."}
      actions={locked ? <Badge tone="amber" icon={<Lock />}>Locked</Badge> : <Badge tone="green">Open</Badge>}
    >
      <div className="row">
        <label className="field">
          <span>Deadline</span>
          <div className="row">
            <input className="input" type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
            <button className="btn" disabled={saving || !deadline || deadline === cycle.deadline_date}
                    onClick={() => saveDeadline(deadline)}>{saving && <Spinner />}Save deadline</button>
            {cycle.deadline_date && <button className="btn ghost" disabled={saving} onClick={() => saveDeadline(null)}>Remove</button>}
          </div>
        </label>
        <span className="spacer" />
        {locked
          ? <button className="btn" onClick={() => onAction("unlock")} disabled={busy}><Unlock />Unlock cycle</button>
          : <button className="btn" onClick={() => setConfirmLock(true)} disabled={busy}><Lock />Lock cycle</button>}
      </div>
      {confirmLock && (
        <ConfirmDialog title="Lock this cycle?" confirmLabel="Lock cycle" busy={busy}
                       onCancel={() => setConfirmLock(false)}
                       onConfirm={async () => { await onAction("lock"); setConfirmLock(false); }}>
          <p>Nobody will be able to edit, including HR, until you unlock it. Do this when corrections are closed.</p>
        </ConfirmDialog>
      )}
    </Card>
  );
}

function EmailStatus({ manager }: { manager: ManagerRow }) {
  const { timezone } = useSession();
  if (!manager.email_status) return <span className="muted">Not sent</span>;
  return (
    <div>
      {manager.email_status === "sent"
        ? <Badge tone="green">Sent</Badge>
        : <Badge tone="red" title={manager.email_error ?? ""}>Failed</Badge>}
      <div className="secondary">{formatDateTime(manager.email_sent_at, timezone)}</div>
      {manager.email_status === "failed" && <div className="secondary">{manager.email_error}</div>}
    </div>
  );
}

function Managers({ cycle }: { cycle: CycleDetail }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { data, isLoading } = useQuery({ queryKey: ["managers", cycle.id], queryFn: () => api.managers(cycle.id) });
  const [search, setSearch] = useState("");
  const [confirmAll, setConfirmAll] = useState(false);
  const [sending, setSending] = useState<string | null>(null);

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (data?.managers ?? []).filter((m) => !needle
      || m.manager_name.toLowerCase().includes(needle) || m.manager_email.toLowerCase().includes(needle));
  }, [data, search]);

  async function send(managerEmail?: string) {
    setSending(managerEmail ?? "all");
    try {
      const { queued } = await api.sendEmails(cycle.id, managerEmail);
      toast(managerEmail ? `Email to ${managerEmail} is on its way.` : `Sending ${plural(queued, "email")}…`);
      // Emails go out in the background; refresh the status column shortly after.
      for (const delay of [1500, 5000, 15000]) {
        window.setTimeout(() => queryClient.invalidateQueries({ queryKey: ["managers", cycle.id] }), delay);
      }
    } catch (error) {
      toast((error as Error).message, "error");
    } finally {
      setSending(null);
      setConfirmAll(false);
    }
  }

  return (
    <Card
      flush
      title="Managers"
      subtitle="Each manager gets one email with a link. No employee data is in the email."
      actions={<>
        <div className="input-icon">
          <Search aria-hidden />
          <input className="input" placeholder="Search managers" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <button className="btn primary" onClick={() => setConfirmAll(true)} disabled={!data?.managers.length || sending !== null}>
          <Send />Email all managers
        </button>
      </>}
    >
      {isLoading ? <Loading /> : !rows.length ? <Empty title="No managers match" /> : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Manager</th><th className="num">Team</th><th>Email</th><th className="num">Changes</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((m) => (
                <tr key={m.manager_email || m.manager_name}>
                  <td>
                    <div className="primary-cell">{m.manager_name || "—"}</div>
                    <div className="secondary">{m.manager_email || <Badge tone="red">Email missing</Badge>}</div>
                  </td>
                  <td className="num">{m.team_size}</td>
                  <td><EmailStatus manager={m} /></td>
                  <td className="num">{m.changes || <span className="muted">0</span>}</td>
                  <td>
                    <div className="row end">
                      {m.manager_email && (
                        <>
                          <Link className="btn sm ghost" to={`/hr/cycles/${cycle.id}/employees?manager=${encodeURIComponent(m.manager_email)}`}>
                            View team
                          </Link>
                          <button className="btn sm" onClick={() => send(m.manager_email)} disabled={sending !== null}>
                            {sending === m.manager_email ? <Spinner /> : <Mail />}{m.email_status ? "Resend" : "Send"}
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {confirmAll && (
        <ConfirmDialog title="Email all managers?" confirmLabel={`Send ${plural(data?.managers.length ?? 0, "email")}`}
                       busy={sending === "all"} onCancel={() => setConfirmAll(false)} onConfirm={() => send()}>
          <p>Every manager in this cycle ({data?.managers.length}) gets the review email with their link.</p>
        </ConfirmDialog>
      )}
    </Card>
  );
}

const SEVERITY_TONE = { error: "red", warning: "amber", info: "blue" } as const;
type Filter = "all" | ExceptionRow["severity"];

function Exceptions({ cycleId }: { cycleId: string }) {
  const { data, isLoading } = useQuery({ queryKey: ["exceptions", cycleId], queryFn: () => api.exceptions(cycleId) });
  const [filter, setFilter] = useState<Filter>("all");
  const all = data?.exceptions ?? [];
  const count = (severity: Filter) => (severity === "all" ? all.length : all.filter((e) => e.severity === severity).length);
  const shown = all.filter((e) => filter === "all" || e.severity === filter).slice(0, 300);

  return (
    <Card
      flush
      title={`Exceptions (${all.length})`}
      subtitle="Things HR should check: missing people in HC_Teamlease, unknown schedules, missing SAP rows."
      actions={<>
        <div className="segmented" role="tablist">
          {(["all", "error", "warning", "info"] as Filter[]).map((f) => (
            <button key={f} role="tab" aria-selected={filter === f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : `${f[0]!.toUpperCase()}${f.slice(1)}s`} {count(f)}
            </button>
          ))}
        </div>
        <a className="btn" href={links.exceptionsCsv(cycleId)}><FileClock />CSV</a>
      </>}
    >
      {isLoading ? <Loading /> : !shown.length ? (
        <Empty title="Nothing to check" icon={<CalendarCheck aria-hidden />}>
          Every employee was found in HC_Teamlease and every day could be calculated.
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Severity</th><th>Employee</th><th>Date</th><th>What to check</th></tr></thead>
            <tbody>
              {shown.map((e, i) => (
                <tr key={i}>
                  <td><Badge tone={SEVERITY_TONE[e.severity]}>{e.severity}</Badge></td>
                  <td>
                    {e.employee_id ? <><div className="primary-cell">{e.employee_name || "—"}</div><div className="secondary">{e.employee_id}</div></> : "—"}
                  </td>
                  <td>{formatDate(e.date) || "—"}</td>
                  <td>
                    <div>{e.message}</div>
                    <div className="secondary">{e.type.replaceAll("_", " ")}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {all.length > shown.length && filter === "all" && (
            <p className="muted small table-note">Showing the first {shown.length}. Download the CSV for all of them.</p>
          )}
        </div>
      )}
    </Card>
  );
}
