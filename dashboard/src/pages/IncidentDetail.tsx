import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle, ArrowLeft, BadgeCheck, Ban, CheckCircle2, FileText, Film, Info, Pencil, RotateCcw, Sparkles,
  StickyNote, Truck,
} from "lucide-react";
import {
  api, ApiError, cap, CLEANUP_LABEL, fmtDate, fmtDateTime, inr, offenderLabel, STATUS_LABEL, VEHICLE_LABEL,
  type IncidentDetail as Detail, type Status,
} from "../api";
import { CleanupChip, Empty, Modal, OverdueBadge, Plate, SampleBadge, StatusBadge, useApp, usePolling } from "../components/ui";

const EVIDENCE_ORDER = [
  ["clip", "Video clip"], ["scene", "Scene"], ["person", "Person"], ["vehicle", "Vehicle"],
  ["plate", "Number plate"], ["litter", "Litter"],
] as const;

const ACTION_TEXT: Record<string, string> = {
  detected: "Detected by camera", verify: "Evidence verified", reject: "Rejected", issue_notice: "Notice issued",
  mark_paid: "Fine paid", escalate: "Escalated", reopen: "Reopened for review", assign_cleanup: "Cleanup assigned",
  mark_cleaned: "Litter cleaned", correct_plate: "Plate corrected", add_note: "Note added",
};

type Dialog = null | "reject" | "notice" | "paid" | "plate" | "cleanup" | "note";

export default function IncidentDetail() {
  const { id } = useParams();
  const { officer, notify, settings } = useApp();
  const res = usePolling(() => api.incident(id!), [id], 8000);
  const [view, setView] = useState<string>("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const d = res.data;

  useEffect(() => {
    if (d && !view) setView(d.evidence.clip ? "clip" : d.evidence.scene ? "scene" : Object.keys(d.evidence)[0] || "");
  }, [d, view]);

  if (res.error && !d) return <Empty title="Incident not found">{res.error}</Empty>;
  if (!d) return <div className="muted">Loading…</div>;

  async function act(action: string, extra: Record<string, unknown> = {}) {
    if (!officer.trim()) {
      notify("Enter your name in the top bar first - it is recorded in the audit trail.", "error");
      return;
    }
    setBusy(true);
    try {
      const updated = await api.act(d!.id, { action, actor: officer.trim(), ...extra });
      res.setData(updated);
      setDialog(null);
      setForm({});
      notify(ACTION_TEXT[action] || "Saved");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Action failed", "error");
    } finally {
      setBusy(false);
    }
  }

  const src = view ? d.evidence[view as keyof Detail["evidence"]] : undefined;
  const isVehicle = d.offender_type === "vehicle";

  return (
    <>
      <Link to="/incidents" className="back"><ArrowLeft size={16} /> Offence register</Link>
      <div className="page-head">
        <div>
          <h1 style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            Case #{d.id} · {offenderLabel(d)} littering
            {d.is_sample && <SampleBadge />}
          </h1>
          <p>{fmtDateTime(d.occurred_at)} · {d.location || d.camera_id} · {d.zone}</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <StatusBadge status={d.status} />
          {d.overdue && <OverdueBadge />}
          <CleanupChip status={d.cleanup_status} />
        </div>
      </div>

      <div className="grid grid-detail">
        <div className="stack">
          <section className="card">
            <div className="evidence-main">
              {!src && <div style={{ color: "#aaa", display: "grid", placeItems: "center", height: "100%" }}>No evidence files</div>}
              {src && view === "clip" && <video key={src} src={src} controls autoPlay muted loop playsInline />}
              {src && view !== "clip" && <img src={src} alt={`${view} evidence`} />}
            </div>
            <div className="evidence-tabs" role="tablist" aria-label="Evidence">
              {EVIDENCE_ORDER.filter(([k]) => d.evidence[k]).map(([k, label]) => (
                <button key={k} role="tab" aria-selected={view === k} className={view === k ? "on" : ""} onClick={() => setView(k)}>
                  {k === "clip" ? <div className="vid"><Film size={20} /></div> : <img src={d.evidence[k]} alt="" />}
                  <span>{label}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="card">
            <div className="section-title">Detection details</div>
            <div className="facts">
              <div><div className="k">Offender</div><div className="v">{offenderLabel(d)}{d.offender_track ? ` (track ${d.offender_track})` : ""}</div></div>
              <div><div className="k">Attribution confidence</div><div className="v">{d.attribution_score ? `${Math.round(d.attribution_score * 100)}%` : "No suspect"}</div></div>
              {isVehicle && <>
                <div><div className="k">Vehicle category</div><div className="v">{VEHICLE_LABEL[d.vehicle_category || "other"] || cap(d.vehicle_category || "")}</div></div>
                <div><div className="k">Plate OCR confidence</div><div className="v">{d.plate_confidence ? `${Math.round(d.plate_confidence * 100)}%` : "—"}{d.plate_corrected ? " · corrected by officer" : ""}</div></div>
              </>}
              {d.linked_track && <div><div className="k">Linked actor</div><div className="v">{d.linked_track} {d.offender_type === "person" ? "(vehicle ridden)" : "(rider / driver)"}</div></div>}
              <div><div className="k">Camera</div><div className="v">{d.camera?.name || d.camera_id}</div></div>
              <div><div className="k">Ward</div><div className="v">{d.ward || "—"}</div></div>
              <div><div className="k">Uploaded</div><div className="v">{fmtDateTime(d.received_at)}</div></div>
              <div className="span2"><div className="k">Case ID</div><div className="v mono" style={{ fontSize: 12 }}>{d.uid}</div></div>
              {d.suspects && d.suspects.length > 1 && (
                <div className="span2"><div className="k">Other people / vehicles nearby</div>
                  <div className="v" style={{ fontWeight: 500 }}>
                    {d.suspects.slice(1).map((s) => `${s.track} ${s.class} (${Math.round(s.score * 100)}%)`).join(", ")}
                  </div></div>
              )}
            </div>
          </section>

          <section className="card">
            <div className="section-title">Audit trail</div>
            <ul className="timeline">
              {d.timeline.map((t, idx) => (
                <li key={idx}>
                  <div className="what">{ACTION_TEXT[t.action] || cap(t.action)}
                    {t.to_value && t.action !== "detected" && <span className="muted" style={{ fontWeight: 400 }}> → {STATUS_LABEL[t.to_value as Status] || CLEANUP_LABEL[t.to_value as keyof typeof CLEANUP_LABEL] || t.to_value}</span>}
                  </div>
                  <div className="when">{fmtDateTime(t.at)} · {t.actor}</div>
                  {t.note && <div className="note">{t.note}</div>}
                </li>
              ))}
            </ul>
          </section>
        </div>

        <div className="stack">
          <section className="card">
            <div className="section-title">{isVehicle ? "Vehicle" : d.offender_type === "person" ? "Pedestrian" : "Offender"}</div>
            <div style={{ padding: "14px 20px 18px", display: "flex", flexDirection: "column", gap: 12 }}>
              {isVehicle && (
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <Plate text={d.plate_display} large />
                  {!["notice_issued", "paid", "escalated"].includes(d.status) && (
                    <button className="btn ghost" onClick={() => { setForm({ plate: d.plate_number || "" }); setDialog("plate"); }}>
                      <Pencil /> {d.plate_number ? "Correct" : "Enter plate"}
                    </button>
                  )}
                </div>
              )}
              {isVehicle && d.plate_valid === false && d.plate_number && (
                <div className="callout warn"><AlertTriangle />OCR text does not match the Indian plate format. Check the plate photo and correct it.</div>
              )}
              {d.offender_type === "person" && (
                <div className="callout"><Info />Pedestrian identified by photographic evidence only. Serve the notice on the spot or through the ward office.</div>
              )}
              {d.offender_type === "unknown" && (
                <div className="callout"><Info />Nobody was close enough to be blamed. This case tracks cleanup only.</div>
              )}
              {d.plate_history && d.plate_history.length > 0 && (
                <div className="callout bad"><AlertTriangle />
                  <div>Repeat offender: {d.plate_history.length} other case{d.plate_history.length > 1 ? "s" : ""} for this plate
                    ({d.plate_history.map((h) => <Link key={h.id} to={`/incidents/${h.id}`} style={{ textDecoration: "underline", marginRight: 4 }}>#{h.id}</Link>)}).</div>
                </div>
              )}
            </div>
          </section>

          <section className="card">
            <div className="section-title">Enforcement</div>
            <div className="actions">
              {d.status === "pending_review" && (
                <>
                  <p className="sub" style={{ margin: 0 }}>Check the clip and photos. Is this genuine littering by the marked {isVehicle ? "vehicle" : "person"}?</p>
                  <div className="row">
                    <button className="btn primary" disabled={busy} onClick={() => act("verify")}><BadgeCheck /> Verify</button>
                    <button className="btn danger" disabled={busy} onClick={() => setDialog("reject")}><Ban /> Reject</button>
                  </div>
                </>
              )}
              {d.status === "verified" && d.offender_type !== "unknown" && (
                <>
                  <p className="sub" style={{ margin: 0 }}>Suggested spot fine: <b>{inr(d.suggested_fine)}</b> (as per fine schedule).</p>
                  <div className="row">
                    <button className="btn primary" disabled={busy} onClick={() => { setForm({ fine: String(d.suggested_fine) }); setDialog("notice"); }}><FileText /> Issue notice</button>
                    <button className="btn danger" disabled={busy} onClick={() => setDialog("reject")}><Ban /> Reject</button>
                  </div>
                </>
              )}
              {d.status === "verified" && d.offender_type === "unknown" && (
                <div className="callout good"><CheckCircle2 />Verified as litter. Track the cleanup below.</div>
              )}
              {(d.status === "notice_issued" || d.status === "escalated") && (
                <>
                  <div className="callout">
                    <FileText />
                    <div>Notice <b>{d.notice_number}</b> · {inr(d.fine_amount)} · due {fmtDate(d.due_date)}</div>
                  </div>
                  <div className="row">
                    <button className="btn primary" disabled={busy} onClick={() => setDialog("paid")}><CheckCircle2 /> Mark paid</button>
                    {d.status === "notice_issued" && <button className="btn" disabled={busy} onClick={() => act("escalate", { note: "Unpaid after due date" })}><AlertTriangle /> Escalate</button>}
                  </div>
                  <Link to={`/incidents/${d.id}/notice`} className="btn block"><FileText /> View / print notice</Link>
                </>
              )}
              {d.status === "paid" && (
                <>
                  <div className="callout good"><CheckCircle2 />
                    <div>Fine of {inr(d.fine_amount)} paid {fmtDateTime(d.paid_at)}{d.payment_ref ? ` · ref ${d.payment_ref}` : ""}</div>
                  </div>
                  <Link to={`/incidents/${d.id}/notice`} className="btn block"><FileText /> View notice</Link>
                </>
              )}
              {d.status === "rejected" && (
                <>
                  <div className="callout"><Ban /><div>Rejected: {d.rejection_reason}</div></div>
                  <button className="btn" disabled={busy} onClick={() => act("reopen")}><RotateCcw /> Reopen</button>
                </>
              )}
            </div>
          </section>

          <section className="card">
            <div className="section-title">Cleanup</div>
            <div className="actions">
              <div><CleanupChip status={d.cleanup_status} />{d.cleanup_assignee && <span className="muted"> · {d.cleanup_assignee}</span>}</div>
              {d.cleanup_status !== "cleaned" && (
                <div className="row">
                  {d.cleanup_status === "pending" && <button className="btn" disabled={busy} onClick={() => { setForm({ assignee: "Ward sanitation team" }); setDialog("cleanup"); }}><Truck /> Assign</button>}
                  <button className="btn" disabled={busy} onClick={() => act("mark_cleaned")}><Sparkles /> Mark cleaned</button>
                </div>
              )}
              <button className="btn ghost" disabled={busy} onClick={() => setDialog("note")}><StickyNote /> Add note</button>
            </div>
          </section>
        </div>
      </div>

      {dialog === "reject" && (
        <Modal title="Reject this detection" onClose={() => setDialog(null)}>
          <div className="field"><label htmlFor="reason">Reason (recorded in the audit trail)</label>
            <select id="reason" className="select" value={form.reason || ""} onChange={(e) => setForm({ ...form, reason: e.target.value })}>
              <option value="">Choose…</option>
              <option>Not litter (shadow / leaf / reflection)</option>
              <option>Wrong person or vehicle marked</option>
              <option>Litter placed in a bin or designated spot</option>
              <option>Evidence unclear</option>
              <option>Duplicate of another case</option>
            </select>
          </div>
          <div className="row">
            <button className="btn" onClick={() => setDialog(null)}>Cancel</button>
            <button className="btn primary" disabled={!form.reason || busy} onClick={() => act("reject", { reason: form.reason })}>Reject</button>
          </div>
        </Modal>
      )}
      {dialog === "notice" && (
        <Modal title="Issue littering notice" onClose={() => setDialog(null)}>
          <p className="sub" style={{ margin: 0 }}>
            A notice number is generated and the fine is due in {settings?.notice_due_days ?? 15} days.
            {isVehicle && <> The notice is addressed to the registered owner of <b>{d.plate_display}</b>.</>}
          </p>
          <div className="field"><label htmlFor="fine">Fine amount (₹)</label>
            <input id="fine" className="input" type="number" min={0} value={form.fine || ""} onChange={(e) => setForm({ ...form, fine: e.target.value })} />
          </div>
          <div className="row">
            <button className="btn" onClick={() => setDialog(null)}>Cancel</button>
            <button className="btn primary" disabled={busy || form.fine === ""} onClick={() => act("issue_notice", { fine_amount: Number(form.fine) })}>Issue notice</button>
          </div>
        </Modal>
      )}
      {dialog === "paid" && (
        <Modal title="Record payment" onClose={() => setDialog(null)}>
          <div className="field"><label htmlFor="ref">Receipt / UPI reference (optional)</label>
            <input id="ref" className="input" value={form.ref || ""} onChange={(e) => setForm({ ...form, ref: e.target.value })} placeholder="e.g. UPI 4521…" />
          </div>
          <div className="row">
            <button className="btn" onClick={() => setDialog(null)}>Cancel</button>
            <button className="btn primary" disabled={busy} onClick={() => act("mark_paid", { payment_ref: form.ref })}>Mark paid</button>
          </div>
        </Modal>
      )}
      {dialog === "plate" && (
        <Modal title="Correct number plate" onClose={() => setDialog(null)}>
          {d.evidence.plate && <img src={d.evidence.plate} alt="Plate crop" style={{ borderRadius: 8, maxHeight: 90, objectFit: "contain", background: "#111" }} />}
          {d.plate_raw && d.plate_raw.length > 0 && <div className="cell-sub">OCR read: {Array.from(new Set(d.plate_raw)).slice(0, 4).join(", ")}</div>}
          <div className="field"><label htmlFor="plate">Plate (e.g. KA01AB1234 or 22BH1234AA)</label>
            <input id="plate" className="input mono" value={form.plate || ""} onChange={(e) => setForm({ ...form, plate: e.target.value.toUpperCase() })} />
          </div>
          <div className="row">
            <button className="btn" onClick={() => setDialog(null)}>Cancel</button>
            <button className="btn primary" disabled={busy || !form.plate} onClick={() => act("correct_plate", { plate: form.plate })}>Save</button>
          </div>
        </Modal>
      )}
      {dialog === "cleanup" && (
        <Modal title="Assign cleanup" onClose={() => setDialog(null)}>
          <div className="field"><label htmlFor="assignee">Assign to</label>
            <input id="assignee" className="input" value={form.assignee || ""} onChange={(e) => setForm({ ...form, assignee: e.target.value })} />
          </div>
          <div className="row">
            <button className="btn" onClick={() => setDialog(null)}>Cancel</button>
            <button className="btn primary" disabled={busy} onClick={() => act("assign_cleanup", { assignee: form.assignee })}>Assign</button>
          </div>
        </Modal>
      )}
      {dialog === "note" && (
        <Modal title="Add a note" onClose={() => setDialog(null)}>
          <textarea className="textarea" value={form.note || ""} onChange={(e) => setForm({ ...form, note: e.target.value })} aria-label="Note" />
          <div className="row">
            <button className="btn" onClick={() => setDialog(null)}>Cancel</button>
            <button className="btn primary" disabled={busy || !form.note?.trim()} onClick={() => act("add_note", { note: form.note })}>Add note</button>
          </div>
        </Modal>
      )}
    </>
  );
}
