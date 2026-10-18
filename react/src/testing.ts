// What view tests read off the page as the browser draws it, here once a third test needs it: the
// browser's tests/helpers.py. Only tests import it (layers.test.ts); the API's shapes are fixtures.ts's.

/** The colour `token` resolves to outside any sheet, read off a probe taken out again. */
export function colorOf(token: string): string {
  const probe = document.body.appendChild(document.createElement("span"));
  probe.style.color = `var(${token})`;
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}
