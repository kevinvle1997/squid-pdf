import { Button } from "react-aria-components";
import type { PageInfo } from "../../api/types";
import { PX_PER_PT } from "../constants";
import { splitAfter } from "../export";
import { useEditor, useEditorState } from "./context";
import { shownSize } from "./geometry";
import styles from "./SplitHere.module.css";

/** Between two pages: download the document as two files, split here. Under the sheet of `info`, the page above. */
export function SplitHere({ after, info }: { after: number; info: PageInfo }) {
  const editor = useEditor();
  const exporting = useEditorState((state) => state.exporting);
  const [wide] = shownSize(info);
  return (
    <div className={styles.split} style={{ "--sheet-width": `${wide * PX_PER_PT}px` }}>
      <Button
        className={styles.button}
        aria-label={`Split after page ${after + 1}`}
        isDisabled={exporting}
        onPress={() => void splitAfter(editor, after)}
      >
        Split here
      </Button>
    </div>
  );
}
