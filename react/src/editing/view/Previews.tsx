import { Fragment } from "react";
import type { FitRules, FontInfo, PageInfo, ReplaceRuns, SpanInfo, Strategy } from "../../api/types";
import { previewFaceOf } from "../faces";
import { settingOf } from "../fit";
import { type Line, lineDrawnAs } from "../lines";
import { newTextFaceOf } from "../newText";
import type { PageState } from "../page";
import { differing } from "../project";
import { redactedBoxes } from "../redaction";
import { isUnderStrip, stripBoxOf } from "../redrawRegions";
import type { Strip } from "../render";
import { fontOf } from "../spanFonts";
import { boxOf, cropOf, newTextBoxIn, newTextStyleOf, paddedBoxOf, previewStyleOf } from "./geometry";
import styles from "./Previews.module.css";

export function Settles({ page, info }: { page: PageState; info: PageInfo }) {
  const { disagreement } = page;
  if (disagreement === undefined) return null;
  const spans = page.spans
    .filter((span) => disagreement.spanIds.has(span.id))
    .map((span) => ({ key: span.id, box: span.bbox }));
  const texts = (page.edits?.newTexts ?? [])
    .filter((text) => disagreement.newTextIds.has(text.newTextId))
    .map((text) => ({ key: `text ${text.newTextId}`, box: newTextBoxIn(page.layout, text.edit) }));
  return [...spans, ...texts].map(({ key, box }) => (
    <span
      key={`${key}:${disagreement.count}`}
      className={styles.settle}
      aria-hidden="true"
      data-settle=""
      style={boxOf(box, info)}
    />
  ));
}

export function NewTextPreviews({
  page,
  info,
  strips,
}: {
  page: PageState;
  info: PageInfo;
  strips: readonly Strip[] | undefined;
}) {
  const now = page.edits?.newTexts ?? [];
  const drawn = page.drawnFrom?.newTexts ?? [];
  const drawnIds = new Set(drawn.map((text) => text.id));
  const nowIds = new Set(now.map((text) => text.id));
  const covered = drawn.filter(
    (text) => !nowIds.has(text.id) && isUnderStrip(newTextBoxIn(page.layout, text.edit), { strips, info }),
  );
  const previewed = now.filter((text) => !drawnIds.has(text.id) && text.newTextId !== page.newTextTypedAgain);
  return (
    <>
      {covered.map((text) => (
        <Original
          key={`was ${text.id}`}
          box={paddedBoxOf(newTextBoxIn(page.layout, text.edit), info)}
          info={info}
          src={page.src}
        />
      ))}
      {previewed.map((text) => (
        <span
          key={text.newTextId}
          className={styles.newTextPreview}
          aria-hidden="true"
          data-preview=""
          style={newTextStyleOf(text.edit, info, newTextFaceOf(page.layout, text.edit))}
        >
          {text.edit.text}
        </span>
      ))}
    </>
  );
}

export function Previews({
  page,
  info,
  strips,
}: {
  page: PageState;
  info: PageInfo;
  strips: readonly Strip[] | undefined;
}) {
  const lineOf = (spanId: string) => page.layout.lines.get(spanId);
  const previews = differing(page.edits, page.drawnFrom).filter(
    ({ span }) => (lineOf(span.id)?.id ?? span.id) !== page.typingIn,
  );
  const lines = new Map(previews.flatMap(({ span, lineEdit }) => (lineEdit ? [[lineOf(span.id)?.id, lineEdit]] : [])));
  const alone = previews.filter(({ lineEdit }) => lineEdit === undefined);
  const lineDrawings = [...lines].flatMap(([lineId, lineEdit]) => {
    const line = lineId === undefined ? undefined : lineOf(lineId);
    if (line === undefined) return [];
    return [<LinePreview key={line.id} line={line} lineEdit={lineEdit} page={page} info={info} strips={strips} />];
  });
  const spanDrawings = alone.map(({ span, text, redacted, strategy }) => {
    if (redacted === "whole") {
      return (
        <span
          key={span.id}
          className={styles.preview}
          aria-hidden="true"
          data-preview=""
          style={boxOf(span.bbox, info)}
        />
      );
    }
    if (redacted !== null) {
      const glyphs = fontOf(page.fonts, span)?.glyphs;
      return (
        <Fragment key={span.id}>
          {isUnderStrip(span.bbox, { strips, info }) && (
            <Original box={stripBoxOf(span, info)} info={info} src={page.src} />
          )}
          {redactedBoxes(span, { ranges: redacted, glyphs, redaction: page.redactions[span.id] }).map((box, index) => (
            <span
              key={redacted[index]?.start}
              className={styles.preview}
              aria-hidden="true"
              data-preview=""
              style={boxOf(box, info)}
            />
          ))}
        </Fragment>
      );
    }
    if (text === span.text) {
      return isUnderStrip(span.bbox, { strips, info }) ? (
        <Original key={span.id} box={stripBoxOf(span, info)} info={info} src={page.src} />
      ) : null;
    }
    return (
      <Preview
        key={span.id}
        span={span}
        info={info}
        drawn={{ text, strategy, font: fontOf(page.fonts, span), rules: page.rules }}
      />
    );
  });
  return [...lineDrawings, ...spanDrawings];
}

function LinePreview({
  line,
  lineEdit,
  page,
  info,
  strips,
}: {
  line: Line;
  lineEdit: ReplaceRuns;
  page: PageState;
  info: PageInfo;
  strips: readonly Strip[] | undefined;
}) {
  const texts = lineEdit.runs.map((run) => run.text);
  const drawn = lineDrawnAs(line, { texts, fonts: page.fonts, asked: lineEdit.strategy, rules: page.rules });
  if (drawn === null) return null;
  const redrawn = drawn.filter((run) => run.redrawn);
  return (
    <>
      {drawn.map(({ span, redrawn }) => {
        if (!redrawn && !isUnderStrip(span.bbox, { strips, info })) return null;
        if (!redrawn) return <Original key={span.id} box={stripBoxOf(span, info)} info={info} src={page.src} />;
        return <span key={span.id} className={styles.preview} aria-hidden="true" style={boxOf(span.bbox, info)} />;
      })}
      {redrawn.map(({ span, moved, text, scale }) => {
        const font = fontOf(page.fonts, span);
        if (font === undefined) return null;
        const face = previewFaceOf(span, { font, text });
        return (
          <span
            key={`words ${span.id}`}
            className={styles.preview}
            aria-hidden="true"
            data-preview=""
            style={previewStyleOf(moved, info, { face, scale })}
          >
            {text}
          </span>
        );
      })}
    </>
  );
}

function Original({
  box,
  info,
  src,
}: {
  box: { x0: number; y0: number; x1: number; y1: number };
  info: PageInfo;
  src: string;
}) {
  return (
    <span className={styles.original} aria-hidden="true" data-original="" style={boxOf(box, info)}>
      <img src={src} alt="" decoding="async" style={cropOf(box, info)} />
    </span>
  );
}

function Preview({
  span,
  info,
  drawn: { text, strategy, font, rules },
}: {
  span: SpanInfo;
  info: PageInfo;
  drawn: { text: string; strategy: Strategy | undefined; font: FontInfo | undefined; rules: FitRules };
}) {
  if (font === undefined) return null;
  const face = previewFaceOf(span, { font, text });
  const setting = settingOf(span, { font, text, asked: strategy, rules });
  return (
    <span
      className={styles.preview}
      aria-hidden="true"
      data-preview=""
      style={previewStyleOf(span, info, { face, scale: setting })}
    >
      {text}
    </span>
  );
}
