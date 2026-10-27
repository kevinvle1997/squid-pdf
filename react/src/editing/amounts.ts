// Amounts in a span's text, read from the text alone, and written back as the page wrote them: their sign or
// code and its side, their separators and decimals, and how they wrote a negative.
import type { Extent } from "./formattedValues";

/** How the page wrote an amount: what's round its number, and how the number's digits are set. */
interface AmountFormat {
  readonly before: string; // its sign or code and the space after it, when it comes first
  readonly after: string; // the space and its sign or code, when it comes last
  readonly code: string | undefined; // its currency's code, where Intl knows one for it
  readonly group: Grouping | null; // how the page set its digits apart, if it did
  readonly decimal: string; // before the decimals
  readonly decimals: number;
  readonly negative: Negative | null; // how it wrote a negative amount, if it was one
}

/** The mark between groups of digits, and their size: three, or three then twos, as India writes 1,25,000. */
interface Grouping {
  readonly mark: string;
  readonly sizes: "thousands" | "lakhs";
}

/** The marks a negative amount takes: a minus or brackets round it, a minus after its sign, or after its number. */
interface Negative {
  readonly open: string;
  readonly afterSign: string;
  readonly afterNumber: string;
  readonly close: string;
}

export interface AmountValue extends Extent {
  readonly kind: "amount";
  readonly amount: number;
  readonly format: AmountFormat;
}

const ALONE_BEFORE = String.raw`(?<![\p{L}\p{N}])`;
const ALONE_AFTER = String.raw`(?![\p{L}\p{N}])`;
// A space between a number and its sign, or between its groups: a no-break one, a narrow one or a thin one too.
const SPACES = String.raw` \u00a0\u202f\u2009`;
const SPACE = `[${SPACES}]`;
const MINUS = String.raw`[\-\u2212]`;
const SIGN_OR_CODE = String.raw`\p{Sc}|[A-Z]{3}`;
// Three digits a group past the first, as India's twos then three, or none; then decimals. Which mark is which is read after.
const NUMBER =
  String.raw`\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?|` +
  String.raw`\d{1,3}(?:[.,'\u2019${SPACES}]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?`;
const AMOUNT = new RegExp(
  `${ALONE_BEFORE}(?<open>[(]|${MINUS})?(?:(?<lead>${SIGN_OR_CODE})(?<leadSpace>${SPACE}?))?` +
    `(?<afterSign>${MINUS})?(?<number>${NUMBER})(?<afterNumber>${MINUS}(?![\\p{Sc}\\p{N}]))?` +
    `(?:(?<trailSpace>${SPACE}?)(?<trail>${SIGN_OR_CODE}))?(?<close>\\))?${ALONE_AFTER}`,
  "gu",
);
// A dash between two amounts makes them a range, which isn't one amount to change.
const RANGE_AFTER = new RegExp(`^${SPACE}?[\\-\\u2013\\u2212]${SPACE}?(?:${SIGN_OR_CODE}|\\d)`, "u");
const RANGE_BEFORE = new RegExp(`(?:${SIGN_OR_CODE}|\\d)${SPACE}?[\\-\\u2013\\u2212]${SPACE}?$`, "u");

let currencies: ReadonlySet<string> | undefined;

/** The currency codes Intl knows. */
function currencyCodes(): ReadonlySet<string> {
  currencies ??= new Set(Intl.supportedValuesOf("currency"));
  return currencies;
}

/** Amounts with a currency's sign or code: a number alone may be a quantity or a reference. */
export function amountsIn(text: string): AmountValue[] {
  const amounts: AmountValue[] = [];
  for (const match of text.matchAll(AMOUNT)) {
    const amount = amountOf(match);
    if (amount !== undefined && !inRange(text, amount)) amounts.push(amount);
  }
  return amounts;
}

/** Whether `amount` is one end of a range, "$10-$20", a dash away from another amount. */
function inRange(text: string, amount: AmountValue): boolean {
  // A minus the amount opens with may be the range's dash.
  const open = amount.format.negative?.open ?? "";
  const before = text.slice(0, amount.start) + (open === "(" ? "" : open);
  return RANGE_AFTER.test(text.slice(amount.end)) || RANGE_BEFORE.test(before);
}

function amountOf(match: RegExpMatchArray): AmountValue | undefined {
  const parts = match.groups ?? {};
  const [lead, trail] = [parts.lead, parts.trail];
  const signOrCode = lead ?? trail;
  if ((lead === undefined) === (trail === undefined) || signOrCode === undefined) return undefined;
  if (/^[A-Z]{3}$/.test(signOrCode) && !currencyCodes().has(signOrCode)) return undefined;
  const code = currencyCodeOf(signOrCode);
  const number = numberOf(parts.number ?? "", { minorUnits: minorUnitsOf(code) });
  if (number === undefined) return undefined;
  let start = match.index ?? 0;
  let end = start + match[0].length;
  let open = parts.open ?? "";
  let close = parts.close ?? "";
  // A bracket only one side is the sentence's, not the amount's.
  if (open === "(" && close === "") [open, start] = ["", start + 1];
  if (close === ")" && open !== "(") [close, end] = ["", end - 1];
  const negative = { open, afterSign: parts.afterSign ?? "", afterNumber: parts.afterNumber ?? "", close };
  const isNegative = Object.values(negative).some((mark) => mark !== "");
  const format: AmountFormat = {
    before: lead === undefined ? "" : lead + (parts.leadSpace ?? ""),
    after: trail === undefined ? "" : (parts.trailSpace ?? "") + trail,
    code,
    group: number.group,
    decimal: number.decimal,
    decimals: number.decimals,
    negative: isNegative ? negative : null,
  };
  return { kind: "amount", amount: isNegative ? -number.value : number.value, format, start, end };
}

/** How many decimals a currency has, as Intl writes it: three for the Kuwaiti dinar, two for most. */
function minorUnitsOf(code: string | undefined): number {
  if (code === undefined) return 2;
  return (
    new Intl.NumberFormat("en", { style: "currency", currency: code }).resolvedOptions().maximumFractionDigits ?? 2
  );
}

const DECIMAL_MARKS = new Set([".", ","]);

/**
 * A number's value and how its digits are set: the last mark is its decimals', unless three digits follow it
 * as a group's, where the currency has no three decimals of its own ("KWD 1.250" is one and a quarter).
 */
function numberOf(
  written: string,
  { minorUnits }: { minorUnits: number },
): { value: number; group: Grouping | null; decimal: string; decimals: number } | undefined {
  const digits = (part: string) => part.replace(/\D/g, "");
  const marks = [...written.matchAll(/\D/g)];
  const last = marks.at(-1);
  if (last === undefined) return { value: Number(written), group: null, decimal: ".", decimals: 0 };
  const lastMark = last[0];
  const oneKind = marks.every((found) => found[0] === lastMark);
  const threeAfter = written.length - last.index - 1 === 3;
  const decimalsOfThree = marks.length === 1 && minorUnits === 3 && DECIMAL_MARKS.has(lastMark);
  // Every mark a group's: one kind, three digits after the last, or a mark decimals are never written after.
  if (oneKind && !decimalsOfThree && (threeAfter || !DECIMAL_MARKS.has(lastMark))) {
    const group = { mark: lastMark, sizes: sizesOf(written) };
    return { value: Number(digits(written)), group, decimal: lastMark === "," ? "." : ",", decimals: 0 };
  }
  const groupMarks = new Set(marks.slice(0, -1).map((found) => found[0]));
  const [mark] = groupMarks;
  if (!DECIMAL_MARKS.has(lastMark) || groupMarks.size > 1 || mark === lastMark) return undefined;
  const whole = written.slice(0, last.index);
  const fraction = written.slice(last.index + 1);
  const group = mark === undefined ? null : { mark, sizes: sizesOf(whole) };
  return { value: Number(`${digits(whole)}.${fraction}`), group, decimal: lastMark, decimals: fraction.length };
}

/** How the groups of a whole number are sized: India's twos before the last three, or threes. */
function sizesOf(whole: string): Grouping["sizes"] {
  const groups = whole.split(/\D/).slice(1, -1);
  return groups.some((group) => group.length === 2) ? "lakhs" : "thousands";
}

/** `text` with `value` changed to `amount`, written as the page wrote the amount it replaces. */
export function withAmount(text: string, value: AmountValue, amount: number): string {
  return text.slice(0, value.start) + writtenAmount(amount, value.format) + text.slice(value.end);
}

// The minus a negative takes where the page wrote none.
const A_MINUS: Negative = { open: "-", afterSign: "", afterNumber: "", close: "" };

function writtenAmount(amount: number, format: AmountFormat): string {
  const scaled = String(Math.round(Math.abs(amount) * 10 ** format.decimals)).padStart(format.decimals + 1, "0");
  const whole = scaled.slice(0, scaled.length - format.decimals);
  const fraction = scaled.slice(scaled.length - format.decimals);
  const grouped = format.group === null ? whole : groupedAs(whole, format.group);
  const number = format.decimals === 0 ? grouped : grouped + format.decimal + fraction;
  const marks = amount < 0 ? (format.negative ?? A_MINUS) : null;
  return (
    (marks?.open ?? "") +
    format.before +
    (marks?.afterSign ?? "") +
    number +
    (marks?.afterNumber ?? "") +
    format.after +
    (marks?.close ?? "")
  );
}

function groupedAs(whole: string, { mark, sizes }: Grouping): string {
  switch (sizes) {
    case "thousands":
      return whole.replace(/\B(?=(\d{3})+$)/g, mark);
    case "lakhs": {
      const lastThree = whole.slice(-3);
      const rest = whole.slice(0, -3);
      return rest === "" ? lastThree : rest.replace(/\B(?=(\d{2})+$)/g, mark) + mark + lastThree;
    }
    default:
      return sizes satisfies never;
  }
}

let codesBySign: ReadonlyMap<string, string> | undefined;

/** The code of the currency `signOrCode` is: itself, or the one whose sign it is, as Intl writes signs in English. */
function currencyCodeOf(signOrCode: string): string | undefined {
  if (currencyCodes().has(signOrCode)) return signOrCode;
  if (codesBySign === undefined) {
    const bySign = new Map<string, string>();
    for (const code of currencyCodes()) {
      const format = new Intl.NumberFormat("en", { style: "currency", currency: code });
      const sign = format.formatToParts(1).find((part) => part.type === "currency")?.value;
      if (sign !== undefined && !bySign.has(sign)) bySign.set(sign, code);
    }
    codesBySign = bySign;
  }
  return codesBySign.get(signOrCode);
}
