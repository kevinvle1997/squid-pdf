// Every end to end test fails on anything the Content-Security-Policy refuses. The preview
// sends the Caddyfile's policy (vite.config.ts), so what's refused here is refused in production.
import { test as base, expect } from "@playwright/test";

/** One thing the policy refused, as the failure names it. */
interface Refusal {
  directive: string;
  blocked: string;
  source: string;
}

export const test = base.extend<{ policyKept: undefined }>({
  policyKept: [
    async ({ page }, use) => {
      const refusals: Refusal[] = [];
      // A binding, not a list on the page: it outlives a navigation, and the page closing.
      await page.exposeFunction("reportRefusal", (refusal: Refusal) => {
        refusals.push(refusal);
      });
      // The browser's own event, the same in Chromium and WebKit, where console text isn't.
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
      // A round trip through the page, so a refusal its last step raised has been reported.
      if (!page.isClosed()) await page.evaluate(() => new Promise((resolve) => setTimeout(resolve)));
      expect(refusals, "the page did something the Caddyfile's Content-Security-Policy refuses").toEqual([]);
    },
    { auto: true },
  ],
});
