// What a page's spans are drawn as when it's near: a mark over each, the field where one is typed in,
// the note their marks share, find's tint, and the prompt before a redaction is undone.
import { useId, useMemo, useState, useSyncExternalStore } from "react";
import { useFocusVisible } from "react-aria";
import type { PageInfo } from "../../api/types";
import { Tooltip } from "../../ui/Tooltip";
import { matchesAcross } from "../find";
import { lookOf } from "../marks";
import type { Match } from "../matching";
import { type PageState, spanIn } from "../page";
import type { SpanReading } from "../project";
import { EditField } from "./EditField";
import { boxOf } from "./geometry";
import styles from "./Marks.module.css";
import { createNotes, type Notes } from "./notes";
import { SpanMark } from "./SpanMark";
import { UndoRedaction } from "./UndoRedaction";

/** A mark over every span, or the field where one is being typed into, and the one note they share. */
export function Marks({ page, info }: { page: PageState; info: PageInfo }) {
  const [notes] = useState(createNotes);
  const noteId = useId();
  const readings = useMemo(() => new Map(page.edits?.spans.map((reading) => [reading.span.id, reading])), [page.edits]);
  return (
    <>
      {page.matches !== undefined && <Found page={page} info={info} readings={readings} matches={page.matches} />}
      {page.spans.map((span) =>
        span.id === page.typingIn ? (
          <EditField key={span.id} span={span} info={info} />
        ) : (
          <SpanMark
            key={span.id}
            span={span}
            info={info}
            reading={readings.get(span.id)}
            font={page.fonts.get(span.font)}
            fit={page.fits[span.id]}
            redaction={page.redactions[span.id]}
            copy={page.copy}
            focusTo={page.focusTo?.spanId === span.id ? page.focusTo : null}
            notes={notes}
            noteId={noteId}
          />
        ),
      )}
      <PageNote notes={notes} id={noteId} page={page} readings={readings} />
      {page.unredacting !== null && (
        <UndoRedactionAt spanId={page.unredacting} page={page} info={info} readings={readings} />
      )}
    </>
  );
}

/** The words find found, tinted as the change they're about to be: only those words, not their whole line. */
function Found({
  page,
  info,
  readings,
  matches,
}: {
  page: PageState;
  info: PageInfo;
  readings: ReadonlyMap<string, SpanReading>;
  matches: ReadonlyMap<string, readonly Match[]>;
}) {
  return page.spans.flatMap((span) => {
    const inSpan = matches.get(span.id);
    if (inSpan === undefined || span.id === page.typingIn) return [];
    const text = readings.get(span.id)?.text ?? span.text;
    const glyphs = page.fonts.get(span.font)?.glyphs ?? {};
    return matchesAcross(span, { text, matches: inSpan, glyphs }).map(({ x0, x1 }) => (
      <span
        key={`${span.id}:${x0}`}
        className={styles.found}
        aria-hidden="true"
        data-found=""
        style={boxOf({ ...span.bbox, x0, x1 }, info)}
      />
    ));
  });
}

/** The note of the span the page's marks say, over its mark; kept while it closes, for its animation. */
function PageNote({
  notes,
  id,
  page,
  readings,
}: {
  notes: Notes;
  id: string;
  page: PageState;
  readings: ReadonlyMap<string, SpanReading>;
}) {
  const { shown, last } = useSyncExternalStore(notes.store.subscribe, notes.store.get);
  // Focus shows the note only when it's the keyboard's: a click's focus leaves it to the click.
  const { isFocusVisible } = useFocusVisible();
  const span = last === null ? undefined : spanIn(page, last.spanId);
  if (last === null || span === undefined) return null;
  const { note } = lookOf({
    span,
    font: page.fonts.get(span.font),
    reading: readings.get(span.id),
    fit: page.fits[span.id],
    redaction: page.redactions[span.id],
    copy: page.copy,
  });
  if (note === null) return null;
  return (
    <Tooltip
      key={last.spanId}
      id={id}
      triggerRef={{ current: last.anchor }}
      isOpen={shown !== null && (shown.via !== "focus" || isFocusVisible)}
      onOpenChange={(isOpen) => isOpen || notes.hide(last.spanId)}
      heading={note.said}
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
  return <UndoRedaction span={span} info={info} copy={page.copy} />;
}
