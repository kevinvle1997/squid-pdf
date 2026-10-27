import { useContext, useId } from "react";
import { Group } from "react-aria-components";
import { createPortal } from "react-dom";
import type { PageInfo, Sentences, SpanInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Warn } from "../../ui/Warn";
import { onEscape } from "../commands";
import { editAnyway, keepRedaction } from "../redaction";
import { SlipLayerContext, useEditor } from "./context";
import { useSlipPlace } from "./slipPlace";
import styles from "./UndoRedaction.module.css";

/**
 * Asked before a redacted span is edited, beside it, never a modal: the one confirmation the
 * editor has, since un-redacting by accident is a leak. Focus starts on keeping it; Escape keeps it too.
 * On the page's layer for slips, as the field's chip is: near the page's foot it hangs past it, over the next page.
 */
export function UndoRedaction({ span, info, sentences }: { span: SpanInfo; info: PageInfo; sentences: Sentences }) {
  const editor = useEditor();
  const asked = useId();
  const keepOnEscape = onEscape(() => keepRedaction(editor));
  const slipLayer = useContext(SlipLayerContext);
  // Placed as the field's chip is: whole on the page and off the line, however the page is turned.
  const { slipRef, style } = useSlipPlace(span.bbox, { page: info });
  if (slipLayer === null) return null;
  return createPortal(
    <Group ref={slipRef} className={styles.prompt} aria-labelledby={asked} style={style}>
      <span id={asked} className={styles.asked}>
        <Warn mark>{sentences.undo_redaction}</Warn>
      </span>
      <span className={styles.choices}>
        <Button onPress={() => editAnyway(editor)} onKeyDown={keepOnEscape}>
          Edit anyway
        </Button>
        <Button variant="primary" autoFocus onPress={() => keepRedaction(editor)} onKeyDown={keepOnEscape}>
          Keep redaction
        </Button>
      </span>
    </Group>,
    slipLayer,
  );
}
