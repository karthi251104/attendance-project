import { Mail } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { api, links } from "../api";
import { Alert, Spinner } from "../components/ui";
import { useRefreshSession, useSession } from "../session";

function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

export function LoginPage() {
  const session = useSession();
  const refresh = useRefreshSession();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [error, setError] = useState(params.get("error"));
  const [busy, setBusy] = useState(false);

  if (session.user) return <Navigate to={next} replace />;

  async function devSignIn(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.devLogin(email);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <aside className="login-hero">
        <div className="brand"><img src="/favicon.svg" alt="" /><span>Attendance Portal</span></div>
        <div>
          <h1>Monthly attendance, reviewed by the people who know their teams.</h1>
          <p>HR uploads the SAP file once. Every manager checks their own team's days, and HR downloads the finished workbook.</p>
          <div className="login-steps">
            <div><span>1</span>HR uploads the SAP export with SAP and HC_Teamlease.</div>
            <div><span>2</span>The portal builds TO CONSIDER and marks every day P, A, OFF or PH.</div>
            <div><span>3</span>Managers correct their team; HR downloads the updated Excel.</div>
          </div>
        </div>
        <small className="hero-foot">For company use only. Sign in with your work account.</small>
      </aside>

      <main className="login-panel">
        <div className="login-card">
          <h2>Sign in</h2>
          <p className="muted">Use your company Microsoft account.</p>
          <div className="stack">
            {error && <Alert tone="error">{error}</Alert>}
            {session.auth_mode === "entra" ? (
              <a className="btn lg block" href={links.signIn(next)}>
                <span className="ms-logo" aria-hidden><i /><i /><i /><i /></span>
                Sign in with Microsoft
              </a>
            ) : (
              <form className="stack" onSubmit={devSignIn}>
                <Alert tone="warn">Development sign-in: any email works, no password. This is never available in production.</Alert>
                <label className="field">
                  <span>Work email</span>
                  <div className="input-icon">
                    <Mail aria-hidden />
                    <input className="input" type="email" required autoFocus value={email} placeholder="name@company.com"
                           onChange={(e) => setEmail(e.target.value)} />
                  </div>
                </label>
                <button className="btn primary lg block" disabled={busy}>{busy && <Spinner />}Sign in</button>
              </form>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
