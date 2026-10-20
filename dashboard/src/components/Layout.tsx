import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { ClipboardList, LayoutDashboard, Menu, Moon, MonitorPlay, Sun, Workflow } from "lucide-react";
import { useApp } from "./ui";

type Theme = "light" | "dark";

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* ignore */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden>
      <svg width="22" height="22" viewBox="0 0 64 64">
        <path d="M6 32c7-11 16-17 26-17s19 6 26 17c-7 11-16 17-26 17S13 43 6 32z" fill="none" stroke="#fff" strokeWidth="5" />
        <circle cx="32" cy="32" r="8" fill="#f4b740" />
      </svg>
    </span>
  );
}

export default function Layout() {
  const { settings, officer, setOfficer, pendingCount } = useApp();
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [open, setOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  useEffect(() => setOpen(false), [location.pathname]);

  const initials = officer.trim().split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "?";

  return (
    <div className="shell">
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand">
          <BrandMark />
          <div>
            <div className="brand-name">Swachh Netra</div>
            <div className="brand-tag">Litter enforcement console</div>
          </div>
        </div>
        <nav className="nav" aria-label="Main">
          <div className="nav-label">Monitor</div>
          <NavLink to="/" end><LayoutDashboard size={18} /> Overview</NavLink>
          <NavLink to="/live"><MonitorPlay size={18} /> Live camera</NavLink>
          <div className="nav-label">Enforcement</div>
          <NavLink to="/incidents">
            <ClipboardList size={18} /> Offence register
            {pendingCount > 0 && <span className="count" title="Pending review">{pendingCount}</span>}
          </NavLink>
          <NavLink to="/how-it-works"><Workflow size={18} /> How it works</NavLink>
        </nav>
        <div className="sidebar-foot">
          {settings?.department || "Solid Waste Management"}
          <br />
          Swachh Bharat Mission (Urban) aligned workflow
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="icon-btn menu-btn" aria-label="Open menu" onClick={() => setOpen(!open)}>
            <Menu size={18} />
          </button>
          <div className="org">
            <b>{settings?.municipality || "Municipal Corporation"}</b>
            <span>{settings?.department || "Solid Waste Management Department"}</span>
          </div>
          <div className="spacer" />
          <label className="officer" title="Your name is recorded in the audit trail of every action">
            <span className="visually-hidden">Acting officer</span>
            <input value={officer} onChange={(e) => setOfficer(e.target.value)} placeholder="Your name / designation" />
            <span className="avatar" aria-hidden>{initials}</span>
          </label>
          <button className="icon-btn" aria-label="Toggle dark mode" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
