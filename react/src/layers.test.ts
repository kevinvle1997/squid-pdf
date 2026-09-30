// The browser's layers, as tests/test_layers.py checks the server's. A feature's logic sits at
// its folder's root and knows nothing of React; what draws it is in its view/, as only a
// feature's api.py knows FastAPI. Imports go one way: editing may use documents, never back.
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, posix, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

const SRC = dirname(fileURLToPath(import.meta.url));
const FRAMEWORK = ["react", "react-dom", "react-dom/client", "react-aria-components"];
const IMPORTS = /(?:^|\n)\s*(?:import|export)\b[^"';]*?["']([^"']+)["']|\bimport\(\s*["']([^"']+)["']\s*\)/g;

interface Source {
  path: string; // from src/, with forward slashes
  imports: string[]; // relative ones resolved from src/, packages as written
}

function sources(folder = SRC): Source[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const full = join(folder, entry.name);
    if (entry.isDirectory()) return sources(full);
    if (!/\.tsx?$/.test(entry.name) || entry.name.endsWith(".d.ts")) return [];
    const path = relative(SRC, full).split("\\").join("/");
    const imports = [...readFileSync(full, "utf8").matchAll(IMPORTS)].map(([, from = "", lazy = ""]) => {
      const spec = from || lazy;
      return spec.startsWith(".") ? posix.join(posix.dirname(path), spec) : spec;
    });
    return [{ path, imports }];
  });
}

const ALL = sources();
const isView = (path: string) => /^[^/]+\/view\//.test(path);
const drawsWithReact = (path: string) => isView(path) || path.startsWith("ui/") || ["App.tsx", "main.tsx"].includes(path);
const isFramework = (spec: string) => FRAMEWORK.includes(spec) || spec.endsWith(".module.css");
const slice = (path: string) => path.split("/")[0] ?? "";

function breaking(rule: (source: Source, spec: string) => boolean): string[] {
  return ALL.flatMap((source) => source.imports.filter((spec) => rule(source, spec)).map((spec) => `${source.path} → ${spec}`));
}

describe("the browser's layers", () => {
  test("the files are found", () => {
    expect(ALL.map((source) => source.path)).toContain("editing/fit.ts");
  });

  test("only a view, a base component or the app's shell uses React", () => {
    expect(breaking(({ path }, spec) => isFramework(spec) && !drawsWithReact(path))).toEqual([]);
  });

  test("a feature's logic never reaches into a view", () => {
    expect(breaking(({ path }, spec) => !drawsWithReact(path) && isView(spec))).toEqual([]);
  });

  test("base components know nothing of the API or any feature", () => {
    const outside = ["api", "documents", "editing"];
    expect(breaking(({ path }, spec) => path.startsWith("ui/") && outside.includes(slice(spec)))).toEqual([]);
  });

  test("the API's client knows no feature and nothing drawn", () => {
    const outside = ["documents", "editing", "ui"];
    expect(breaking(({ path }, spec) => path.startsWith("api/") && outside.includes(slice(spec)))).toEqual([]);
  });

  test("editing may use documents, and documents never editing", () => {
    expect(breaking(({ path }, spec) => slice(path) === "documents" && slice(spec) === "editing")).toEqual([]);
  });
});
