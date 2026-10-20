import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle, BadgeCheck, CheckCircle2, CircleDashed, Clock3, FileText, Inbox, Sparkles, Truck, XCircle,
} from "lucide-react";
import { CLEANUP_LABEL, STATUS_LABEL, type Cleanup, type Settings, type Status } from "../api";

// ------------------------------------------------------------ app context
interface AppCtx {
  settings: Settings | null;
  officer: string;
  setOfficer: (name: string) => void;
  notify: (message: string, kind?: "info" | "error") => void;
  pendingCount: number;
  setPendingCount: (n: number) => void;
}

export const AppContext = createContext<AppCtx>(null as unknown as AppCtx);
export const useApp = () => useContext(AppContext);

export function AppProvider({ settings, children }: { settings: Settings | null; children: ReactNode }) {
  const [officer, setOfficerState] = useState(() => {
    try {
      return localStorage.getItem("officer") || "";
    } catch {
      return "";
    }
  });
  const [toast, setToast] = useState<{ message: string; kind: "info" | "error" } | null>(null);
  const [pendingCount, setPendingCount] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  const setOfficer = (name: string) => {
    setOfficerState(name);
    try {
      localStorage.setItem("officer", name);
    } catch {
      /* private mode - keep it in memory only */
    }
  };
  const notify = useCallback((message: string, kind: "info" | "error" = "info") => {
    setToast({ message, kind });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), 3800);
  }, []);

  return (
    <AppContext.Provider value={{ settings, officer, setOfficer, notify, pendingCount, setPendingCount }}>
      {children}
      {toast && (
        <div className={`toast ${toast.kind === "error" ? "error" : ""}`} role="status">
          {toast.message}
        </div>
      )}
    </AppContext.Provider>
  );
}

// ------------------------------------------------------------ polling hook
export function usePolling<T>(fn: () => Promise<T>, deps: unknown[], intervalMs = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const load = useCallback(async () => {
    try {
      const result = await fnRef.current();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    load();
    if (!intervalMs) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, intervalMs);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload: load, setData };
}

// ------------------------------------------------------------ badges
const STATUS_ICON: Record<Status, typeof Clock3> = {
  pending_review: Clock3,
  verified: BadgeCheck,
  notice_issued: FileText,
  paid: CheckCircle2,
  escalated: AlertTriangle,
  rejected: XCircle,
};

export function StatusBadge({ status }: { status: Status }) {
  const Icon = STATUS_ICON[status];
  return (
    <span className={`badge ${status}`}>
      <Icon aria-hidden /> {STATUS_LABEL[status]}
    </span>
  );
}

export function OverdueBadge() {
  return (
    <span className="badge overdue">
      <AlertTriangle aria-hidden /> Overdue
    </span>
  );
}

export function SampleBadge() {
  return <span className="badge sample" title="Generated sample data">Sample</span>;
}

const CLEAN_ICON: Record<Cleanup, typeof Clock3> = { pending: CircleDashed, assigned: Truck, cleaned: Sparkles };

export function CleanupChip({ status }: { status: Cleanup }) {
  const Icon = CLEAN_ICON[status];
  return (
    <span className={`chip-clean ${status}`}>
      <Icon aria-hidden /> {CLEANUP_LABEL[status]}
    </span>
  );
}

export function Plate({ text, large }: { text: string | null | undefined; large?: boolean }) {
  if (!text) return <span className={`plate unread ${large ? "lg" : ""}`}><span className="num">NOT READ</span></span>;
  return (
    <span className={`plate ${large ? "lg" : ""}`} aria-label={`Number plate ${text}`}>
      <span className="ind">IND</span>
      <span className="num">{text}</span>
    </span>
  );
}

// ------------------------------------------------------------ misc
export function Kpi({ label, value, foot, icon, hero }: {
  label: string; value: ReactNode; foot?: ReactNode; icon?: ReactNode; hero?: boolean;
}) {
  return (
    <div className={`card kpi ${hero ? "hero" : ""}`}>
      <div className="label">{icon}{label}</div>
      <div className="value">{value}</div>
      {foot && <div className="foot">{foot}</div>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <Inbox aria-hidden />
      <div style={{ fontWeight: 600 }}>{title}</div>
      {children && <div className="muted" style={{ marginTop: 4 }}>{children}</div>}
    </div>
  );
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <h3>{title}</h3>
        {children}
      </div>
    </div>
  );
}
