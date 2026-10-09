import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/reader/",
  plugins: [react()],
  build: { rollupOptions: { input: { reader: "index.html", vocabulary: "vocabulary.html", reading: "reading.html" } } },
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
