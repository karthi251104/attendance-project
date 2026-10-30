import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, FileDown, History, Search } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, links } from "../api";
import { Alert, Badge, Card, Empty, Loading, PageHeader } from "../components/ui";
import { statusTone } from "../lib/attendance";
import { formatDate, formatDateTime, plural } from "../lib/format";
import { useSession } from "../session";

export function ChangesPage() {
  const { cycleId = "" } = useParams();
  const { timezone } = useSession();
  const cycle = useQuery({ queryKey: ["cycle", cycleId], queryFn: () => api.cycle(cycleId) });
  const managers = useQuery({ queryKey: ["managers", cycleId], queryFn: () => api.managers(cycleId) });
  const [employee, setEmployee] = useState("");
  const [changedBy, setChangedBy] = useState("");
  const [manager, setManager] = useState("");
  const filters = { employee: employee.trim(), changed_by: changedBy.trim(), manager };
  const changes = useQuery({ queryKey: ["changes", cycleId, filters], queryFn: () => api.changes(cycleId, filters) });

  return (
    <>
      <PageHeader
        back={<Link className="breadcrumb" to={`/hr/cycles/${cycleId}`}><ArrowLeft size={16} />Review</Link>}
        title="Change history"
        subtitle={<span>{cycle.data?.name} · every change made in the portal, newest first</span>}
        actions={<a className="btn" href={links.changesCsv(cycleId, filters)}><FileDown />Download CSV</a>}
      />
      <Card flush>
        <div className="toolbar">
          <div className="input-icon">
            <Search aria-hidden />
            <input className="input" placeholder="Employee ID" value={employee} onChange={(e) => setEmployee(e.target.value)} />
          </div>
          <input className="input" placeholder="Changed by (email)" value={changedBy} onChange={(e) => setChangedBy(e.target.value)} />
          <select className="select" value={manager} onChange={(e) => setManager(e.target.value)} aria-label="Team">
            <option value="">All teams</option>
            {managers.data?.managers.filter((m) => m.manager_email).map((m) => (
              <option key={m.manager_email} value={m.manager_email}>{m.manager_name}</option>
            ))}
          </select>
          <span className="spacer" />
          {changes.data && <span className="muted">{plural(changes.data.changes.length, "change")}</span>}
        </div>
        {changes.error ? <div className="card-body"><Alert tone="error">{(changes.error as Error).message}</Alert></div>
          : changes.isLoading ? <Loading /> : !changes.data?.changes.length ? (
            <Empty title="No changes yet" icon={<History aria-hidden />}>Edits made by managers and HR appear here.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>When</th><th>Employee</th><th>Day</th><th>Change</th><th>By</th><th>Comment</th></tr>
                </thead>
                <tbody>
                  {changes.data.changes.map((c, i) => (
                    <tr key={i}>
                      <td>{formatDateTime(c.changed_at, timezone)}</td>
                      <td><div className="primary-cell">{c.employee_name}</div><div className="secondary">{c.employee_id}</div></td>
                      <td>{formatDate(c.date)}</td>
                      <td>
                        <div className="row">
                          <Badge tone={statusTone(c.old_value)}>{c.old_value ?? "–"}</Badge>→
                          <Badge tone={statusTone(c.new_value)}>{c.new_value}</Badge>
                        </div>
                      </td>
                      <td>{c.changed_by_email}<div className="secondary">{c.changed_by_role === "hr" ? "HR" : "Manager"}</div></td>
                      <td>{c.comment ?? <span className="muted">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
    </>
  );
}
