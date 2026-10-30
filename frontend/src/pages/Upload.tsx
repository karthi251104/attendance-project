import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileSpreadsheet, UploadCloud, X } from "lucide-react";
import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Alert, Card, PageHeader, Spinner } from "../components/ui";
import { useToast } from "../components/toast";
import { useSession } from "../session";

const ACCEPTED = [".xlsm", ".xlsx"];

export function UploadPage() {
  const { max_upload_mb } = useSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [deadline, setDeadline] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function choose(chosen: File | undefined) {
    setError(null);
    if (!chosen) return;
    if (!ACCEPTED.some((ext) => chosen.name.toLowerCase().endsWith(ext))) {
      setError("Please choose an Excel workbook (.xlsm or .xlsx).");
      return;
    }
    if (chosen.size > max_upload_mb * 1024 * 1024) {
      setError(`The file is larger than ${max_upload_mb} MB.`);
      return;
    }
    setFile(chosen);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    choose(event.dataTransfer.files[0]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.upload(file, deadline);
      await queryClient.invalidateQueries({ queryKey: ["cycles"] });
      toast("Workbook processed. TO CONSIDER is ready for review.");
      navigate(`/hr/cycles/${id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        back={<Link className="breadcrumb" to="/hr"><ArrowLeft size={16} />Cycles</Link>}
        title="Upload attendance workbook"
        subtitle="The file straight from SAP, before running the macro."
      />
      <div className="stack">
        <div className="flow">
          <div className="flow-step"><b>1. SAP sheet</b><span className="muted">One row per employee per day: schedule and worked hours.</span></div>
          <div className="flow-step"><b>2. HC_Teamlease sheet</b><span className="muted">Joining date, status, department and reporting manager.</span></div>
          <div className="flow-step"><b>3. Portal builds TO CONSIDER</b><span className="muted">P / A / OFF / PH for every day, plus the totals.</span></div>
        </div>

        <Card>
          <form className="stack" onSubmit={submit}>
            {error && <Alert tone="error">{error}</Alert>}
            {file ? (
              <div className="file-pill">
                <FileSpreadsheet aria-hidden />
                <div>
                  <strong>{file.name}</strong>
                  <div className="muted small">{(file.size / 1024 / 1024).toFixed(1)} MB</div>
                </div>
                <span className="spacer" />
                <button type="button" className="btn ghost sm" onClick={() => setFile(null)} disabled={busy}><X />Remove</button>
              </div>
            ) : (
              <div
                className={`dropzone${over ? " over" : ""}`}
                role="button" tabIndex={0}
                onClick={() => input.current?.click()}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
                onDragOver={(e) => { e.preventDefault(); setOver(true); }}
                onDragLeave={() => setOver(false)}
                onDrop={onDrop}
              >
                <UploadCloud aria-hidden />
                <h3>Drop the workbook here, or click to choose</h3>
                <p className="muted">.xlsm or .xlsx, up to {max_upload_mb} MB. It must have the SAP and HC_Teamlease sheets and no TO CONSIDER sheet.</p>
                <input ref={input} type="file" accept={ACCEPTED.join(",")} hidden onChange={(e) => choose(e.target.files?.[0])} />
              </div>
            )}

            <label className="field">
              <span>Correction deadline (optional)</span>
              <input className="input" type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
              <small className="muted">Managers can edit until 23:59 on this day. You can change it later.</small>
            </label>

            <div className="row">
              <span className="spacer" />
              <Link className="btn ghost" to="/hr">Cancel</Link>
              <button className="btn primary" disabled={!file || busy}>
                {busy ? <><Spinner />Processing… this can take a minute</> : <><UploadCloud />Upload and process</>}
              </button>
            </div>
          </form>
        </Card>
      </div>
    </>
  );
}
