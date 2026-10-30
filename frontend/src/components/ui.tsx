// Small building blocks used by every page.

import { AlertCircle, AlertTriangle, CheckCircle2, Info, Inbox } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

type Tone = "info" | "warn" | "error" | "success";
const ALERT_ICONS = { info: Info, warn: AlertTriangle, error: AlertCircle, success: CheckCircle2 };

export function Alert({ tone, children }: { tone: Tone; children: ReactNode }) {
  const Icon = ALERT_ICONS[tone];
  return (
    <div className={`alert ${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function Badge({ tone = "slate", icon, children, title }: {
  tone?: string; icon?: ReactNode; children: ReactNode; title?: string;
}) {
  return <span className={`badge tone-${tone}`} title={title}>{icon}{children}</span>;
}

export function Card({ title, subtitle, actions, children, flush = false }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-header">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      <div className={`card-body${flush ? " flush" : ""}`}>{children}</div>
    </section>
  );
}

export function Stat({ icon, tone, value, label }: { icon: ReactNode; tone: string; value: ReactNode; label: string }) {
  return (
    <div className="card stat">
      <div className={`stat-icon tone-${tone}`} aria-hidden>{icon}</div>
      <div>
        <div className="stat-value">{value}</div>
        <div className="stat-label">{label}</div>
      </div>
    </div>
  );
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      {icon ?? <Inbox aria-hidden />}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="loading"><span className="spinner" aria-hidden />{label}</div>;
}

export function Spinner() {
  return <span className="spinner" aria-hidden />;
}

export function PageHeader({ title, subtitle, actions, back }: {
  title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; back?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        {back}
        <h1>{title}</h1>
        {subtitle && <div className="subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

/** A confirm dialog. Escape or clicking outside cancels. */
export function ConfirmDialog({ title, children, confirmLabel, danger = false, busy = false, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="card modal" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
        <h2 id="dialog-title">{title}</h2>
        <div>{children}</div>
        <div className="row">
          <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="button" ref={confirmRef} className={`btn ${danger ? "danger" : "primary"}`} onClick={onConfirm} disabled={busy}>
            {busy && <Spinner />}{confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
