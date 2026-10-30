import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useToast } from "../components/toast";
import { Alert, Card, PageHeader, Spinner } from "../components/ui";
import { useRefreshSession } from "../session";

const MIN_LENGTH = 8;

export function ChangePasswordPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const refresh = useRefreshSession();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (next.length < MIN_LENGTH) return setError(`The new password must have at least ${MIN_LENGTH} characters.`);
    if (next !== repeat) return setError("The two new passwords are not the same.");
    setBusy(true);
    try {
      await api.changePassword(current, next);
      await refresh(); // the session was renewed, with a new CSRF token
      toast("Password changed. Use the new one next time you sign in.");
      navigate("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Change password" subtitle="Your login ID stays your work email." />
      <div className="narrow">
        <Card>
          <form className="stack" onSubmit={submit}>
            {error && <Alert tone="error">{error}</Alert>}
            <label className="field">
              <span>Current password</span>
              <input className="input" type="password" required autoComplete="current-password" value={current}
                     onChange={(e) => setCurrent(e.target.value)} />
            </label>
            <label className="field">
              <span>New password</span>
              <input className="input" type="password" required autoComplete="new-password" value={next}
                     onChange={(e) => setNext(e.target.value)} />
              <small className="muted">At least {MIN_LENGTH} characters.</small>
            </label>
            <label className="field">
              <span>New password again</span>
              <input className="input" type="password" required autoComplete="new-password" value={repeat}
                     onChange={(e) => setRepeat(e.target.value)} />
            </label>
            <div className="row end">
              <button type="button" className="btn ghost" onClick={() => navigate(-1)}>Cancel</button>
              <button className="btn primary" disabled={busy}>{busy && <Spinner />}Change password</button>
            </div>
          </form>
        </Card>
      </div>
    </>
  );
}
