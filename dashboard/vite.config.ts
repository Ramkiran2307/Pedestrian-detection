import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the Flask server runs on :5000; proxy the API and evidence files
// so the browser sees one origin (no CORS setup needed).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:5000",
      "/media": "http://127.0.0.1:5000",
    },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 900 },
});
