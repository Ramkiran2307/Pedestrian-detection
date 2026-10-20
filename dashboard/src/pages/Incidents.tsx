import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Camera, ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import { api, fmtDateTime, inr, offenderLabel, STATUS_LABEL, type Status } from "../api";
import { CleanupChip, Empty, OverdueBadge, Plate, SampleBadge, StatusBadge, useApp, usePolling } from "../components/ui";

const TYPES = [
  { v: "", label: "All" },
  { v: "person", label: "Pedestrian" },
  { v: "vehicle", label: "Vehicle" },
  { v: "unknown", label: "Unattributed" },
];

export default function Incidents() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { setPendingCount } = useApp();
  const [q, setQ] = useState(params.get("q") || "");
  const page = Number(params.get("page") || 1);
  const filters = {
    status: params.get("status") || "",
    type: params.get("type") || "",
    cleanup: params.get("cleanup") || "",
    overdue: params.get("overdue") || "",
    from: params.get("from") || "",
    to: params.get("to") || "",
    q: params.get("q") || "",
  };
  const key = JSON.stringify({ ...filters, page });
  const list = usePolling(() => api.incidents({ ...filters, page, page_size: 15 }), [key], 6000);
  const pending = usePolling(() => api.incidents({ status: "pending_review", page_size: 1 }), [], 10000);

  useEffect(() => {
    if (pending.data) setPendingCount(pending.data.total);
  }, [pending.data, setPendingCount]);

  // debounce the search box into the URL
  useEffect(() => {
    const id = window.setTimeout(() => {
      if (q !== (params.get("q") || "")) update({ q });
    }, 350);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  function update(next: Record<string, string>) {
    const p = new URLSearchParams(params);
    Object.entries(next).forEach(([k, v]) => (v ? p.set(k, v) : p.delete(k)));
    if (!("page" in next)) p.delete("page");
    setParams(p, { replace: true });
  }

  const data = list.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const anyFilter = Object.values(filters).some(Boolean);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Offence register</h1>
          <p>Every detection with its evidence, review state and cleanup status.</p>
        </div>
      </div>

      <section className="card">
        <div className="toolbar">
          <div className="search">
            <Search aria-hidden />
            <input className="input" value={q} onChange={(e) => setQ(e.target.value)}
                   placeholder="Search plate, notice no. or #id" aria-label="Search" />
          </div>
          <div className="segmented" role="group" aria-label="Offender type">
            {TYPES.map((t) => (
              <button key={t.v} className={filters.type === t.v ? "on" : ""} onClick={() => update({ type: t.v })}>{t.label}</button>
            ))}
          </div>
          <select className="select" value={filters.status} onChange={(e) => update({ status: e.target.value, overdue: "" })} aria-label="Status">
            <option value="">Any status</option>
            {(Object.keys(STATUS_LABEL) as Status[]).map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
          </select>
          <select className="select" value={filters.cleanup} onChange={(e) => update({ cleanup: e.target.value })} aria-label="Cleanup">
            <option value="">Any cleanup</option>
            <option value="pending">Cleanup pending</option>
            <option value="assigned">Cleanup assigned</option>
            <option value="cleaned">Cleaned</option>
          </select>
          <input type="date" className="input" style={{ width: "auto" }} value={filters.from} aria-label="From date"
                 onChange={(e) => update({ from: e.target.value })} />
          <input type="date" className="input" style={{ width: "auto" }} value={filters.to} aria-label="To date"
                 onChange={(e) => update({ to: e.target.value })} />
          {filters.overdue && <span className="badge overdue">Overdue only</span>}
          {anyFilter && (
            <button className="btn ghost" onClick={() => { setQ(""); setParams({}, { replace: true }); }}>
              <X /> Clear
            </button>
          )}
        </div>

        {list.error && !data && <Empty title="Cannot load incidents">{list.error}</Empty>}
        {data && data.items.length === 0 && (
          <Empty title={anyFilter ? "Nothing matches these filters" : "No incidents recorded yet"}>
            {anyFilter ? "Try clearing a filter." : "Detections appear here as soon as an edge camera uploads them."}
          </Empty>
        )}
        {data && data.items.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Evidence</th><th>Case</th><th>Offender</th><th>Plate</th><th>Location</th>
                  <th>Status</th><th>Cleanup</th><th className="num">Fine</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((i) => (
                  <tr className="row" key={i.id} onClick={() => navigate(`/incidents/${i.id}`)}>
                    <td style={{ width: 88 }}>
                      {i.thumbnail ? <img className="thumb" src={i.thumbnail} alt="" loading="lazy" />
                        : <div className="thumb empty"><Camera size={16} /></div>}
                    </td>
                    <td>
                      <div className="cell-main">#{i.id} {i.is_sample && <SampleBadge />}</div>
                      <div className="cell-sub">{fmtDateTime(i.occurred_at)}</div>
                    </td>
                    <td>
                      <div className="cell-main">{offenderLabel(i)}</div>
                      <div className="cell-sub">
                        {i.attribution_score ? `Match ${Math.round(i.attribution_score * 100)}%` : "No suspect nearby"}
                      </div>
                    </td>
                    <td>{i.offender_type === "vehicle" ? <Plate text={i.plate_display} /> : <span className="muted">—</span>}</td>
                    <td>
                      <div>{i.location || "—"}</div>
                      <div className="cell-sub">{i.ward || ""} {i.camera_id ? `· ${i.camera_id}` : ""}</div>
                    </td>
                    <td><div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}><StatusBadge status={i.status} />{i.overdue && <OverdueBadge />}</div></td>
                    <td><CleanupChip status={i.cleanup_status} /></td>
                    <td className="num">{inr(i.fine_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total > 0 && (
          <div className="pager">
            <span>{data.total} case{data.total === 1 ? "" : "s"} · page {data.page} of {pages}</span>
            <div className="btns">
              <button className="btn" disabled={page <= 1} onClick={() => update({ page: String(page - 1) })}><ChevronLeft /> Prev</button>
              <button className="btn" disabled={page >= pages} onClick={() => update({ page: String(page + 1) })}>Next <ChevronRight /></button>
            </div>
          </div>
        )}
      </section>
    </>
  );
}
