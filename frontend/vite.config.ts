import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Local dev: the backend runs on :8000. In production the frontend is
      // a static build and VITE_API_BASE points at the Cloud Run URL.
      "/api": "http://localhost:8000",
    },
  },
});
