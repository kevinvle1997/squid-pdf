// The preview sends the Caddyfile's policy (vite.config.ts), so what's refused here is refused in production.
import { test as base, expect } from "@playwright/test";

interface Refusal {
  directive: string;
  blocked: string;
  source: string;
}

export const test = base.extend<{ policyKept: undefined }>({
  policyKept: [
    async ({ page }, use) => {
      const refusals: Refusal[] = [];
      // A binding, not a list on the page: it outlives a navigation.
      await page.exposeFunction("reportRefusal", (refusal: Refusal) => {
        refusals.push(refusal);
      });
      // The same in Chromium and WebKit, where console text isn't.
      await page.addInitScript(() => {
        document.addEventListener("securitypolicyviolation", (event) => {
          const report = (window as unknown as { reportRefusal: (refusal: Refusal) => void }).reportRefusal;
          report({
            directive: event.effectiveDirective,
            blocked: event.blockedURI,
            source: `${event.sourceFile}:${event.lineNumber}`,
          });
        });
      });
      await use(undefined);
      // A round trip, so a refusal the last step raised has been reported.
      if (!page.isClosed()) await page.evaluate(() => new Promise((resolve) => setTimeout(resolve)));
      expect(refusals, "the page did something the Caddyfile's Content-Security-Policy refuses").toEqual([]);
    },
    { auto: true },
  ],
});
