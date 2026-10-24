import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { api, AUTH_REQUIRED_EVENT, getToken, type Settings } from "./api";
import Layout from "./components/Layout";
import { AppProvider, Empty } from "./components/ui";
import HowItWorks from "./pages/HowItWorks";
import IncidentDetail from "./pages/IncidentDetail";
import Incidents from "./pages/Incidents";
import Live from "./pages/Live";
import Login from "./pages/Login";
import Notice from "./pages/Notice";
import Overview from "./pages/Overview";
import "./styles.css";

function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  useEffect(() => {
    api.settings()
      .then((s) => {
        setSettings(s);
        if (s.auth_required && !getToken()) setNeedLogin(true);
      })
      .catch(() => setSettings(null));
    const onAuth = () => setNeedLogin(true);
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onAuth);
  }, []);
  if (needLogin) return <Login settings={settings} onDone={() => setNeedLogin(false)} />;
  return (
    <AppProvider settings={settings}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="incidents" element={<Incidents />} />
            <Route path="incidents/:id" element={<IncidentDetail />} />
            <Route path="incidents/:id/notice" element={<Notice />} />
            <Route path="live" element={<Live />} />
            <Route path="how-it-works" element={<HowItWorks />} />
            <Route path="*" element={<Empty title="Page not found" />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
