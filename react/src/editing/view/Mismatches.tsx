import { type ReactNode, useMemo } from "react";
import { Dialog, DialogTrigger, Heading, Popover } from "react-aria-components";
import { Button } from "../../ui/Button";
import { EyeToggle } from "../../ui/EyeToggle";
import { BELOW_BAR_PX } from "../constants";
import { changedMismatches } from "../editor";
import { readableName } from "../fonts";
import { type Mismatches, type MismatchRow, mismatchesOf } from "../mismatches";
import { toggleShownMismatches } from "../shownMismatches";
import { shallowEqual } from "../store";
import { counted } from "../words";
import { useEditor, useEditorState } from "./context";
import styles from "./Mismatches.module.css";

const PLACES = { one: "place", other: "places" };

export function MismatchCount({ changed, children }: { changed: boolean; children: ReactNode }) {
  const showing = useEditorState((state) => state.shownMismatches.size > 0);
  return (
    <DialogTrigger>
      <Button variant="quiet" className={styles.count}>
        <span>
          {children}
          {showing && <span className="vh"> (shown on the page)</span>}
        </span>
        {showing && <ShownMark />}
        <Chevron />
      </Button>
      <Popover placement="bottom end" offset={BELOW_BAR_PX} className={styles.popover}>
        <Dialog aria-label="Where edits won't match" className={styles.dialog}>
          {({ close }) => (changed ? <ChangedGroups shown={close} /> : <AtRestGroups shown={close} />)}
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

function Chevron() {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={styles.chevron}
    >
      <path d="M2.5 3.75 5 6.25l2.5-2.5" />
    </svg>
  );
}

function ShownMark() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      className={styles.chevron}
    >
      <path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" strokeLinejoin="round" />
      <circle cx="8" cy="8" r="2" />
    </svg>
  );
}

function AtRestGroups({ shown }: { shown: () => void }) {
  const mismatches = useEditorState((state) => state.layout.mismatchesAtRest);
  return <Groups mismatches={mismatches} shown={shown} />;
}

function ChangedGroups({ shown }: { shown: () => void }) {
  const { reading, drawn, layout, sentences } = useEditorState(
    (state) => ({
      reading: state.reading,
      drawn: state.drawn,
      layout: state.layout,
      sentences: state.doc.sentences,
    }),
    shallowEqual,
  );
  const mismatches = useMemo(
    () => mismatchesOf(changedMismatches({ reading, drawn, layout }), { fonts: layout.fonts, sentences }),
    [reading, drawn, layout, sentences],
  );
  return <Groups mismatches={mismatches} shown={shown} />;
}

function Groups({ mismatches, shown }: { mismatches: Mismatches; shown: () => void }) {
  return (
    <>
      {mismatches.fonts.length > 0 && (
        <section className={styles.group}>
          <Heading level={2} className={styles.heading}>
            In a similar font
          </Heading>
          <ul className={styles.list}>
            {mismatches.fonts.map((row) => (
              <li key={row.key} className={styles.row}>
                <span className={styles.name}>{readableName(row.name)}</span>
                <span className={styles.places}>{counted(row.places, PLACES)}</span>
                <ShowOnPage row={row} what={`the places in ${readableName(row.name)}`} shown={shown} />
                <span className={styles.sentence}>{row.sentence}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {mismatches.reasons.length > 0 && (
        <section className={styles.group}>
          <Heading level={2} className={styles.heading}>
            Will look different
          </Heading>
          <ul className={styles.list}>
            {mismatches.reasons.map((row) => (
              <li key={row.key} className={styles.row}>
                <span className={styles.sentence}>{row.sentence}</span>
                <span className={styles.places}>{counted(row.places, PLACES)}</span>
                <ShowOnPage row={row} what={counted(row.places, PLACES)} shown={shown} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function ShowOnPage({ row, what, shown }: { row: MismatchRow; what: string; shown: () => void }) {
  const editor = useEditor();
  const isShown = useEditorState((state) => state.shownMismatches.has(row.key));
  return (
    <EyeToggle
      label={`Show ${what} on the page`}
      isSelected={isShown}
      onChange={(selected) => {
        toggleShownMismatches(editor, row);
        if (selected) shown();
      }}
    />
  );
}
