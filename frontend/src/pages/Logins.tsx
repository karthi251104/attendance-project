// HR gives each manager an ID (their work email) and a password, resets forgotten ones, or disables a login.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Download, KeyRound, Search, UserPlus } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../api";
import { useToast } from "../components/toast";
import { Alert, Badge, Card, ConfirmDialog, Empty, Loading, PageHeader, Spinner } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { useSession } from "../session";
import type { NewLogin, Person } from "../types";

type Filter = "all" | "missing" | "locked" | "disabled";

function loginStatus(person: Person) {
  if (!person.has_login) return <Badge>No login yet</Badge>;
  if (!person.active) return <Badge tone="red">Disabled</Badge>;
  if (person.locked) return <Badge tone="amber">Locked (wrong passwords)</Badge>;
  return <Badge tone="green">Active</Badge>;
}

export function LoginsPage() {
  const { timezone, user } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data, error, isLoading } = useQuery({ queryKey: ["people"], queryFn: api.people });
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<NewLogin[] | null>(null);
  const [confirmDisable, setConfirmDisable] = useState<Person | null>(null);

  const people = data?.people ?? [];
  const missing = people.filter((p) => !p.has_login).length;
  const matches = (p: Person, f: Filter) => f === "all" || (f === "missing" && !p.has_login)
    || (f === "locked" && p.locked) || (f === "disabled" && p.has_login && !p.active);
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return people.filter((p) => matches(p, filter)
      && (!needle || p.email.includes(needle) || p.name.toLowerCase().includes(needle)));
  }, [people, search, filter]);

  async function run<T>(key: string, action: () => Promise<T>): Promise<T | undefined> {
    setBusy(key);
    try {
      return await action();
    } catch (err) {
      toast((err as Error).message, "error");
      return undefined;
    } finally {
      setBusy(null);
      await queryClient.invalidateQueries({ queryKey: ["people"] });
    }
  }

  async function createMissing() {
    const result = await run("create", api.createMissingLogins);
    if (result) setCredentials(result.created);
  }

  async function reset(person: Person) {
    const login = await run(person.email, () => api.resetPassword(person.email));
    if (login) setCredentials([login]);
  }

  async function setActive(person: Person, active: boolean) {
    const ok = await run(person.email, () => api.setLoginActive(person.email, active));
    setConfirmDisable(null);
    if (ok) toast(active ? `${person.email} can sign in again.` : `${person.email} can no longer sign in.`);
  }

  return (
    <>
      <PageHeader
        title="Logins"
        subtitle="Each person signs in with their work email and the password you give them. They can change it after signing in."
        actions={
          <button className="btn primary" onClick={createMissing} disabled={!missing || busy !== null}>
            {busy === "create" ? <Spinner /> : <UserPlus />}
            {missing ? `Create logins for ${missing} ${missing === 1 ? "person" : "people"}` : "Everyone has a login"}
          </button>
        }
      />
      {error && <Alert tone="error">{(error as Error).message}</Alert>}
      <Card flush>
        <div className="toolbar">
          <div className="input-icon">
            <Search aria-hidden />
            <input className="input" placeholder="Search name or email" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="segmented" role="tablist">
            {([["all", "All"], ["missing", "No login"], ["locked", "Locked"], ["disabled", "Disabled"]] as [Filter, string][]).map(([f, label]) => (
              <button key={f} role="tab" aria-selected={filter === f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
                {label} {people.filter((p) => matches(p, f)).length}
              </button>
            ))}
          </div>
        </div>
        {isLoading ? <Loading /> : !rows.length ? (
          <Empty title={people.length ? "Nobody matches" : "No managers yet"}>
            {people.length ? null : "Upload a workbook first: every manager in it can get a login."}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Person</th><th>Role</th><th>Login</th><th>Last sign-in</th><th /></tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.email}>
                    <td>
                      <div className="primary-cell">{p.name || p.email}</div>
                      <div className="secondary">{p.email}</div>
                    </td>
                    <td>{p.role === "hr" ? <Badge tone="primary">HR</Badge> : <Badge>Manager</Badge>}</td>
                    <td>{loginStatus(p)}</td>
                    <td>{p.last_login_at ? formatDateTime(p.last_login_at, timezone) : <span className="muted">Never</span>}</td>
                    <td>
                      <div className="row end">
                        <button className="btn sm" onClick={() => reset(p)} disabled={busy !== null}>
                          {busy === p.email ? <Spinner /> : <KeyRound />}{p.has_login ? "Reset password" : "Create login"}
                        </button>
                        {p.has_login && p.email !== user?.email && (p.active
                          ? <button className="btn sm ghost danger" onClick={() => setConfirmDisable(p)} disabled={busy !== null}>Disable</button>
                          : <button className="btn sm ghost" onClick={() => setActive(p, true)} disabled={busy !== null}>Enable</button>)}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {credentials && <CredentialsDialog logins={credentials} onClose={() => setCredentials(null)} />}
      {confirmDisable && (
        <ConfirmDialog title="Disable this login?" confirmLabel="Disable" danger busy={busy !== null}
                       onCancel={() => setConfirmDisable(null)} onConfirm={() => setActive(confirmDisable, false)}>
          <p>{confirmDisable.name || confirmDisable.email} is signed out at once and cannot sign in until you enable the login again.</p>
        </ConfirmDialog>
      )}
    </>
  );
}

/** The new passwords, shown once: copy or download them, then send each person their own. */
function CredentialsDialog({ logins, onClose }: { logins: NewLogin[]; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const lines = logins.map((l) => `${l.name || l.email}\t${l.email}\t${l.password}`);

  async function copy() {
    await navigator.clipboard.writeText(["Name\tEmail (login ID)\tPassword", ...lines].join("\n"));
    setCopied(true);
  }

  function download() {
    const quote = (value: string) => `"${value.replaceAll('"', '""')}"`;
    const csv = ["Name,Email (login ID),Password", ...logins.map((l) => [l.name, l.email, l.password].map(quote).join(","))].join("\r\n");
    const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "portal-logins.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="backdrop">
      <div className="card modal wide-modal" role="dialog" aria-modal="true" aria-labelledby="creds-title">
        <h2 id="creds-title">{logins.length === 1 ? "New password" : `${logins.length} new logins`}</h2>
        {!logins.length ? <p>Everyone already has a login.</p> : (
          <>
            <Alert tone="warn">These passwords are shown <b>only now</b>. Copy or download them, send each person their own,
              then delete the file. People can change their password after signing in.</Alert>
            <div className="table-wrap creds">
              <table className="table">
                <thead><tr><th>Name</th><th>Login ID (email)</th><th>Password</th></tr></thead>
                <tbody>
                  {logins.map((l) => (
                    <tr key={l.email}><td>{l.name || "—"}</td><td>{l.email}</td><td><code className="password">{l.password}</code></td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <div className="row">
          {logins.length > 0 && <>
            <button className="btn" onClick={copy}>{copied ? <Check /> : <Copy />}{copied ? "Copied" : "Copy all"}</button>
            <button className="btn" onClick={download}><Download />Download CSV</button>
          </>}
          <button className="btn primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
