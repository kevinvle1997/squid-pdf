import { useId, useMemo, useState, useSyncExternalStore } from "react";
import { useFocusVisible } from "react-aria";
import type { PageInfo } from "../../api/types";
import { classes } from "../../ui/classes";
import { Tooltip } from "../../ui/Tooltip";
import { tintsOn } from "../find";
import { lineLookOf, lookOf } from "../marks";
import type { Match } from "../matching";
import { shownAmong } from "../mismatches";
import { shownNewTextsAmong } from "../newText";
import { inPageOrder, type PageState, placedOnPage, spanIn } from "../page";
import type { Redacted, SpanReading } from "../project";
import { undoQuestionOf } from "../redaction";
import { landedFits, type PageFits } from "../render";
import { fontOf } from "../spanFonts";
import { EditField } from "./EditField";
import { boxOf, markBoxOf, newTextBoxIn } from "./geometry";
import { LineMark } from "./LineMark";
import styles from "./Marks.module.css";
import { NewTextField } from "./NewTextField";
import { RestingNewText } from "./NewTextMark";
import { createNotes, isShowing, type Notes } from "./notes";
import { RunsField } from "./RunsField";
import { SpanMark } from "./SpanMark";
import { UndoRedaction } from "./UndoRedaction";

export function Marks({ page, info }: { page: PageState; info: PageInfo }) {
  const [notes] = useState(createNotes);
  const noteId = useId();
  const readings = useMemo(() => new Map(page.edits?.spans.map((reading) => [reading.span.id, reading])), [page.edits]);
  const fits = useMemo(
    () => landedFits(page.fits, { now: page.edits, drawnFrom: page.drawnFrom }),
    [page.fits, page.edits, page.drawnFrom],
  );
  return (
    <>
      {page.matches !== undefined && <Found page={page} info={info} matches={page.matches} />}
      <Shown page={page} info={info} readings={readings} fits={fits} />
      {inPageOrder(page.spans, placedOnPage(page)).map(({ span, placed }) => {
        if (placed === "field") return <NewTextField key="field" info={info} />;
        if (placed !== undefined) {
          return <RestingNewText key={placed.newTextId} text={placed} page={page} info={info} notes={notes} />;
        }
        const line = page.layout.lines.get(span.id);
        // A line of several runs is one mark, or one field, where its first span stands.
        if (line !== undefined) {
          if (line.id !== span.id) return null;
          if (line.id === page.typingIn) return <RunsField key={line.id} line={line} info={info} />;
          const sentTo = page.focusTo;
          const focusTo = sentTo !== null && line.spans.some(({ id }) => id === sentTo.spanId) ? sentTo : null;
          return (
            <LineMark
              key={line.id}
              line={line}
              info={info}
              readings={readings}
              fonts={page.fonts}
              fits={fits}
              redactions={page.redactions}
              sentences={page.sentences}
              focusTo={focusTo}
              notes={notes}
              noteId={noteId}
            />
          );
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

function Shown({
  page,
  info,
  readings,
  fits,
}: {
  page: PageState;
  info: PageInfo;
  readings: ReadonlyMap<string, SpanReading>;
  fits: PageFits;
}) {
  const { shownMismatches: shown, changed, fonts, layout } = page;
  if (shown.size === 0) return null;
  const spans = shownAmong(page.spans, { shown, changed, readings, fits, fonts })
    // Being typed into, alone or in its line: the field says how it won't match.
    .filter((span) => (layout.lines.get(span.id)?.id ?? span.id) !== page.typingIn)
    .map((span) => {
      const replaced = readings.get(span.id)?.replaced ?? false;
      return { key: span.id, box: markBoxOf(span, { replaced, fit: fits[span.id] }) };
    });
  const texts = shownNewTextsAmong(page.edits?.newTexts ?? [], { shown, layout, fits: page.newTextFits })
    .filter((text) => text.newTextId !== page.newTextTypedAgain)
    .map((text) => ({ key: `text ${text.newTextId}`, box: newTextBoxIn(layout, text.edit) }));
  return [...spans, ...texts].map(({ key, box }) => (
    <span key={key} className={styles.shown} aria-hidden="true" data-shown="" data-warned="" style={boxOf(box, info)} />
  ));
}

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
  fits: PageFits;
}) {
  const { shown, last } = useSyncExternalStore(notes.store.subscribe, notes.store.get);
  const { isFocusVisible } = useFocusVisible();
  const span = last === null ? undefined : spanIn(page, last.markId);
  if (last === null || span === undefined) return null;
  const line = page.layout.lines.get(span.id);
  const { sentences, redactions, fonts } = page;
  const { note } =
    line === undefined
      ? lookOf({
          span,
          font: fontOf(fonts, span),
          reading: readings.get(span.id),
          fit: fits[span.id],
          redaction: redactions[span.id],
          sentences,
        })
      : lineLookOf({ spans: line.spans, fonts, readings, fits, redactions, sentences });
  if (note === null) return null;
  return (
    <Tooltip
      key={last.markId}
      id={id}
      triggerRef={{ current: last.anchor }}
      isOpen={isShowing(shown, isFocusVisible)}
      onOpenChange={(isOpen) => isOpen || notes.hide(last.markId)}
      onHoverChange={(isHovered) => (isHovered ? notes.keep(last.markId) : notes.hideSoon(last.markId))}
      heading={note.sentence}
      warn={note.warn}
    >
      {note.why}
    </Tooltip>
  );
}

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
  if (span === undefined) return null;
  // A line asks for every span of it redacted: of all of one, as a span redacted whole asks.
  const spans = page.layout.lines.get(spanId)?.spans ?? [span];
  const redactions = spans.map(({ id }) => readings.get(id)?.redacted ?? null);
  const whole = redactions.includes("whole");
  const redacted: Redacted | undefined = whole ? "whole" : (redactions.find((each) => each !== null) ?? undefined);
  if (redacted === undefined) return null;
  return <UndoRedaction span={span} info={info} question={undoQuestionOf(page.sentences, redacted)} />;
}
