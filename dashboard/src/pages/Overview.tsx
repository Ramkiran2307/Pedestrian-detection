import { useCallback, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, Camera, Clock3, IndianRupee, Recycle } from "lucide-react";
import { api, compactInr, fmtDateTime, inr, offenderLabel, STATUS_LABEL, timeAgo, VEHICLE_LABEL, type Status } from "../api";
import { BarList, HourlyChart, TrendChart } from "../components/charts";
import DevUpload from "../components/DevUpload";
import { Empty, Kpi, Plate, StatusBadge, useApp, usePolling } from "../components/ui";

const PIPELINE: Status[] = ["pending_review", "verified", "notice_issued", "escalated", "paid", "rejected"];

export default function Overview() {
  const { setPendingCount, settings } = useApp();
  const stats = usePolling(() => api.stats(), [], 5000);
  const recent = usePolling(() => api.incidents({ page_size: 6 }), [], 5000);
  const cams = usePolling(() => api.cameras(), [], 10000);
  const navigate = useNavigate();
  const s = stats.data;
  const { reload: reloadStats } = stats;
  const { reload: reloadRecent } = recent;
  const { reload: reloadCams } = cams;
  const refreshAll = useCallback(() => {
    reloadStats();
    reloadRecent();
    reloadCams();
  }, [reloadStats, reloadRecent, reloadCams]);

  useEffect(() => {
    if (s) setPendingCount(s.totals.pending_review);
  }, [s, setPendingCount]);

  if (stats.error && !s) return <Empty title="Cannot reach the server">{stats.error}. Is <code>python -m server.app</code> running?</Empty>;
  if (!s) return <div className="muted">Loading…</div>;

  const vehicleRows = Object.entries(s.by_vehicle)
    .map(([k, v]) => ({ label: VEHICLE_LABEL[k] || k, value: v }))
    .sort((a, b) => b.value - a.value);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Command centre</h1>
          <p>Littering detected by edge cameras in the clean zones, and the enforcement on each case.</p>
        </div>
        <span className="live-dot">Live · refreshes every 5 s</span>
      </div>

      <div className="grid grid-kpi">
        <Kpi hero label="Incidents today" value={s.totals.today}
             foot={`${s.totals.all} recorded in total · ${s.by_type.person} pedestrian, ${s.by_type.vehicle} vehicle`} />
        <Link to="/incidents?status=pending_review" className="kpi-link">
          <Kpi label="Awaiting review" icon={<Clock3 size={15} />} value={s.totals.pending_review}
               foot="Open the register to verify evidence" />
        </Link>
        <Link to="/incidents?overdue=1" className="kpi-link">
          <Kpi label="Overdue notices" icon={<AlertTriangle size={15} />} value={s.totals.overdue}
               foot={`${s.fines.notices} notices issued`} />
        </Link>
        <Kpi label="Fines collected" icon={<IndianRupee size={15} />} value={compactInr(s.fines.collected)}
             foot={`of ${inr(s.fines.issued)} levied`} />
        <Link to="/incidents?cleanup=pending" className="kpi-link">
          <Kpi label="Cleanup pending" icon={<Recycle size={15} />} value={s.totals.cleanup_pending}
               foot={s.review_accuracy !== null ? `Detections confirmed by officers: ${Math.round(s.review_accuracy * 100)}%` : "No reviews yet"} />
        </Link>
      </div>

      <div className="grid grid-2" style={{ marginTop: 16 }}>
        <section className="card">
          <div className="card-head"><h2>Incidents per day</h2><span className="hint">Last 14 days · by offender type</span></div>
          <TrendChart data={s.daily} />
        </section>
        <section className="card">
          <div className="card-head"><h2>Time of day</h2><span className="hint">All incidents, IST</span></div>
          <HourlyChart data={s.hourly} />
        </section>
      </div>

      <section className="card" style={{ marginTop: 16 }}>
        <div className="card-head"><h2>Enforcement pipeline</h2><span className="hint">Cases at each stage</span></div>
        <div className="pipeline">
          {PIPELINE.map((st) => (
            <Link to={`/incidents?status=${st}`} className="step" key={st} title={`Show ${STATUS_LABEL[st]}`}>
              <StatusBadge status={st} />
              <div className="n tabular">{s.by_status[st]}</div>
            </Link>
          ))}
        </div>
      </section>

      <div className="grid grid-2" style={{ marginTop: 16 }}>
        <section className="card">
          <div className="card-head">
            <h2>Latest detections</h2>
            <Link to="/incidents" className="btn ghost">Offence register <ArrowRight /></Link>
          </div>
          {recent.data && recent.data.items.length ? (
            <div className="table-wrap" style={{ marginTop: 10 }}>
              <table className="data">
                <tbody>
                  {recent.data.items.map((i) => (
                    <tr className="row" key={i.id} onClick={() => navigate(`/incidents/${i.id}`)}>
                      <td style={{ width: 84 }}>
                        {i.thumbnail ? <img className="thumb" src={i.thumbnail} alt="" loading="lazy" /> : <div className="thumb empty"><Camera size={16} /></div>}
                      </td>
                      <td>
                        <div className="cell-main">#{i.id} · {offenderLabel(i)}</div>
                        <div className="cell-sub">{fmtDateTime(i.occurred_at)} · {i.location || i.camera_id}</div>
                      </td>
                      <td>{i.offender_type === "vehicle" ? <Plate text={i.plate_display} /> : <span className="muted">—</span>}</td>
                      <td><StatusBadge status={i.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No detections yet">Run the edge pipeline: <code>python -m edge.run</code></Empty>
          )}
        </section>

        <div className="stack">
          <section className="card">
            <div className="card-head"><h2>Repeat offenders</h2><span className="hint">Same plate, 2+ cases</span></div>
            {s.repeat_offenders.length ? (
              <div className="table-wrap" style={{ marginTop: 10 }}>
              <table className="data">
                <thead><tr><th>Plate</th><th className="num">Cases</th><th className="num">Fines</th><th>Last seen</th></tr></thead>
                <tbody>
                  {s.repeat_offenders.map((r) => (
                    <tr className="row" key={r.plate} onClick={() => navigate(`/incidents?q=${r.plate}`)}>
                      <td><Plate text={r.display} /></td>
                      <td className="num">{r.count}</td>
                      <td className="num">{inr(r.fines)}</td>
                      <td className="cell-sub">{timeAgo(r.last_seen)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            ) : <Empty title="No repeat offenders" />}
          </section>
          <section className="card">
            <div className="card-head"><h2>Vehicle offences by type</h2></div>
            {vehicleRows.length ? <BarList rows={vehicleRows} /> : <Empty title="No vehicle cases yet" />}
          </section>
          <section className="card">
            <div className="card-head"><h2>Cameras</h2><Link to="/live" className="btn ghost">Live <ArrowRight /></Link></div>
            <div style={{ padding: "10px 20px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
              {(cams.data || []).map((c) => (
                <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span className={`badge ${c.online ? "paid" : "neutral"}`}>{c.online ? "Online" : "Offline"}</span>
                  <div style={{ minWidth: 0 }}>
                    <div className="cell-main">{c.name || c.id}</div>
                    <div className="cell-sub">{c.id} · {c.ward || "—"} · seen {timeAgo(c.last_seen)}</div>
                  </div>
                  <span className="muted tabular" style={{ marginLeft: "auto" }}>{c.incidents}</span>
                </div>
              ))}
              {cams.data && !cams.data.length && <span className="muted">No camera has connected yet.</span>}
            </div>
          </section>
        </div>
      </div>
      {settings?.dev_tools && <DevUpload onFinished={refreshAll} />}
    </>
  );
}
