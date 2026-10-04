// The Caddyfile allows React Aria's inline style by hash, so a changed one breaks only production.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "vitest";

const REACT = join(dirname(fileURLToPath(import.meta.url)), "..");
const USE_PRESS = join(REACT, "node_modules/react-aria/dist/private/interactions/usePress.mjs");
const CADDYFILE = join(REACT, "../Caddyfile");

/** The text of the style tag usePress adds, as the browser hashes it. */
function pressableStyle(): string {
  const source = readFileSync(USE_PRESS, "utf8");
  const template = /style\.textContent = `([^`]*)`\.trim\(\)/.exec(source)?.[1];
  if (template === undefined) throw new Error(`usePress no longer sets its style as this test reads it: ${USE_PRESS}`);
  return template
    .replace(/\$\{([^}]+)\}/g, (_, name: string) => {
      const value = new RegExp(`${name.replaceAll("$", "\\$")} = '([^']*)'`).exec(source)?.[1];
      if (value === undefined) throw new Error(`usePress's ${name} isn't a plain string`);
      return value;
    })
    .trim();
}

test("the proxy's policy allows React Aria's one style tag, by its hash", () => {
  const hash = createHash("sha256").update(pressableStyle()).digest("base64");
  const policy = /Content-Security-Policy "([^"]+)"/.exec(readFileSync(CADDYFILE, "utf8"))?.[1] ?? "";
  expect(policy).toContain(`'sha256-${hash}'`);
});
