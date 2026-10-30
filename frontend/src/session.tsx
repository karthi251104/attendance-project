// Who is signed in. Loaded once from /api/session and refreshed after sign-in / sign-out.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, setCsrfToken } from "./api";
import { Loading } from "./components/ui";
import type { Session, User } from "./types";

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const { data, error, refetch } = useQuery({
    queryKey: ["session"],
    queryFn: async () => {
      const session = await api.session();
      setCsrfToken(session.csrf_token);
      return session;
    },
    staleTime: 60_000,
  });
  if (error) {
    return (
      <div className="loading">
        Could not reach the portal. <button className="btn sm" onClick={() => refetch()}>Try again</button>
      </div>
    );
  }
  if (!data) return <Loading label="Starting…" />;
  return <SessionContext.Provider value={data}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside SessionProvider");
  return session;
}

/** Re-read the session (after sign-in or sign-out). */
export function useRefreshSession() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: ["session"] });
}

/** Render children only for a signed-in user (and only HR when `hr` is set). */
export function RequireUser({ hr = false, children }: { hr?: boolean; children: (user: User) => ReactNode }) {
  const { user } = useSession();
  const location = useLocation();
  if (!user) {
    const next = location.pathname + location.search;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }
  if (hr && !user.is_hr) return <Navigate to="/" replace />;
  return <>{children(user)}</>;
}
