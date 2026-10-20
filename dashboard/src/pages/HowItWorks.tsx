import { Bike, Camera, Database, Eye, FileCheck2, Gavel, Hourglass, ScanLine, Trash2, UserRound } from "lucide-react";

const EDGE = [
  { icon: Camera, title: "1 · Capture", text: "A single camera watches the clean zone drawn on the frame. The Raspberry Pi (or laptop) processes the video itself." },
  { icon: Bike, title: "2 · Track vehicles", text: "yolov8n finds cars, motorcycles, buses and trucks. ByteTrack gives each one a stable ID (V1, V2…)." },
  { icon: UserRound, title: "3 · Track people", text: "yolo26s finds people and tracks them (P1, P2…), each with its own ID." },
  { icon: Trash2, title: "4 · Spot litter", text: "A background model of the empty zone flags new objects that stay still for 1.5 s while nobody is standing on them." },
  { icon: Hourglass, title: "5 · Cache, then push or bin", text: "Each actor's path and best photos are held for 4 s after it leaves. If litter appears next to it, it is pushed; otherwise it is binned." },
];
const SERVER = [
  { icon: ScanLine, title: "Plate reading", text: "Only for offending vehicles: license_plate.pt finds the plate, EasyOCR reads it, and the text is corrected to the Indian format (KA 01 AB 1234, 22 BH 1234 AA)." },
  { icon: Database, title: "Evidence upload", text: "Clip, snapshots and metadata are saved to an outbox first, then uploaded. Nothing is lost if the network drops." },
  { icon: Eye, title: "Officer review", text: "A sanitary inspector verifies or rejects every detection. No notice goes out without a human check." },
  { icon: Gavel, title: "Notice and fine", text: "The verified case gets a notice number, a fine from the schedule and a due date. Payment or escalation is recorded." },
  { icon: FileCheck2, title: "Cleanup and audit", text: "The litter is assigned to the ward's sanitation team. Every action is logged with the officer's name." },
];

export default function HowItWorks() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>How it works</h1>
          <p>From a camera frame to a closed case: the pipeline behind every incident on this dashboard.</p>
        </div>
      </div>
      <section className="card">
        <div className="card-head"><h2>On the edge device</h2><span className="hint">edge/ · runs next to the camera</span></div>
        <div className="flow">
          {EDGE.map((s) => (
            <div className="f" key={s.title}><b><s.icon aria-hidden /> {s.title}</b><span className="sub">{s.text}</span></div>
          ))}
        </div>
      </section>
      <section className="card" style={{ marginTop: 16 }}>
        <div className="card-head"><h2>Evidence and enforcement</h2><span className="hint">server/ and this dashboard</span></div>
        <div className="flow">
          {SERVER.map((s) => (
            <div className="f" key={s.title}><b><s.icon aria-hidden /> {s.title}</b><span className="sub">{s.text}</span></div>
          ))}
        </div>
      </section>
      <div className="grid grid-2e" style={{ marginTop: 16 }}>
        <section className="card card-pad">
          <h2 style={{ fontSize: 15, marginBottom: 8 }}>Privacy by design</h2>
          <ul className="sub" style={{ margin: 0, paddingLeft: 18 }}>
            <li>Passers-by are never stored: their cache entry is binned a few seconds after they leave.</li>
            <li>No face recognition. People are identified only by photo and video evidence.</li>
            <li>Plates are read only for vehicles linked to a littering event.</li>
            <li>Every officer action is written to an audit trail.</li>
          </ul>
        </section>
        <section className="card card-pad">
          <h2 style={{ fontSize: 15, marginBottom: 8 }}>Case lifecycle</h2>
          <p className="sub" style={{ margin: 0 }}>
            Pending review → Verified → Notice issued → Fine paid. A notice can be escalated if unpaid, and a
            detection can be rejected with a reason (and reopened). Cleanup is tracked separately:
            pending → assigned → cleaned.
          </p>
        </section>
      </div>
    </>
  );
}
