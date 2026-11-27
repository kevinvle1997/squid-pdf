import { Button } from "react-aria-components";
import type { PageInfo } from "../../api/types";
import { splitAfter } from "../export";
import { useEditor, useEditorState } from "./context";
import { sheetWidthOf } from "./geometry";
import styles from "./SplitHere.module.css";

export function SplitHere({ after, shownNumber, info }: { after: number; shownNumber: number; info: PageInfo }) {
  const editor = useEditor();
  const exporting = useEditorState((state) => state.exporting);
  return (
    <div className={styles.split} style={sheetWidthOf(info)}>
      <Button
        className={styles.button}
        aria-label={`Split after page ${shownNumber}`}
        isDisabled={exporting}
        onPress={() => void splitAfter(editor, after)}
      >
        <Scissors />
        Split here
      </Button>
    </div>
  );
}

function Scissors() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    >
      <circle cx="3.25" cy="3.5" r="2.25" />
      <circle cx="3.25" cy="10.5" r="2.25" />
      <path d="M5 9.07 13 2.5M5 4.93 13 11.5" />
    </svg>
  );
}
