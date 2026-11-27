import type { FitInfo, FontInfo, RedactionInfo, Sentences, SpanInfo } from "../api/types";
import type { SpanReading } from "./project";
import { type FontsByObject, fontOf } from "./spanFonts";
import { liveVerdictOf, type Verdict, verdictOf } from "./verdicts";
import { fill } from "./words";

export interface Note {
  readonly warn: boolean;
  readonly sentence: string;
  readonly why: string | null;
}

export interface Look {
  readonly formField: boolean;
  readonly changed: boolean;
  readonly trouble: boolean;
  readonly redacted: boolean;
  readonly wordsRedacted: boolean;
  readonly unredacted: boolean;
  readonly note: Note | null;
}

interface MarkedSpan {
  readonly span: SpanInfo;
  readonly font: FontInfo | undefined;
  readonly reading: SpanReading | undefined;
  readonly fit: FitInfo | undefined;
  readonly redaction: RedactionInfo | undefined;
  readonly sentences: Sentences;
}

export function lookOf({ span, font, reading, fit, redaction, sentences }: MarkedSpan): Look {
  const redacted = reading?.redacted ?? null;
  const changed = reading?.replaced ?? false;
  // A fit counts only while the span is changed: put back, it may linger until its page is drawn again.
  const trouble = changed ? fit?.message : null;
  const looks = {
    formField: span.form_field,
    changed,
    trouble: Boolean(trouble),
    redacted: redacted === "whole",
    wordsRedacted: redacted !== null && redacted !== "whole",
    unredacted: redacted !== null && redaction?.verified === false,
  };
  if (redacted !== null) return { ...looks, note: redactionNote(redaction) };
  if (trouble) return { ...looks, note: { warn: true, sentence: trouble, why: null } };
  if (!changed && !span.form_field) return { ...looks, note: null };
  const live = () => liveVerdictOf(span, { font, text: reading?.text ?? span.text });
  return { ...looks, note: fidelityNote(verdictOf(span, { reading, fit, live }), { span, font, sentences }) };
}

export function editNote(
  span: SpanInfo,
  {
    verdict,
    font,
    sentences,
    muted,
  }: { verdict: Verdict; font: FontInfo | undefined; sentences: Sentences; muted: boolean },
): Note | null {
  if (muted || span.form_field) return null;
  return fidelityNote(verdict, { span, font, sentences });
}

function redactionNote(redaction: RedactionInfo | undefined): Note | null {
  if (redaction?.message == null) return null;
  return { warn: true, sentence: redaction.message, why: null };
}

function approximateSentence(why: NonNullable<SpanInfo["why"]>, sentences: Sentences): string {
  return fill(sentences.approximate[why.code] ?? "", why.params);
}

function fidelityNote(
  verdict: Verdict,
  { span, font, sentences }: Pick<MarkedSpan, "span" | "font" | "sentences">,
): Note | null {
  if (span.form_field) return { warn: true, sentence: sentences.form_field_not_edited, why: null };
  if (verdict.why !== null) return { warn: true, sentence: approximateSentence(verdict.why, sentences), why: null };
  if (verdict.substitute === null) return null;
  const substituteSentence = verdict.same_widths ? sentences.substitute_same_widths : sentences.substitute;
  // The font's reason is its first span's: it holds here only if a similar face draws that one too.
  const why = font?.substitute == null ? null : font.why;
  // Warned whatever the widths: the file's own font doesn't draw it.
  return { warn: true, sentence: fill(substituteSentence, { font: verdict.substitute }), why };
}

/** A line to mark: its spans, and what each reads now, the server's verdicts on them, and the fonts they're in. */
interface MarkedLine {
  readonly spans: readonly SpanInfo[];
  readonly fonts: FontsByObject;
  readonly readings: ReadonlyMap<string, SpanReading>; // only the spans an edit changed
  readonly fits: Readonly<Record<string, FitInfo>>; // by span: the server's verdict on its run of the line's edit
  readonly redactions: Readonly<Record<string, RedactionInfo>>; // by span: the server's verdict on its redaction
  readonly sentences: Sentences;
}

/**
 * What a line's one mark shows: each line its spans' marks would draw, and the first note among them, in the line's
 * order. Redacted whole only when every span is; some words redacted when some are.
 */
export function lineLookOf({ spans, fonts, readings, fits, redactions, sentences }: MarkedLine): Look {
  const looks = spans.map((span) =>
    lookOf({
      span,
      font: fontOf(fonts, span),
      reading: readings.get(span.id),
      fit: fits[span.id],
      redaction: redactions[span.id],
      sentences,
    }),
  );
  const any = (part: (look: Look) => boolean) => looks.some(part);
  const redacted = looks.length > 0 && looks.every((look) => look.redacted);
  return {
    formField: any((look) => look.formField),
    changed: any((look) => look.changed),
    trouble: any((look) => look.trouble),
    redacted,
    wordsRedacted: !redacted && any((look) => look.redacted || look.wordsRedacted),
    unredacted: any((look) => look.unredacted),
    note: looks.find((look) => look.note !== null)?.note ?? null,
  };
}
