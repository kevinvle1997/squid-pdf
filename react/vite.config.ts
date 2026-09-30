/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vite";

// Everything under /api is the Python app's; the rest of the host is this one's.
// Same origin in development too: the owner cookie is SameSite=Strict, and the API has no CORS.
const api = { "/api": process.env.SQUIDPDF_API ?? "http://127.0.0.1:8000" };

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: api,
    // The preview draws in the very font files the server draws with, from src/squidpdf/fonts.
    fs: { allow: [".."] },
  },
  preview: { proxy: api },
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
