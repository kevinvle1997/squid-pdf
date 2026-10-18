// What the stylesheets promise that CSS can't say itself: the token contract's dark values written twice,
// one breakpoint, colours and gaps only from tokens.css, and stacking by its roles.
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

/** `css` without its comments, whose prose may read like a declaration. */
function uncommented(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Each declaration of one of `properties` in `css`, as `path: property: value`. */
function declared(path: string, css: string, properties: RegExp): { where: string; value: string }[] {
  return [...uncommented(css).matchAll(/([a-z-]+)\s*:\s*([^;{}]+);/g)]
    .filter(([, property = ""]) => properties.test(property))
    .map(([, property, value = ""]) => ({ where: `${path}: ${property}: ${value.trim()}`, value: value.trim() }));
}

const NESTING: Readonly<Record<string, number>> = { "(": 1, ")": -1 };

/** A value's parts, split at the spaces outside any parentheses: `calc(-1 * var(--s8)) auto` is two. */
function partsOf(value: string): string[] {
  const parts = [""];
  let depth = 0;
  for (const letter of value) {
    depth += NESTING[letter] ?? 0;
    if (depth === 0 && /\s/.test(letter)) parts.push("");
    else parts[parts.length - 1] += letter;
  }
  return parts.filter(Boolean);
}

// A step of the spacing scale, or none: design.md, Space and layout.
const SPACING_STEP = /^(0|auto|var\(--s[1-8]\)|calc\(-1 \* var\(--s[1-8]\)\))$/;

describe("the stylesheets", () => {
  test("the dark values are the same for the system's setting and the user's choice", () => {
    const tokens = readFileSync(join(SRC, "styles/tokens.css"), "utf8");
    const system = block(tokens, ':root:not([data-theme="light"])');
    expect(system.length).toBeGreaterThan(0);
    expect(block(tokens, ':root[data-theme="dark"]')).toEqual(system);
  });

  test("every narrow layout starts at the same width, however its query is written", () => {
    const widths = new Set(
      ALL.flatMap(({ css }) =>
        [...uncommented(css).matchAll(/@media ([^{]+)\{/g)]
          .map(([, condition = ""]) => condition.trim())
          .filter((condition) => condition.includes("width")),
      ),
    );
    expect([...widths]).toEqual(["(max-width: 820px)"]);
  });

  test("a colour is a token: only tokens.css writes one", () => {
    const written = ALL.filter(({ path }) => path !== "styles/tokens.css").flatMap(({ path, css }) =>
      [...uncommented(css).matchAll(/#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\(/gi)].map(
        ([found]) => `${path}: ${found}`,
      ),
    );
    expect(written).toEqual([]);
  });

  test("every gap is a step of the spacing scale", () => {
    const stray = ALL.flatMap(({ path, css }) =>
      declared(path, css, /^(gap|row-gap|column-gap|margin|padding)(-.+)?$/).filter(
        ({ value }) => !partsOf(value).every((part) => SPACING_STEP.test(part)),
      ),
    );
    // The one exception: a visually hidden element's margin, the standard pattern's -1px.
    expect(stray.map(({ where }) => where)).toEqual(["styles/base.css: margin: -1px"]);
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
