import { useQuery } from "@tanstack/react-query";
import { CalendarRange, ChevronRight } from "lucide-react";
import { Link, Navigate } from "react-router-dom";
import { api } from "../api";
import { Alert, Card, Empty, Loading, PageHeader } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { useSession } from "../session";
import type { User } from "../types";

/** HR goes to the cycles list; a manager goes straight to their team (or picks one of several cycles). */
export function HomePage({ user }: { user: User }) {
  const { timezone } = useSession();
  const { data, error, isLoading } = useQuery({ queryKey: ["my-cycles"], queryFn: api.myCycles, enabled: !user.is_hr });

  if (user.is_hr) return <Navigate to="/hr" replace />;
  if (error) return <Alert tone="error">{(error as Error).message}</Alert>;
  if (isLoading || !data) return <Loading />;
  if (data.cycles.length === 1) return <Navigate to={`/team/${data.cycles[0]!.id}`} replace />;

  return (
    <>
      <PageHeader title={`Hello, ${user.name}`} subtitle="Choose the attendance period to review." />
      <Card flush>
        {!data.cycles.length ? (
          <Empty title="Nothing to review" icon={<CalendarRange aria-hidden />}>
            There is no attendance for you to review right now. HR will email you when there is.
          </Empty>
        ) : (
          <table className="table">
            <tbody>
              {data.cycles.map((cycle) => (
                <tr key={cycle.id}>
                  <td>
                    <Link className="primary-cell" to={`/team/${cycle.id}`}>{cycle.name}</Link>
                    {cycle.correction_deadline && <div className="secondary">Open until {formatDateTime(cycle.correction_deadline, timezone)}</div>}
                  </td>
                  <td className="num"><Link className="btn sm" to={`/team/${cycle.id}`}>Review<ChevronRight /></Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

export function NotFoundPage() {
  return (
    <Card>
      <Empty title="Page not found">The link may be old. <Link to="/">Go to the start page</Link></Empty>
    </Card>
  );
}
