import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { api, type Settings } from "./api";
import Layout from "./components/Layout";
import { AppProvider, Empty } from "./components/ui";
import HowItWorks from "./pages/HowItWorks";
import IncidentDetail from "./pages/IncidentDetail";
import Incidents from "./pages/Incidents";
import Live from "./pages/Live";
import Notice from "./pages/Notice";
import Overview from "./pages/Overview";
import "./styles.css";

function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  useEffect(() => {
    api.settings().then(setSettings).catch(() => setSettings(null));
  }, []);
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
