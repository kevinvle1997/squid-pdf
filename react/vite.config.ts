/// <reference types="vitest/config" />
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import optimizeLocales from "@react-aria/optimize-locales-plugin";
import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vite";

// /api is the Python app's, on the same origin in development too: the owner cookie is SameSite=Strict and the
// API has no CORS. The view's tests have no server: what they ask of it is mocked.
const api: Record<string, string> = process.env.VITEST
  ? {}
  : { "/api": process.env.SQUIDPDF_API ?? "http://127.0.0.1:8000" };
// A folder with key.pem and cert.pem: the end to end tests serve over HTTPS, as Caddy does.
const tls = process.env.SQUIDPDF_TLS;
// The landing links the code this build runs, as the AGPL asks of a copy run for others (section 13).
const REPOSITORY = "https://github.com/kevinvle1997/squid-pdf";

/** Where this build's code is read: its commit's tree, else the repository, and the build says why. */
function sourceCode(): string {
  // The deploy's commit (compose.yaml), else this checkout's.
  const commit = process.env.SQUIDPDF_COMMIT || checkoutCommit();
  if (commit) return `${REPOSITORY}/tree/${commit}`;
  process.emitWarning(
    "No commit to link, so the landing links the repository's main branch: set SQUIDPDF_COMMIT to the commit this build is from.",
  );
  return REPOSITORY;
}

/** This checkout's commit, or none outside one: the web image's build has no .git. */
function checkoutCommit(): string | undefined {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    // No git, or no repository here: the caller falls back, and says so.
    return undefined;
  }
}

/** The Content-Security-Policy Caddy sends, read from its one home, the Caddyfile. */
function caddyPolicy(): string {
  const caddyfile = new URL("../Caddyfile", import.meta.url);
  const policy = /Content-Security-Policy "([^"]+)"/.exec(readFileSync(caddyfile, "utf8"))?.[1];
  if (policy === undefined) throw new Error(`No Content-Security-Policy in ${caddyfile.pathname}`);
  return policy;
}

export default defineConfig(({ isPreview }) => ({
  // React Aria's own words, such as a drop zone's, in the interface's one language: the landing carried every one.
  plugins: [react(), optimizeLocales.vite({ locales: ["en-US"] })],
  // The code the landing links: this build's (`sourceCode`).
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
    // The preview draws in the very font files the server draws with, from src/squidpdf/fonts.
    fs: { allow: [".."] },
  },
  preview: {
    proxy: api,
    https: tls ? { key: readFileSync(`${tls}/key.pem`), cert: readFileSync(`${tls}/cert.pem`) } : undefined,
    // Caddy's policy, so the end to end tests fail on what it refuses (e2e/policy.ts). Preview only:
    // the dev server's hot reload injects inline script, and the web image's build has no Caddyfile.
    headers: isPreview ? { "Content-Security-Policy": caddyPolicy() } : undefined,
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
}));
