// Number-plate check for vehicle cases. The plate is read automatically (enhanced
// image + plate OCR), but nobody gets a notice until an officer confirms it here.
import { AlertTriangle, BadgeCheck, Pencil, ShieldCheck, Sparkles } from "lucide-react";
import { fmtDateTime, type IncidentDetail } from "../api";
import { Plate } from "./ui";

const LEVEL_TEXT: Record<string, string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

export default function PlateReview({ d, busy, onVerify, onPick, onEdit }: {
  d: IncidentDetail;
  busy: boolean;
  onVerify: () => void;
  onPick: (plate: string) => void;
  onEdit: () => void;
}) {
  const locked = ["notice_issued", "paid", "escalated"].includes(d.status);
  const alternatives = (d.plate_candidates || []).filter((c) => c.text !== d.plate_number);
  const pct = d.plate_confidence !== null && d.plate_confidence !== undefined ? Math.round(d.plate_confidence * 100) : null;

  return (
    <div className="plate-review">
      {(d.evidence.plate || d.evidence.plate_enhanced) && (
        <div className="plate-shots">
          {d.evidence.plate && (
            <figure>
              <img src={d.evidence.plate} alt="Number plate as seen by the camera" />
              <figcaption>Camera</figcaption>
            </figure>
          )}
          {d.evidence.plate_enhanced && (
            <figure>
              <img src={d.evidence.plate_enhanced} alt="Number plate after enhancement" />
              <figcaption><Sparkles size={12} aria-hidden /> Enhanced (multi-frame)</figcaption>
            </figure>
          )}
        </div>
      )}

      <div className="plate-line">
        <Plate text={d.plate_display} large />
        {d.plate_number && (
          d.plate_verified ? (
            <span className="badge paid"><ShieldCheck aria-hidden /> Verified</span>
          ) : (
            <span className="badge pending_review"><AlertTriangle aria-hidden /> Not verified</span>
          )
        )}
      </div>

      {d.plate_number && !d.plate_verified && (
        <div className="cell-sub">
          Read automatically{d.plate_corrected ? "" : d.plate_engine === "plate-ocr" ? " from the enhanced plate" : ""}
          {d.plate_level && <> · <span className={`conf ${d.plate_level}`}>{LEVEL_TEXT[d.plate_level] || d.plate_level}{pct !== null ? ` (${pct}%)` : ""}</span></>}
        </div>
      )}
      {d.plate_verified && (
        <div className="cell-sub">
          {d.plate_corrected ? "Entered" : "Confirmed"} by <b>{d.plate_verified_by}</b> · {fmtDateTime(d.plate_verified_at)}
        </div>
      )}

      {!d.plate_number && (
        <div className="callout warn"><AlertTriangle />
          <div>The plate could not be read. Look at the plate photos and enter the number.</div>
        </div>
      )}
      {d.plate_number && !d.plate_verified && d.plate_level === "low" && (
        <div className="callout warn"><AlertTriangle />
          <div>Low confidence: compare every character with the enhanced image before confirming.</div>
        </div>
      )}

      {!locked && d.plate_number && !d.plate_verified && alternatives.length > 0 && (
        <div className="alternatives">
          <span className="cell-sub">Other possible readings:</span>
          {alternatives.map((c) => (
            <button key={c.text} className="alt" disabled={busy} onClick={() => onPick(c.text)}
                    title={`Use ${c.pretty || c.text}`}>
              {c.pretty || c.text} <span className="muted">{Math.round(c.confidence * 100)}%</span>
            </button>
          ))}
        </div>
      )}

      {!locked && (
        <div className="row">
          {d.plate_number && !d.plate_verified && (
            <button className="btn primary" disabled={busy} onClick={onVerify}>
              <BadgeCheck /> Plate verified
            </button>
          )}
          <button className="btn" disabled={busy} onClick={onEdit}>
            <Pencil /> {d.plate_number ? "Correct plate" : "Enter plate"}
          </button>
        </div>
      )}
      {!locked && d.plate_number && !d.plate_verified && (
        <p className="cell-sub" style={{ margin: 0 }}>
          A notice can only be issued after the plate is verified, so a fine never goes to the wrong vehicle owner.
        </p>
      )}
    </div>
  );
}
