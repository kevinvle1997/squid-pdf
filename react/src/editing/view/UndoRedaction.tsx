import { useContext, useId } from "react";
import { Group } from "react-aria-components";
import { createPortal } from "react-dom";
import type { PageInfo, SpanInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Warn } from "../../ui/Warn";
import { onEscape } from "../commands";
import { editAnyway, keepRedaction } from "../redaction";
import { SlipLayerContext, useEditor } from "./context";
import { keepFocusOnPress } from "./keys";
import { useSlipPlace } from "./slipPlace";
import styles from "./UndoRedaction.module.css";

/** Never a modal: the editor's one confirmation, since un-redacting by accident is a leak. */
export function UndoRedaction({ span, info, question }: { span: SpanInfo; info: PageInfo; question: string }) {
  const editor = useEditor();
  const asked = useId();
  const keepOnEscape = onEscape(() => keepRedaction(editor));
  const slipLayer = useContext(SlipLayerContext);
  const { slipRef, style } = useSlipPlace(span.bbox, { page: info });
  if (slipLayer === null) return null;
  return createPortal(
    <Group ref={slipRef} className={styles.prompt} aria-labelledby={asked} style={style} onMouseDown={keepFocusOnPress}>
      <span id={asked} className={styles.asked}>
        <Warn mark>{question}</Warn>
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
