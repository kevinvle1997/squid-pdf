// The bar's warned count, pressed: where an edit won't match, grouped by why (quality.md, Rule 1).
import { type ReactNode, useMemo } from "react";
import { Dialog, DialogTrigger, Heading, Popover } from "react-aria-components";
import { Button } from "../../ui/Button";
import { EyeToggle } from "../../ui/EyeToggle";
import { BELOW_BAR_PX } from "../constants";
import { changedUnlike } from "../editor";
import { toggleShown } from "../shown";
import { shallowEqual } from "../store";
import { type Unlike, type UnlikeRow, unlikeOf } from "../unlike";
import { counted } from "../words";
import { useEditor, useEditorState } from "./context";
import styles from "./Unlike.module.css";

const PLACES = { one: "place", other: "places" };

/** The count, as a button that opens its places grouped: the whole document's before any change, else the changes'. */
export function UnlikeCount({ changed, children }: { changed: boolean; children: ReactNode }) {
  return (
    <DialogTrigger>
      <Button variant="quiet">{children}</Button>
      <Popover placement="bottom end" offset={BELOW_BAR_PX} className={styles.popover}>
        <Dialog aria-label="Where edits won't match" className={styles.dialog}>
          {changed ? <ChangedGroups /> : <AtRestGroups />}
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

/** Every place in the document, worked out once as it opened. */
function AtRestGroups() {
  const unlike = useEditorState((state) => state.layout.unlikeAtRest);
  return <Groups unlike={unlike} />;
}

/** The changed places, grouped while the list is open. */
function ChangedGroups() {
  const { reading, drawn, fonts, copy } = useEditorState(
    (state) => ({ reading: state.reading, drawn: state.drawn, fonts: state.layout.fonts, copy: state.doc.copy }),
    shallowEqual,
  );
  const unlike = useMemo(
    () => unlikeOf(changedUnlike({ reading, drawn }), { fonts, copy }),
    [reading, drawn, fonts, copy],
  );
  return <Groups unlike={unlike} />;
}

/** The fonts a similar one draws for, then the reasons the file's own won't match, each row with its places. */
function Groups({ unlike }: { unlike: Unlike }) {
  return (
    <>
      {unlike.fonts.length > 0 && (
        <section className={styles.group}>
          <Heading level={2} className={styles.heading}>
            In a similar font
          </Heading>
          <ul className={styles.list}>
            {unlike.fonts.map((row) => (
              <li key={row.key} className={styles.row}>
                <span className={styles.name}>{row.name}</span>
                <span className={styles.places}>{counted(row.places, PLACES)}</span>
                <ShowOnPage row={row} what={`the places in ${row.name}`} />
                <span className={styles.said}>{row.said}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {unlike.reasons.length > 0 && (
        <section className={styles.group}>
          <Heading level={2} className={styles.heading}>
            Will look different
          </Heading>
          <ul className={styles.list}>
            {unlike.reasons.map((row) => (
              <li key={row.key} className={styles.row}>
                <span className={styles.said}>{row.said}</span>
                <span className={styles.places}>{counted(row.places, PLACES)}</span>
                <ShowOnPage row={row} what={counted(row.places, PLACES)} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

/** The row's eye: shows its places on the page, bringing the first into view, until it's pressed again. */
function ShowOnPage({ row, what }: { row: UnlikeRow; what: string }) {
  const editor = useEditor();
  const shown = useEditorState((state) => state.shownUnlike.has(row.key));
  return <EyeToggle label={`Show ${what} on the page`} isSelected={shown} onChange={() => toggleShown(editor, row)} />;
}
