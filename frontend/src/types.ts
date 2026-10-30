// Shapes of the JSON the API returns (see app/views.py and app/routes/*.py).

export interface User {
  email: string;
  name: string;
  is_hr: boolean;
}

export interface Session {
  user: User | null;
  csrf_token: string;
  auth_mode: "dev" | "password" | "entra";
  statuses: string[];
  timezone: string;
  max_upload_mb: number;
}

export interface CycleStats {
  employees?: number;
  managers?: number;
  exceptions?: number;
  filled_from_sap?: number;
}

export interface Cycle {
  id: string;
  name: string;
  start_date: string | null;
  end_date: string | null;
  status: "open" | "locked";
  active: boolean;
  correction_deadline: string | null;
  deadline_date: string | null;
  uploaded_at: string | null;
  uploaded_by: string | null;
  original_filename: string | null;
  stats: CycleStats;
}

/** A cycle opened by someone with access to it: includes their role and what they may do. */
export interface CycleDetail extends Cycle {
  role: "hr" | "manager";
  read_only_reason: string | null;
  can_edit: boolean;
  dates: string[];
}

export type DayMap = Record<string, string | null>;

export interface Employee {
  employee_id: string;
  employee_name: string;
  department: string | null;
  emp_status: string | null;
  lwd: string | null;
  days: DayMap;
  original_days: DayMap;
  calculated_days: DayMap;
  schedule: Record<string, string>;
  worked_seconds: Record<string, number>;
  manager_name?: string | null;
  manager_email?: string | null;
}

export interface ManagerRow {
  manager_email: string;
  manager_name: string;
  team_size: number;
  changes: number;
  email_status: "sent" | "failed" | null;
  email_error: string | null;
  email_sent_at: string | null;
}

export interface ExceptionRow {
  type: string;
  severity: "error" | "warning" | "info";
  message: string;
  employee_id: string | null;
  employee_name: string | null;
  date: string | null;
}

export interface ChangeRow {
  changed_at: string;
  employee_id: string;
  employee_name: string;
  manager_email: string | null;
  date: string;
  old_value: string | null;
  new_value: string;
  changed_by_email: string;
  changed_by_role: "hr" | "manager";
  comment: string | null;
}

export interface SaveResult {
  saved: number;
  unchanged: number;
  conflicts: { employee_id: string; date: string; current_value: string | null }[];
}

export interface Person {
  email: string;
  name: string;
  role: "hr" | "manager";
  has_login: boolean;
  active: boolean;
  locked: boolean;
  last_login_at: string | null;
  password_changed_at: string | null;
}

/** A login HR just created or reset: the password is shown once. */
export interface NewLogin {
  email: string;
  name: string;
  password: string;
}