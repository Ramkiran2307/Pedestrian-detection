// Development-phase tool: upload a video, let the edge pipeline process it on the
// server machine, and refresh the dashboard as soon as the incidents arrive.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, CircleX, FlaskConical, Loader2, MonitorPlay, Square, Upload } from "lucide-react";
import { api, ApiError, type DevJob } from "../api";
import { useApp } from "./ui";

const ACTIVE = new Set(["queued", "running"]);

function duration(job: DevJob): string {
  if (!job.started) return "";
  const s = Math.round((job.finished ?? Date.now() / 1000) - job.started);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function DevUpload({ onFinished }: { onFinished: () => void }) {
  const { notify } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [fullFrame, setFullFrame] = useState(true);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [jobs, setJobs] = useState<DevJob[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const seen = useRef<Record<string, string>>({});

  const anyActive = jobs.some((j) => ACTIVE.has(j.status));

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const list = await api.devJobs();
        if (stop) return;
        // A job that just finished: refresh the whole dashboard and say what was found.
        for (const j of list) {
          const before = seen.current[j.id];
          if (before && ACTIVE.has(before) && j.status === "done") {
            notify(`Finished “${j.filename}”: ${j.incidents} incident${j.incidents === 1 ? "" : "s"} found`);
            onFinished();
          } else if (before && ACTIVE.has(before) && j.status === "failed") {
            notify(`Processing “${j.filename}” failed`, "error");
          }
          seen.current[j.id] = j.status;
        }
        setJobs(list);
        // While a video is processing, incidents are uploaded as they are found - refresh too.
        if (list.some((j) => j.status === "running" && j.uploaded > 0)) onFinished();
      } catch {
        /* server restarting; try again next tick */
      }
    };
    load();
    const id = window.setInterval(load, anyActive ? 1500 : 6000);
    return () => { stop = true; window.clearInterval(id); };
  }, [anyActive, notify, onFinished]);

  async function submit() {
    if (!file) return;
    setUploadPct(0);
    try {
      const job = await api.uploadVideo(file, fullFrame, setUploadPct);
      seen.current[job.id] = job.status;
      setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
      setFile(null);
      if (input.current) input.current.value = "";
      notify("Video uploaded - processing started");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Upload failed", "error");
    } finally {
      setUploadPct(null);
    }
  }

  async function cancel(id: string) {
    try {
      await api.cancelDevJob(id);
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Could not cancel", "error");
    }
  }

  return (
    <section className="card dev-card" style={{ marginTop: 16 }} aria-labelledby="dev-title">
      <div className="card-head">
        <h2 id="dev-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <FlaskConical size={17} aria-hidden /> Test with your own footage
          <span className="badge neutral">Development</span>
        </h2>
        <span className="hint">Runs the edge pipeline on this machine as camera DEV-UPLOAD</span>
      </div>

      <div className="dev-body">
        <label
          className={`dropzone ${drag ? "drag" : ""} ${file ? "has-file" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            const f = e.dataTransfer.files?.[0];
            if (f) setFile(f);
          }}
        >
          <input ref={input} type="file" accept="video/*,.mp4,.avi,.mov,.mkv,.webm"
                 onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <Upload aria-hidden />
          {file ? (
            <span><b>{file.name}</b> · {(file.size / 1024 / 1024).toFixed(1)} MB</span>
          ) : (
            <span><b>Choose a video</b> or drop it here (MP4, AVI, MOV, MKV, WebM)</span>
          )}
        </label>

        <div className="dev-controls">
          <label className="check">
            <input type="checkbox" checked={fullFrame} onChange={(e) => setFullFrame(e.target.checked)} />
            <span>Treat the whole frame as the clean zone
              <span className="cell-sub" style={{ display: "block" }}>Untick to use the zone drawn in edge/config.yaml</span>
            </span>
          </label>
          <button className="btn primary" disabled={!file || uploadPct !== null} onClick={submit}>
            {uploadPct !== null ? <><Loader2 className="spin" /> Uploading {uploadPct}%</> : <><Upload /> Upload &amp; process</>}
          </button>
        </div>
      </div>

      {jobs.length > 0 && (
        <ul className="job-list">
          {jobs.map((j) => (
            <li key={j.id} className={`job ${j.status}`}>
              <div className="job-icon" aria-hidden>
                {ACTIVE.has(j.status) && <Loader2 className="spin" />}
                {j.status === "done" && <CheckCircle2 />}
                {(j.status === "failed" || j.status === "cancelled") && <CircleX />}
              </div>
              <div className="job-main">
                <div className="job-title">
                  <b>{j.filename}</b>
                  <span className="muted">
                    {j.status === "queued" && "Waiting…"}
                    {j.status === "running" && (j.total_frames
                      ? `Processing frame ${j.frames} / ${j.total_frames}${j.fps ? ` · ${j.fps} fps` : ""}`
                      : "Loading models…")}
                    {j.status === "done" && `Done in ${duration(j)}`}
                    {j.status === "failed" && "Failed"}
                    {j.status === "cancelled" && "Cancelled"}
                  </span>
                </div>
                {ACTIVE.has(j.status) && (
                  <div className="track" role="progressbar" aria-valuenow={j.percent ?? 0} aria-valuemin={0} aria-valuemax={100}>
                    <div className="fill" style={{ width: `${j.percent ?? 2}%` }} />
                  </div>
                )}
                <div className="cell-sub">
                  {j.incidents} incident{j.incidents === 1 ? "" : "s"} found · {j.binned} passers-by binned
                  {j.status === "running" && j.uploaded < j.incidents && " · preparing evidence…"}
                </div>
                {j.status === "failed" && (
                  <details className="job-error" open>
                    <summary>{j.error || "The pipeline stopped with an error"}</summary>
                    {j.log_tail.length > 0 && <pre>{j.log_tail.join("\n")}</pre>}
                  </details>
                )}
              </div>
              <div className="job-actions">
                {j.status === "running" && <Link to="/live" className="btn ghost"><MonitorPlay /> Watch</Link>}
                {ACTIVE.has(j.status) && <button className="btn ghost" onClick={() => cancel(j.id)}><Square /> Stop</button>}
                {j.status === "done" && j.incidents > 0 && (
                  <Link to="/incidents?camera=DEV-UPLOAD" className="btn">View incidents</Link>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
