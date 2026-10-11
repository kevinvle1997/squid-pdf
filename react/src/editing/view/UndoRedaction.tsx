import { useId } from "react";
import { Group } from "react-aria-components";
import type { Copy, PageInfo, SpanInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Warn } from "../../ui/Warn";
import { editAnyway, keepRedaction } from "../redaction";
import { useEditor } from "./context";
import { boxOf, points } from "./geometry";
import styles from "./UndoRedaction.module.css";

/**
 * Asked before a redacted span is edited, beside it, never a modal: the one confirmation the
 * editor has, since un-redacting by accident is a leak. Focus starts on keeping it; Escape keeps it too.
 */
export function UndoRedaction({ span, info, copy }: { span: SpanInfo; info: PageInfo; copy: Copy }) {
  const editor = useEditor();
  const asked = useId();
  const keepOnEscape = (event: { key: string; continuePropagation: () => void }) => {
    if (event.key === "Escape") keepRedaction(editor);
    else event.continuePropagation();
  };
  return (
    <Group
      className={styles.prompt}
      aria-labelledby={asked}
      style={{ left: boxOf(span.bbox, info).left, top: points(span.bbox.y1 + 4, info) }}
    >
      <span id={asked} className={styles.asked}>
        <Warn mark>{copy.undo_redaction}</Warn>
      </span>
      <span className={styles.choices}>
        <Button onPress={() => editAnyway(editor)} onKeyDown={keepOnEscape}>
          Edit anyway
        </Button>
        <Button autoFocus onPress={() => keepRedaction(editor)} onKeyDown={keepOnEscape}>
          Keep redaction
        </Button>
      </span>
    </Group>
  );
}
