/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
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
  test: { include: ["src/**/*.test.ts"] },
});
