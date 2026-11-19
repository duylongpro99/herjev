import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The built UI is the Worker's static assets (see wrangler.jsonc).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    // `npm run dev:worker` serves the API (port set in wrangler.jsonc).
    proxy: { "/api": "http://127.0.0.1:8790" },
  },
  test: {
    projects: [
      { extends: true, test: { name: "ui", environment: "node", include: ["src/**/*.test.ts"] } },
      {
        extends: true,
        // The API tests need Postgres: $HERJEV_TEST_DATABASE_URL, or the docker compose one.
        test: {
          name: "worker",
          environment: "node",
          include: ["worker/**/*.test.ts"],
          globalSetup: ["worker/global-setup.ts"],
        },
      },
    ],
  },
});
