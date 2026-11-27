export interface FindOptions {
  readonly matchCase: boolean;
  readonly wholeWords: boolean;
}

export interface Match {
  readonly start: number;
  readonly end: number;
}

const KEYBOARD: readonly { readonly typed: string; readonly letters: RegExp }[] = [
  { typed: " ", letters: /[\s\u00a0\u2000-\u200a\u202f\u205f\u3000]/u },
  { typed: "'", letters: /[\u2018\u2019\u201a\u201b\u2032\u02bc]/u },
  { typed: '"', letters: /[\u201c-\u201f\u2033]/u },
  { typed: "-", letters: /[\u2010-\u2015\u2212]/u },
];

const UNSEEN = /[\u00ad\u200b\u2060\ufeff]/u;

const LIGATURE = /[\ufb00-\ufb06]/u;

const WORD_LETTER = /[\p{L}\p{M}\p{N}_]/u;

const AS_TYPED = /^[\x20-\x7e]*$/;

function typedAs(letter: string): string {
  const typed = KEYBOARD.find(({ letters }) => letters.test(letter))?.typed;
  if (typed !== undefined) return typed;
  if (UNSEEN.test(letter)) return "";
  if (LIGATURE.test(letter)) return letter.normalize("NFKC");
  return letter;
}

function readAs(letter: string, { matchCase }: Pick<FindOptions, "matchCase">): string {
  const read = typedAs(letter);
  return matchCase ? read : read.toLowerCase();
}

/** Why: a no-break space a page set stays one, and a font that draws only that one draws the change. */
function spelledAs(found: string, replacement: string): string {
  let spelled = replacement;
  for (const { typed, letters } of KEYBOARD) {
    const lettersOfKind = new Set([...found].filter((letter) => letters.test(letter)));
    const [only] = lettersOfKind;
    if (lettersOfKind.size === 1 && only !== undefined && only !== typed) spelled = spelled.replaceAll(typed, only);
  }
  return spelled;
}

type LetterAt = (unit: number) => number | undefined;

function readOf(
  text: string,
  options: Pick<FindOptions, "matchCase">,
): { read: string; startOf: LetterAt; endOf: LetterAt } {
  if (AS_TYPED.test(text)) {
    const inText = (unit: number) => unit >= 0 && unit < text.length;
    return {
      read: options.matchCase ? text : text.toLowerCase(),
      startOf: (unit) => (inText(unit) ? unit : undefined),
      endOf: (unit) => (inText(unit) ? unit + 1 : undefined),
    };
  }
  let read = "";
  const letterStart: number[] = [];
  const letterEnd: number[] = [];
  let at = 0;
  for (const letter of text) {
    const piece = readAs(letter, options);
    read += piece;
    for (let unit = 0; unit < piece.length; unit++) {
      letterStart.push(at);
      letterEnd.push(at + letter.length);
    }
    at += letter.length;
  }
  return { read, startOf: (unit) => letterStart[unit], endOf: (unit) => letterEnd[unit] };
}

export function matchesIn(text: string, searched: string, options: FindOptions): Match[] {
  const wanted = readOf(searched, options).read;
  if (wanted === "") return [];
  const { read, startOf, endOf } = readOf(text, options);
  const matches: Match[] = [];
  let readAt = read.indexOf(wanted);
  while (readAt !== -1) {
    const readEnd = readAt + wanted.length;
    // Whole letters of the text, never half a ligature; and with Whole words, no letter either side.
    const wholeLetters = startOf(readAt - 1) !== startOf(readAt) && startOf(readEnd) !== startOf(readEnd - 1);
    const apart =
      !options.wholeWords || !(WORD_LETTER.test(read[readAt - 1] ?? "") || WORD_LETTER.test(read[readEnd] ?? ""));
    const start = startOf(readAt);
    const end = endOf(readEnd - 1);
    if (wholeLetters && apart && start !== undefined && end !== undefined) {
      matches.push({ start, end });
      readAt = read.indexOf(wanted, readEnd);
    } else {
      readAt = read.indexOf(wanted, readAt + 1);
    }
  }
  return matches;
}

export function replacedIn(text: string, matches: readonly Match[], replacement: string): string {
  let out = "";
  let kept = 0;
  for (const { start, end } of matches) {
    out += text.slice(kept, start) + spelledAs(text.slice(start, end), replacement);
    kept = end;
  }
  return out + text.slice(kept);
}
