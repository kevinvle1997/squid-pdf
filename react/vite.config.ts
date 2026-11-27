/// <reference types="vitest/config" />
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import optimizeLocales from "@react-aria/optimize-locales-plugin";
import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vite";

// The owner cookie is SameSite=Strict and the API has no CORS, so /api is on the same origin in development too.
const api: Record<string, string> = process.env.VITEST
  ? {}
  : { "/api": process.env.SQUIDPDF_API ?? "http://127.0.0.1:8000" };
const tls = process.env.SQUIDPDF_TLS;
// The landing links the code this build runs, as the AGPL asks of a copy run for others (section 13).
const REPOSITORY = "https://github.com/kevinvle1997/squid-pdf";

function sourceCode(): string {
  const commit = process.env.SQUIDPDF_COMMIT || checkoutCommit();
  if (commit) return `${REPOSITORY}/tree/${commit}`;
  process.emitWarning(
    "No commit to link, so the landing links the repository's main branch: set SQUIDPDF_COMMIT to the commit this build is from.",
  );
  return REPOSITORY;
}

function checkoutCommit(): string | undefined {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return undefined;
  }
}

function caddyPolicy(): string {
  const caddyfile = new URL("../Caddyfile", import.meta.url);
  const policy = /Content-Security-Policy "([^"]+)"/.exec(readFileSync(caddyfile, "utf8"))?.[1];
  if (policy === undefined) throw new Error(`No Content-Security-Policy in ${caddyfile.pathname}`);
  return policy;
}

export default defineConfig(({ isPreview }) => ({
  plugins: [react(), optimizeLocales.vite({ locales: ["en-US"] })],
  define: { __SOURCE_CODE__: JSON.stringify(sourceCode()) },
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
    fs: { allow: [".."] },
  },
  preview: {
    proxy: api,
    https: tls ? { key: readFileSync(`${tls}/key.pem`), cert: readFileSync(`${tls}/cert.pem`) } : undefined,
    // Preview only: the dev server's hot reload injects inline script, and the web image's build has no Caddyfile.
    headers: isPreview ? { "Content-Security-Policy": caddyPolicy() } : undefined,
  },
  test: {
    projects: [
      { extends: true, test: { name: "unit", include: ["src/**/*.test.ts"] } },
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
}));
