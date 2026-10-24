import { useState } from "react";
import { KeyRound, Loader2 } from "lucide-react";
import { api, ApiError, setToken, type Settings } from "../api";
import { BrandMark } from "../components/Layout";

// Shown when the server has LW_DASHBOARD_PASSWORD set (the dashboard is online).
export default function Login({ settings, onDone }: { settings: Settings | null; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { token } = await api.login(code);
      setToken(token || "");
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Cannot reach the server");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand" style={{ padding: 0 }}>
          <BrandMark />
          <div>
            <div className="brand-name">Swachh Netra</div>
            <div className="brand-tag">Litter enforcement console</div>
          </div>
        </div>
        <div>
          <h1 style={{ fontSize: 19 }}>{settings?.municipality || "Municipal Corporation"}</h1>
          <p className="sub" style={{ margin: "4px 0 0" }}>Enter the dashboard access code to continue.</p>
        </div>
        <div className="field">
          <label htmlFor="code">Access code</label>
          <input id="code" className="input" type="password" autoComplete="current-password" autoFocus
                 value={code} onChange={(e) => setCode(e.target.value)} />
        </div>
        {error && <div className="callout bad" role="alert">{error}</div>}
        <button className="btn primary block" disabled={busy || !code}>
          {busy ? <Loader2 className="spin" /> : <KeyRound />} Sign in
        </button>
      </form>
    </div>
  );
}
