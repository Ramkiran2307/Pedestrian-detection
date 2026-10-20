import { useEffect, useState } from "react";
import { VideoOff } from "lucide-react";
import { api, timeAgo, type Camera } from "../api";
import { Empty, usePolling } from "../components/ui";

type EdgeStatus = Record<string, number | string | boolean | null>;

const STAT_ROWS: [string, string][] = [
  ["fps", "Processing FPS"],
  ["cache_active", "Actors in cache"],
  ["cache_pushed", "Pushed to incidents"],
  ["cache_binned", "Binned (innocent)"],
  ["incidents", "Litter events"],
  ["uploaded", "Uploaded"],
];

export default function Live() {
  const cams = usePolling(() => api.cameras(), [], 5000);
  const [selected, setSelected] = useState<string>("");
  const [direct, setDirect] = useState<EdgeStatus | null>(null);
  const [streamError, setStreamError] = useState(false);
  const list = cams.data || [];
  const cam: Camera | undefined = list.find((c) => c.id === selected) || list.find((c) => c.online) || list[0];

  useEffect(() => {
    setStreamError(false);
    setDirect(null);
    if (!cam?.preview_url) return;
    const base = cam.preview_url.replace(/\/stream\.mjpg$/, "");
    let stop = false;
    const tick = async () => {
      try {
        const r = await fetch(`${base}/status`, { cache: "no-store" });
        if (!stop && r.ok) setDirect(await r.json());
      } catch {
        if (!stop) setDirect(null);
      }
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => { stop = true; window.clearInterval(id); };
  }, [cam?.id, cam?.preview_url]);

  if (cams.error && !cams.data) return <Empty title="Cannot reach the server">{cams.error}</Empty>;
  const stats: EdgeStatus = direct || cam?.stats || {};

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Live camera</h1>
          <p>Annotated feed straight from the edge device: people (blue), vehicles (green), litter being watched (orange) and offenders (red).</p>
        </div>
        {list.length > 1 && (
          <select className="select" style={{ width: "auto" }} value={cam?.id || ""} onChange={(e) => setSelected(e.target.value)}>
            {list.map((c) => <option key={c.id} value={c.id}>{c.name || c.id}</option>)}
          </select>
        )}
      </div>

      {!cam && <Empty title="No camera has connected yet">Start the edge pipeline with <code>python -m edge.run</code>.</Empty>}
      {cam && (
        <div className="grid grid-detail">
          <section className="card" style={{ padding: 10 }}>
            <div className="live-frame">
              {cam.preview_url && !streamError ? (
                <>
                  <img src={cam.preview_url} alt={`Live view of ${cam.name || cam.id}`} onError={() => setStreamError(true)} />
                  <span className="rec">LIVE · {cam.id}</span>
                </>
              ) : (
                <div style={{ textAlign: "center", padding: 20 }}>
                  <VideoOff size={34} style={{ margin: "0 auto 8px" }} />
                  <div>Live preview unavailable</div>
                  <div style={{ fontSize: 12, marginTop: 4 }}>
                    {cam.online ? "The device is online but its preview server can't be reached from this browser."
                      : `Camera last seen ${timeAgo(cam.last_seen)}.`}
                  </div>
                  {streamError && <button className="btn" style={{ marginTop: 12 }} onClick={() => setStreamError(false)}>Retry</button>}
                </div>
              )}
            </div>
          </section>
          <div className="stack">
            <section className="card">
              <div className="card-head">
                <h2>{cam.name || cam.id}</h2>
                <span className={`badge ${cam.online ? "paid" : "neutral"}`}>{cam.online ? "Online" : "Offline"}</span>
              </div>
              <div className="cell-sub" style={{ padding: "4px 20px 0" }}>{cam.location} · {cam.ward} · {cam.zone} · last heartbeat {timeAgo(cam.last_seen)}</div>
              <div className="stat-list">
                {STAT_ROWS.map(([k, label]) => (
                  <div key={k}><div className="k">{label}</div><div className="v">{stats[k] ?? "—"}</div></div>
                ))}
              </div>
              {stats.warming_up === true && <div className="callout warn" style={{ margin: "0 20px 16px" }}>Learning the empty background…</div>}
              {typeof stats.upload_error === "string" && stats.upload_error && (
                <div className="callout bad" style={{ margin: "0 20px 16px" }}>Upload problem: {stats.upload_error}</div>
              )}
            </section>
            <section className="card card-pad">
              <h2 style={{ fontSize: 15, marginBottom: 6 }}>Reading the cache numbers</h2>
              <p className="sub" style={{ margin: 0 }}>
                Every detected person and vehicle is held in memory for a few seconds after it leaves the frame.
                If litter appears next to it in that window it is <b>pushed</b> into an incident with its photos.
                Otherwise it is <b>binned</b>: deleted without anything being saved.
              </p>
            </section>
          </div>
        </div>
      )}
    </>
  );
}
