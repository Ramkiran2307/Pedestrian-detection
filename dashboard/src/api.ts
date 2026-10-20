// Typed client for the Flask API (see server/app.py).

export type OffenderType = "person" | "vehicle" | "unknown";
export type Status = "pending_review" | "verified" | "notice_issued" | "paid" | "escalated" | "rejected";
export type Cleanup = "pending" | "assigned" | "cleaned";

export interface Incident {
  id: number;
  uid: string;
  camera_id: string | null;
  zone: string | null;
  ward: string | null;
  location: string | null;
  occurred_at: string;
  detected_at: string | null;
  received_at: string;
  offender_type: OffenderType;
  offender_track: string | null;
  attribution_score: number | null;
  vehicle_class: string | null;
  vehicle_category: string | null;
  linked_track: string | null;
  plate_number: string | null;
  plate_display: string | null;
  plate_confidence: number | null;
  plate_valid: boolean | null;
  plate_raw: string[] | null;
  plate_corrected: boolean;
  status: Status;
  cleanup_status: Cleanup;
  cleanup_assignee: string | null;
  fine_amount: number | null;
  notice_number: string | null;
  notice_issued_at: string | null;
  due_date: string | null;
  paid_at: string | null;
  payment_ref: string | null;
  rejection_reason: string | null;
  is_sample: boolean;
  evidence: Partial<Record<"scene" | "litter" | "person" | "vehicle" | "plate" | "clip", string>>;
  thumbnail: string | null;
  overdue: boolean;
  suggested_fine: number;
  suspects?: { track: string; type: string; class: string; score: number; distance: number }[];
}

export interface TimelineEntry {
  at: string;
  actor: string;
  action: string;
  from_value: string | null;
  to_value: string | null;
  note: string | null;
}

export interface Camera {
  id: string;
  name: string | null;
  location: string | null;
  ward: string | null;
  zone: string | null;
  preview_url: string | null;
  last_seen: string | null;
  online: boolean;
  incidents: number;
  stats: Record<string, number | string | boolean | null> | null;
}

export interface IncidentDetail extends Incident {
  timeline: TimelineEntry[];
  plate_history?: { id: number; occurred_at: string; status: Status; fine_amount: number | null }[];
  camera: Camera | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Stats {
  totals: {
    all: number; today: number; pending_review: number; overdue: number;
    cleanup_pending: number; cameras: number; cameras_online: number;
  };
  by_status: Record<Status, number>;
  by_type: Record<OffenderType, number>;
  by_vehicle: Record<string, number>;
  fines: { issued: number; collected: number; notices: number };
  review_accuracy: number | null;
  daily: { date: string; person: number; vehicle: number; unknown: number }[];
  hourly: { hour: number; count: number }[];
  repeat_offenders: { plate: string; display: string; count: number; fines: number; last_seen: string }[];
}

export interface Settings {
  municipality: string;
  department: string;
  timezone: string;
  currency: string;
  fines: Record<string, number>;
  notice_due_days: number;
  dev_tools?: boolean;
}

export interface DevJob {
  id: string;
  filename: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  full_frame: boolean;
  created: number;
  started: number | null;
  finished: number | null;
  frames: number;
  total_frames: number;
  percent: number | null;
  fps: number | null;
  incidents: number;
  uploaded: number;
  binned: number;
  error: string | null;
  log_tail: string[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body.error || `Request failed (${res.status})`);
  return body as T;
}

export const api = {
  stats: () => request<Stats>("/api/v1/stats"),
  settings: () => request<Settings>("/api/v1/settings"),
  cameras: () => request<Camera[]>("/api/v1/cameras"),
  incidents: (params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "") qs.set(k, String(v));
    });
    return request<Page<Incident>>(`/api/v1/incidents?${qs}`);
  },
  incident: (id: number | string) => request<IncidentDetail>(`/api/v1/incidents/${id}`),
  act: (id: number, body: Record<string, unknown>) =>
    request<IncidentDetail>(`/api/v1/incidents/${id}/actions`, { method: "POST", body: JSON.stringify(body) }),
  devJobs: () => request<DevJob[]>("/api/v1/dev/jobs"),
  cancelDevJob: (id: string) => request<DevJob>(`/api/v1/dev/jobs/${id}/cancel`, { method: "POST" }),
  // XMLHttpRequest instead of fetch so the upload can report progress.
  uploadVideo: (file: File, fullFrame: boolean, onProgress: (pct: number) => void) =>
    new Promise<DevJob>((resolve, reject) => {
      const form = new FormData();
      form.append("video", file);
      form.append("full_frame", fullFrame ? "1" : "0");
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/v1/dev/jobs");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
      xhr.onload = () => {
        let body: { error?: string } = {};
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* not JSON */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body as DevJob);
        else reject(new ApiError(xhr.status, body.error || `Upload failed (${xhr.status})`));
      };
      xhr.onerror = () => reject(new ApiError(0, "Network error while uploading"));
      xhr.send(form);
    }),
};

// ---------------------------------------------------------------- formatting
const TZ = "Asia/Kolkata";

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: TZ, day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { timeZone: TZ, day: "2-digit", month: "short", year: "numeric" });
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.max(s, 0)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function inr(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return "₹" + n.toLocaleString("en-IN");
}

export function compactInr(n: number): string {
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(1)} Cr`;
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(1)} L`;
  if (n >= 1e3) return `₹${(n / 1e3).toFixed(1)}K`;
  return `₹${n}`;
}

export const STATUS_LABEL: Record<Status, string> = {
  pending_review: "Pending review",
  verified: "Verified",
  notice_issued: "Notice issued",
  paid: "Fine paid",
  escalated: "Escalated",
  rejected: "Rejected",
};

export const CLEANUP_LABEL: Record<Cleanup, string> = {
  pending: "Cleanup pending",
  assigned: "Cleanup assigned",
  cleaned: "Cleaned",
};

export const VEHICLE_LABEL: Record<string, string> = {
  two_wheeler: "Two-wheeler",
  car: "Car / LMV",
  heavy: "Bus / truck",
  other: "Other",
};

export function offenderLabel(i: Pick<Incident, "offender_type" | "vehicle_class">): string {
  if (i.offender_type === "vehicle") return i.vehicle_class ? cap(i.vehicle_class) : "Vehicle";
  if (i.offender_type === "person") return "Pedestrian";
  return "Unattributed";
}

export function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");
}
