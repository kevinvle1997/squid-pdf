/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vite";

// Everything under /api is the Python app's; the rest of the host is this one's.
// Same origin in development too: the owner cookie is SameSite=Strict, and the API has no CORS.
// The view's tests have no server: what they ask of it is mocked, and page images just fail.
const api: Record<string, string> = process.env.VITEST
  ? {}
  : { "/api": process.env.SQUIDPDF_API ?? "http://127.0.0.1:8000" };
// A folder with key.pem and cert.pem: the end to end tests serve over HTTPS, as Caddy does.
const tls = process.env.SQUIDPDF_TLS;

export default defineConfig({
  plugins: [react()],
  // Bundled up front, so a test never reloads halfway with a second copy of React.
  optimizeDeps: {
    include: [
      "react",
      "react-dom",
      "react-dom/client",
      "react-aria",
      "react-aria-components",
      "use-sync-external-store/with-selector",
      "vitest-browser-react",
    ],
  },
  server: {
    proxy: api,
    // The preview draws in the very font files the server draws with, from src/squidpdf/fonts.
    fs: { allow: [".."] },
  },
  preview: {
    proxy: api,
    https: tls ? { key: readFileSync(`${tls}/key.pem`), cert: readFileSync(`${tls}/cert.pem`) } : undefined,
  },
  test: {
    projects: [
      // The logic: no React, no DOM, in node.
      { extends: true, test: { name: "unit", include: ["src/**/*.test.ts"] } },
      // What's drawn: in a real Chromium, for its layout, observers, fonts and React Aria's pointer handling.
      {
        extends: true,
        test: {
          name: "view",
          include: ["src/**/*.test.tsx"],
          browser: { enabled: true, headless: true, provider: playwright(), instances: [{ browser: "chromium" }] },
        },
      },
    ],
  },
});
