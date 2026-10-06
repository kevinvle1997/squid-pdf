import { describe, expect, test } from "vitest";
import { counted, fill } from "./words";

describe("filling the server's sentences", () => {
  test("missing letters are joined with 'or', as the server says them", () => {
    const sentence = "no {chars} in this font, so the line is drawn in {font}";
    expect(fill(sentence, { chars: ["é", "ö"], font: "Liberation Serif Regular" })).toBe(
      "no é or ö in this font, so the line is drawn in Liberation Serif Regular",
    );
  });

  test("a width is said to one decimal place", () => {
    expect(fill("{delta_pt} pt too long", { delta_pt: 4.36 })).toBe("4.4 pt too long");
    // A half rounds up, as the server writes it (core/app/words).
    expect(fill("{delta_pt} pt too long", { delta_pt: 2.25 })).toBe("2.3 pt too long");
    expect(fill("{delta_pt} pt too long", { delta_pt: 1.45 })).toBe("1.5 pt too long");
  });

  test("a placeholder with no fact is left as it is", () => {
    expect(fill("{delta_pt} pt too long", {})).toBe("{delta_pt} pt too long");
  });

  test("a count takes its plural from the language's rules, not an added s", () => {
    expect(counted(1, { one: "change", other: "changes" })).toBe("1 change");
    expect(counted(2, { one: "change", other: "changes" })).toBe("2 changes");
  });
});
