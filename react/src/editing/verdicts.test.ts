import { describe, expect, test } from "vitest";
import shared from "../../../tests/editing/fit_cases.json";
import type { SpanInfo } from "../api/types";
import { aFit, aFont, aSpan, halfWideGlyphs } from "../fixtures";
import { liveVerdictOf, shortWarningOf, verdictOf } from "./verdicts";

describe("the one verdict", () => {
  // Its copy of the font lacks "€", so judged on its own words it's a similar font's.
  const lacking = aSpan({
    id: "l",
    text: "Total 14 €",
    fidelity: "substitute",
    substitute: "Liberation Serif Regular",
  });

  test("the one verdict is the span's untouched, its render's once that lands, else the one worked out as it's typed", () => {
    const live = () => ({ fidelity: "approximate" as const, why: null, substitute: null, same_widths: false });
    const fit = aFit({ fidelity: "substitute", substitute: "Liberation Serif Regular" });
    expect(verdictOf(lacking, { reading: undefined, fit, live })).toBe(lacking);
    expect(verdictOf(lacking, { reading: { replaced: false }, fit, live })).toBe(lacking);
    expect(verdictOf(lacking, { reading: { replaced: true }, fit, live })).toBe(fit);
    expect(verdictOf(lacking, { reading: { replaced: true }, fit: undefined, live })).toEqual(live());
    expect(shortWarningOf(aFit())).toBeNull();
    expect(shortWarningOf(fit)).toBe("a similar font");
  });
});

const FIDELITIES: readonly SpanInfo["fidelity"][] = ["exact", "approximate", "substitute"];
const REASONS: readonly NonNullable<SpanInfo["why"]>["code"][] = [
  "turned_text",
  "right_to_left_text",
  "spaced_text",
  "undrawable_letters",
];

/** `said`, as the one of `known` it names: a case naming one the browser doesn't know fails here. */
function oneOf<T extends string>(known: readonly T[], said: string): T {
  const found = known.find((each) => each === said);
  if (found === undefined) throw new Error(`${said} is none of ${known.join(", ")}`);
  return found;
}

describe("an edit's verdict, worked out as it's typed", () => {
  const SERIF = "Liberation Serif Regular"; // the face the server's cases store a copy of, and its look-alike

  test.each(shared.verdicts)("$what", ({ text, lacking, stored, span: judged, typed, fidelity }) => {
    // The span as the server sends it, and its font's letters: every one the cases type but those its copy lacks.
    const substitute = judged.fidelity === "substitute" ? SERIF : null;
    const span = aSpan({
      id: "case",
      text,
      fidelity: oneOf(FIDELITIES, judged.fidelity),
      why: judged.why === null ? null : { code: oneOf(REASONS, judged.why), params: {} },
      substitute,
      in_file: stored,
    });
    const letters = [...new Set(text + typed)].filter((letter) => !lacking.includes(letter)).join("");
    const font = aFont("LiberationSerif", { substitute, look_alike: SERIF, glyphs: halfWideGlyphs(letters) });
    expect(liveVerdictOf(span, { font, text: typed }).fidelity).toBe(fidelity);
  });
});
