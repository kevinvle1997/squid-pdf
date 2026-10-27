// What a page's spans are drawn as when it's near: a mark over each, the field, the note their marks share,
// find's tint, the mismatches the bar's list shows, and the prompt before a redaction is undone.
import { useEffect, useId, useMemo, useReducer, useState, useSyncExternalStore } from "react";
import { useFocusVisible } from "react-aria";
import type { PageInfo } from "../../api/types";
import { classes } from "../../ui/classes";
import { Tooltip } from "../../ui/Tooltip";
import { INSERT_ASCENT_EM } from "../constants";
import { addFaces } from "../faces";
import { tintsOn } from "../find";
import { lookOf } from "../marks";
import type { Match } from "../matching";
import { shownAmong } from "../mismatches";
import { shownTextsAmong, textFaceOf } from "../newText";
import { inPageOrder, type PageState, spanIn } from "../page";
import type { InsertReading, SpanReading } from "../project";
import { landedFits, type PageFits } from "../render";
import { fontOf } from "../spanFonts";
import { EditField } from "./EditField";
import { boxOf, markBoxOf, textBoxIn, textWidthIn } from "./geometry";
import styles from "./Marks.module.css";
import { NewTextField } from "./NewTextField";
import { createNotes, type Notes } from "./notes";
import { SpanMark } from "./SpanMark";
import { TextMark } from "./TextMark";
import { UndoRedaction } from "./UndoRedaction";

/** A mark over every span, or the field where one is being typed into, and the one note they share. */
export function Marks({ page, info }: { page: PageState; info: PageInfo }) {
  const [notes] = useState(createNotes);
  const noteId = useId();
  const readings = useMemo(() => new Map(page.edits?.spans.map((reading) => [reading.span.id, reading])), [page.edits]);
  // A fit for words the span no longer reads says nothing of these: until its render lands, they're worked out here.
  const fits = useMemo(
    () => landedFits(page.fits, { now: page.edits, drawnFrom: page.drawnFrom }),
    [page.fits, page.edits, page.drawnFrom],
  );
  return (
    <>
      {page.matches !== undefined && <Found page={page} info={info} matches={page.matches} />}
      <Shown page={page} info={info} readings={readings} fits={fits} />
      {inPageOrder(page.spans, placedOn(page)).map(({ span, placed }) => {
        if (placed === "field") return <NewTextField key="field" info={info} />;
        if (placed !== undefined) {
          return <RestingText key={placed.textId} text={placed} page={page} info={info} />;
        }
        return span.id === page.typingIn ? (
          <EditField key={span.id} span={span} info={info} />
        ) : (
          <SpanMark
            key={span.id}
            span={span}
            info={info}
            reading={readings.get(span.id)}
            font={fontOf(page.fonts, span)}
            fit={fits[span.id]}
            redaction={page.redactions[span.id]}
            sentences={page.sentences}
            focusTo={page.focusTo?.spanId === span.id ? page.focusTo : null}
            notes={notes}
            noteId={noteId}
          />
        );
      })}
      <PageNote notes={notes} id={noteId} page={page} readings={readings} fits={fits} />
      {page.unredacting !== null && (
        <UndoRedactionAt spanId={page.unredacting} page={page} info={info} readings={readings} />
      )}
    </>
  );
}

/** What's put on the page among its spans: each piece of new text at rest, and the field new text is typed in. */
function placedOn(page: PageState): { item: InsertReading | "field"; at: readonly [number, number] }[] {
  const resting = (page.edits?.inserts ?? []).filter((text) => text.textId !== page.writingAgain);
  const texts = resting.map((text) => {
    const [x, y] = text.edit.origin;
    return { item: text, at: [x, y - text.edit.size * INSERT_ASCENT_EM] as const };
  });
  return page.writing === null ? texts : [...texts, { item: "field" as const, at: page.writing }];
}

/** New text at rest, measured in the face that previews it, again as that face loads. */
function RestingText({ text, page, info }: { text: InsertReading; page: PageState; info: PageInfo }) {
  const [, measureAgain] = useReducer((times: number) => times + 1, 0);
  const face = textFaceOf(page.layout, text.edit);
  useEffect(() => {
    addFaces([face]);
    document.fonts.addEventListener("loadingdone", measureAgain);
    return () => document.fonts.removeEventListener("loadingdone", measureAgain);
  }, [face]);
  const widthPt = textWidthIn(page.layout, text.edit);
  return <TextMark text={text} info={info} face={face} widthPt={widthPt} takesFocus={page.focusText === text.textId} />;
}

/** Where an edit won't match, in the rows of the bar's list whose eye is on: each place, as long as it's drawn. */
function Shown({
  page,
  info,
  readings,
  fits,
}: {
  page: PageState;
  info: PageInfo;
  readings: ReadonlyMap<string, SpanReading>;
  fits: PageFits; // each as its span reads now, once drawn so
}) {
  const { shownMismatches: shown, changed, fonts, layout } = page;
  if (shown.size === 0) return null;
  const spans = shownAmong(page.spans, { shown, changed, readings, fits, fonts })
    // Being typed into: the field says how it won't match.
    .filter((span) => span.id !== page.typingIn)
    .map((span) => {
      const replaced = readings.get(span.id)?.replaced ?? false;
      return { key: span.id, box: markBoxOf(span, { replaced, fit: fits[span.id] }) };
    });
  const texts = shownTextsAmong(page.edits?.inserts ?? [], { shown, layout, fits: page.insertFits })
    .filter((text) => text.textId !== page.writingAgain)
    .map((text) => ({ key: `text ${text.textId}`, box: textBoxIn(layout, text.edit) }));
  return [...spans, ...texts].map(({ key, box }) => (
    <span key={key} className={styles.shown} aria-hidden="true" data-shown="" data-warned="" style={boxOf(box, info)} />
  ));
}

/** The words find found, tinted as the change they're about to be: only those words, not their whole line; the place gone to ringed. */
function Found({
  page,
  info,
  matches,
}: {
  page: PageState;
  info: PageInfo;
  matches: ReadonlyMap<string, readonly Match[]>;
}) {
  return tintsOn(page, matches).map(({ span, x0, x1 }) => (
    <span
      key={`${span.id}:${x0}`}
      className={classes(styles.found, span.id === page.foundAt && styles.foundAt)}
      aria-hidden="true"
      data-found=""
      style={boxOf({ ...span.bbox, x0, x1 }, info)}
    />
  ));
}

/** The note of the span the page's marks say, over its mark; kept while it closes, for its animation. */
function PageNote({
  notes,
  id,
  page,
  readings,
  fits,
}: {
  notes: Notes;
  id: string;
  page: PageState;
  readings: ReadonlyMap<string, SpanReading>;
  fits: PageFits; // each as its span reads now, once drawn so
}) {
  const { shown, last } = useSyncExternalStore(notes.store.subscribe, notes.store.get);
  // Focus shows the note only when it's the keyboard's: a click's focus leaves it to the click.
  const { isFocusVisible } = useFocusVisible();
  const span = last === null ? undefined : spanIn(page, last.spanId);
  if (last === null || span === undefined) return null;
  const { note } = lookOf({
    span,
    font: fontOf(page.fonts, span),
    reading: readings.get(span.id),
    fit: fits[span.id],
    redaction: page.redactions[span.id],
    sentences: page.sentences,
  });
  if (note === null) return null;
  return (
    <Tooltip
      key={last.spanId}
      id={id}
      triggerRef={{ current: last.anchor }}
      isOpen={shown !== null && (shown.via !== "focus" || isFocusVisible)}
      onOpenChange={(isOpen) => isOpen || notes.hide(last.spanId)}
      heading={note.sentence}
      warn={note.warn}
    >
      {note.why}
    </Tooltip>
  );
}

/** The prompt beside a redacted span the user asked to edit, while it's still redacted. */
function UndoRedactionAt({
  spanId,
  page,
  info,
  readings,
}: {
  spanId: string;
  page: PageState;
  info: PageInfo;
  readings: ReadonlyMap<string, SpanReading>;
}) {
  const span = spanIn(page, spanId);
  if (span === undefined || !readings.get(spanId)?.redacted) return null;
  return <UndoRedaction span={span} info={info} sentences={page.sentences} />;
}
