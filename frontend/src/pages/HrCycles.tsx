import { useQuery } from "@tanstack/react-query";
import { CalendarRange, Lock, Upload } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Alert, Badge, Card, Empty, Loading, PageHeader } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { useSession } from "../session";
import type { Cycle } from "../types";

export function CycleStatus({ cycle }: { cycle: Cycle }) {
  return (
    <div className="row">
      {cycle.active ? <Badge tone="green">Active</Badge> : <Badge>Older version</Badge>}
      {cycle.status === "locked" && <Badge tone="amber" icon={<Lock />}>Locked</Badge>}
    </div>
  );
}

export function HrCyclesPage() {
  const { timezone } = useSession();
  const navigate = useNavigate();
  const { data, error, isLoading } = useQuery({ queryKey: ["cycles"], queryFn: api.cycles });

  return (
    <>
      <PageHeader
        title="Attendance cycles"
        subtitle="Every upload is a cycle. The newest upload of a period is the active one managers see."
        actions={<Link className="btn primary" to="/hr/upload"><Upload />Upload workbook</Link>}
      />
      {error && <Alert tone="error">{(error as Error).message}</Alert>}
      <Card flush>
        {isLoading ? <Loading /> : !data?.cycles.length ? (
          <Empty title="No cycles yet" icon={<CalendarRange aria-hidden />}>
            Upload the SAP attendance file to start. <Link to="/hr/upload">Upload now</Link>
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Period</th><th>Uploaded</th><th className="num">Employees</th><th className="num">Managers</th>
                  <th className="num">Exceptions</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {data.cycles.map((cycle) => (
                  <tr key={cycle.id} className="clickable" onClick={() => navigate(`/hr/cycles/${cycle.id}`)}>
                    <td>
                      <Link className="primary-cell" to={`/hr/cycles/${cycle.id}`} onClick={(e) => e.stopPropagation()}>{cycle.name}</Link>
                      <div className="secondary">{cycle.original_filename}</div>
                    </td>
                    <td>
                      {formatDateTime(cycle.uploaded_at, timezone)}
                      <div className="secondary">{cycle.uploaded_by}</div>
                    </td>
                    <td className="num">{cycle.stats.employees ?? "–"}</td>
                    <td className="num">{cycle.stats.managers ?? "–"}</td>
                    <td className="num">{cycle.stats.exceptions ?? "–"}</td>
                    <td><CycleStatus cycle={cycle} /></td>
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
