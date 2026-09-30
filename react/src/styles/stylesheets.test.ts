// What the stylesheets promise that CSS can't say itself: tokens.css writes the dark values
// twice, as the token contract does, and a media query can't read a variable.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");

function stylesheets(folder = SRC): { path: string; css: string }[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const full = join(folder, entry.name);
    if (entry.isDirectory()) return stylesheets(full);
    return entry.name.endsWith(".css") ? [{ path: full.slice(SRC.length + 1), css: readFileSync(full, "utf8") }] : [];
  });
}

/** The declarations inside the first `{…}` after `selector`, one per line, as written. */
function block(css: string, selector: string): string[] {
  const start = css.indexOf("{", css.indexOf(selector)) + 1;
  return css
    .slice(start, css.indexOf("}", start))
    .split(";")
    .map((declaration) => declaration.trim())
    .filter(Boolean);
}

const ALL = stylesheets();

describe("the stylesheets", () => {
  test("the dark values are the same for the system's setting and the user's choice", () => {
    const tokens = readFileSync(join(SRC, "styles/tokens.css"), "utf8");
    const system = block(tokens, ':root:not([data-theme="light"])');
    expect(system.length).toBeGreaterThan(0);
    expect(block(tokens, ':root[data-theme="dark"]')).toEqual(system);
  });

  test("every narrow layout starts at the same width", () => {
    const widths = new Set(
      ALL.flatMap(({ css }) => [...css.matchAll(/@media \(max-width: ([^)]+)\)/g)].map(([, width]) => width)),
    );
    expect([...widths]).toEqual(["820px"]);
  });

  test("what sits over what is a role in tokens.css, never a number", () => {
    const numbered = ALL.flatMap(({ path, css }) =>
      [...css.matchAll(/z-index:\s*([^;]+);/g)]
        .filter(([, value]) => !value?.startsWith("var(--z-"))
        .map(([found]) => `${path}: ${found}`),
    );
    expect(numbered).toEqual([]);
  });
});
