// Small fetch wrapper: same-origin cookies, the CSRF header on every change, and readable errors.

import type {
  ChangeRow, Cycle, CycleDetail, Employee, ExceptionRow, ManagerRow, SaveResult, Session,
} from "./types";

let csrfToken = "";

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: BodyInit | undefined;
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
  } catch {
    throw new ApiError("Could not reach the server. Check your connection and try again.", 0);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError((data as { error?: string }).error ?? `Request failed (${response.status}).`, response.status);
  }
  return data as T;
}

const q = (params: Record<string, string | undefined>) => {
  const search = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
  return search.toString() ? `?${search}` : "";
};

export const api = {
  session: () => request<Session>("GET", "/api/session"),
  devLogin: (email: string) => request<{ ok: true }>("POST", "/api/auth/dev-login", { email }),
  logout: () => request<{ redirect: string }>("POST", "/api/auth/logout"),

  cycles: () => request<{ cycles: Cycle[] }>("GET", "/api/cycles"),
  myCycles: () => request<{ cycles: Cycle[] }>("GET", "/api/my-cycles"),
  upload: (file: File, deadline: string) => {
    const form = new FormData();
    form.append("file", file);
    if (deadline) form.append("deadline", deadline);
    return request<{ id: string }>("POST", "/api/cycles", form);
  },
  cycle: (id: string) => request<CycleDetail>("GET", `/api/cycles/${id}`),
  setStatus: (id: string, action: "lock" | "unlock" | "activate") =>
    request<CycleDetail>("POST", `/api/cycles/${id}/status`, { action }),
  setDeadline: (id: string, deadline: string | null) =>
    request<CycleDetail>("PUT", `/api/cycles/${id}/deadline`, { deadline }),

  managers: (id: string) => request<{ managers: ManagerRow[]; departments: string[] }>("GET", `/api/cycles/${id}/managers`),
  exceptions: (id: string) => request<{ exceptions: ExceptionRow[] }>("GET", `/api/cycles/${id}/exceptions`),
  employees: (id: string, filters: { manager?: string; department?: string } = {}) =>
    request<{ employees: Employee[] }>("GET", `/api/cycles/${id}/employees${q(filters)}`),
  save: (id: string, changes: { employee_id: string; date: string; value: string; old_value: string | null }[],
         comment: string | null) => request<SaveResult>("POST", `/api/cycles/${id}/changes`, { changes, comment }),
  changes: (id: string, filters: { employee?: string; changed_by?: string; manager?: string } = {}) =>
    request<{ changes: ChangeRow[] }>("GET", `/api/cycles/${id}/changes${q(filters)}`),
  sendEmails: (id: string, managerEmail?: string) =>
    request<{ queued: number }>("POST", `/api/cycles/${id}/emails`, { manager_email: managerEmail ?? null }),
};

/** Plain links (the browser downloads the file itself). */
export const links = {
  download: (id: string) => `/api/cycles/${id}/download`,
  exceptionsCsv: (id: string) => `/api/cycles/${id}/exceptions.csv`,
  changesCsv: (id: string, filters: { employee?: string; changed_by?: string; manager?: string } = {}) =>
    `/api/cycles/${id}/changes.csv${q(filters)}`,
  signIn: (next: string) => `/auth/login${q({ next })}`,
};
