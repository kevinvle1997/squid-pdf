import { defineConfig, devices } from "@playwright/test";

// The real API and the built app, as a user gets them: nothing mocked.
const API_PORT = 8765;
const WEB_PORT = 4173;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? "github" : "list",
  use: {
    // localhost, not 127.0.0.1: the owner cookie is Secure, and browsers trust localhost with it.
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `cd .. && SQUIDPDF_DATA="$(mktemp -d)" uv run uvicorn squidpdf.api.app:create_app --factory --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: `npm run build && npx vite preview --port ${WEB_PORT} --strictPort`,
      env: { SQUIDPDF_API: `http://127.0.0.1:${API_PORT}` },
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
