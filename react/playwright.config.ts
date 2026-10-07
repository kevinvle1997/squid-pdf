import { defineConfig, devices } from "@playwright/test";

// The real API and the built app, as a user gets them: nothing mocked.
const API_PORT = 8765;
const WEB_PORT = 4173;
// A certificate made for the run. Over plain HTTP, WebKit drops the Secure owner cookie, even on localhost.
const CERTIFICATE = `openssl req -x509 -newkey rsa:2048 -nodes -subj /CN=localhost -days 1 -keyout "$tls/key.pem" -out "$tls/cert.pem" 2>/dev/null`;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `https://localhost:${WEB_PORT}`,
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    // Safari, where every iOS user is. Only on CI: a cloud session can't install WebKit.
    ...(process.env.CI ? [{ name: "webkit", use: { ...devices["Desktop Safari"] } }] : []),
  ],
  webServer: [
    {
      command: `cd .. && SQUIDPDF_DATA="$(mktemp -d)" uv run uvicorn squidpdf.api.app:create_app --factory --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: `npm run build && tls="$(mktemp -d)" && ${CERTIFICATE} && SQUIDPDF_TLS="$tls" npx vite preview --port ${WEB_PORT} --strictPort`,
      env: { SQUIDPDF_API: `http://127.0.0.1:${API_PORT}` },
      url: `https://localhost:${WEB_PORT}`,
      ignoreHTTPSErrors: true,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
